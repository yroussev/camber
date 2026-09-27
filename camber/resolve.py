"""Resolve equipment data into role-named frames.

This is the seam that lets diagnostics ask for *meaning* instead of filenames.
Given a data source, a :class:`~camber.model.mapping.MappingProvider`, and an equipment
reference, it returns a DataFrame whose columns are :class:`~camber.model.roles.Role` values.

Two sources sit behind the one :func:`resolve` call:

* a **per-point CSV folder** (:class:`EquipRef`, found by :func:`discover` /
  :func:`discover_terminals`) -- raw vendor tokens translated to roles by the mapping, and
* a **Parquet store** (:class:`StoreEquipRef`, found by :func:`discover_store`) -- data already
  normalized to roles at ingest (e.g. by ``camber datasets ingest``), so the mapping is not
  consulted. Store reads are cached per equipment and invalidated when the facility's partition
  directory changes (or :meth:`camber.store.ParquetStore.drop_facility` runs).

Every analysis path (``Registry.run`` / ``run_periods`` / ``run_fleet``, config-driven SOO and
drift) loads through :func:`resolve`, so a store-backed ref works everywhere a folder one does.

A rule says ``frame[Role.HEAT_VALVE]`` and never sees ``HWValve`` / ``HHW_Valve``
/ whatever this BAS called it. Equipment discovery and occupancy filtering also
live here, so they are defined once rather than re-globbed and re-coded per rule.
"""

from __future__ import annotations

import os
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from glob import glob

import pandas as pd

from . import realio
from .model.mapping import MappingProvider
from .model.roles import STATUS_ROLES, Role
from .schedules import occupied_mask
from .units import normalize_percent_frame

__all__ = [
    "EquipRef",
    "StoreEquipRef",
    "TERMINAL_CLASSES",
    "discover",
    "discover_terminals",
    "discover_store",
    "clear_store_cache",
    "resolve",
    "occupied",
]


@dataclass(frozen=True)
class EquipRef:
    """A discovered piece of equipment: where its data is and how it's named.

    Some equipment's points are split across more than one source token (e.g. a
    boiler's run status under ``HotWaterPlant_B1`` but its loop temps under
    ``HotWaterPlant``). ``extra_equips`` lists those sibling tokens; resolve()
    searches the primary ``equip`` first, then each extra, so one role-frame can
    span them. The primary token wins if a role is found in more than one.
    """

    equip: str  # primary equipment token incl. id, e.g. "VAV_117" / "AHU_1"
    equip_class: str  # e.g. "VAV", "AHU"
    folder: str  # primary source folder holding its per-point CSVs
    extra_equips: tuple = ()  # sibling tokens holding related points
    extra_folders: tuple = ()  # additional folders to search (multi-folder sources)
    # The site's IANA zone (provisional, 0.90.1; config ``source.timezone``): offset-bearing / epoch
    # stamps in its files are converted to that wall clock. None keeps the clock as written.
    timezone: str | None = None
    strict_timezone: bool = False  # refuse offset-bearing stamps when no timezone is set

    def all_equips(self) -> tuple:
        """Primary token then any extras, in search order."""
        return (self.equip,) + tuple(self.extra_equips)

    def all_folders(self) -> tuple:
        """Primary folder then any extras, in search order."""
        return (self.folder,) + tuple(self.extra_folders)


@dataclass(frozen=True)
class StoreEquipRef:
    """One piece of equipment held in a :class:`~camber.store.ParquetStore`.

    The store already holds role-named series (written at ingest), so no mapping is involved:
    :func:`resolve` reads ``equip`` of ``facility_id`` from the store at ``store_root``, optionally
    bounded to ``[start, end]`` (inclusive, any pandas-parseable timestamp). ``equip_class`` is the
    class recorded at write time. Found by :func:`discover_store`.
    """

    equip: str
    equip_class: str
    facility_id: str
    store_root: str
    start: str | None = None
    end: str | None = None

    def all_equips(self) -> tuple:
        """The one equipment token (parity with :meth:`EquipRef.all_equips`)."""
        return (self.equip,)


# Terminal-unit (zone-level air terminal) equipment classes. A "VAV-only" sweep
# misses constant-volume and fan-powered constant-volume boxes, which serve real
# zones too -- a VAV-only sweep can silently miss a real fraction of the zones.
# Keep this in step with the terminal prefixes in :mod:`camber.inventory`.
TERMINAL_CLASSES: tuple = ("VAV", "CAV", "FCAV")


# Status roles resampled "on if on at any moment of the bin" rather than by duty: prep-mode flags
# that *exclude* samples, where a bin partly in warm-up/cool-down should be excluded whole.
_ANY_ON_ROLES = frozenset({Role.WARMUP, Role.COOLDOWN})


def _as_folders(folder) -> list:
    """Normalize a folder argument (str or iterable of str) to a list of folders."""
    if isinstance(folder, (str, os.PathLike)):
        return [os.fspath(folder)]
    return [os.fspath(f) for f in folder]


def discover(folder, equip_class: str, marker_measure: str = "SpaceTemp"):
    """Find equipment of ``equip_class`` via a marker measure file.

    Replaces ad-hoc per-script filename globbing. ``marker_measure`` is a measure
    every instance of the class has (default ``SpaceTemp`` for terminal boxes;
    pass e.g. ``CHW_Valve`` for AHUs).

    ``folder`` may be a single folder (str) or an iterable of folders -- a
    multi-folder source (e.g. one export split across batches). Each equipment is
    discovered once (the first folder whose marker file matches is its primary
    folder); the remaining source folders become ``extra_folders`` so
    :func:`resolve` still finds points that live in a sibling folder. Equipment is
    returned sorted by token, so a multi-folder sweep is order-stable.
    """
    folders = _as_folders(folder)
    suffix = f"_{marker_measure}.csv"
    primary: dict = {}  # equip -> folder where its marker was first found
    for fd in folders:
        for p in sorted(glob(os.path.join(fd, f"{equip_class}_*{suffix}"))):
            equip = os.path.basename(p)[: -len(suffix)]
            primary.setdefault(equip, fd)
    out = []
    for equip in sorted(primary):
        marker_fd = primary[equip]
        extras = tuple(f for f in folders if f != marker_fd)
        out.append(
            EquipRef(equip=equip, equip_class=equip_class, folder=marker_fd, extra_folders=extras)
        )
    return out


def discover_terminals(
    folder, marker_measure: str = "SpaceTemp", classes: tuple = TERMINAL_CLASSES
):
    """Discover ALL terminal-unit zones (VAV + CAV + FCAV) in ``folder``.

    The union helper for zone-level analyses: ``discover("VAV", ...)`` alone misses
    constant-volume (CAV) and fan-powered constant-volume (FCAV) boxes, which serve
    zones too. Returns one :class:`EquipRef` per terminal, de-duplicated by equip
    token and sorted, so a zone census never silently drops a box class. The glob
    prefixes are distinct (``VAV_`` / ``CAV_`` / ``FCAV_``), so no token is matched
    by more than one class; the de-dup is a belt-and-braces guard.
    """
    seen, out = set(), []
    for cls in classes:
        for ref in discover(folder, cls, marker_measure=marker_measure):
            if ref.equip not in seen:
                seen.add(ref.equip)
                out.append(ref)
    return sorted(out, key=lambda r: r.equip)


def _store_of(store):
    """A :class:`~camber.store.ParquetStore` from a store object or a root path."""
    from .store import ParquetStore

    if isinstance(store, (str, os.PathLike)):
        return ParquetStore(os.fspath(store))
    return store


def discover_store(
    store,
    facility_id: str,
    equip_class: str | None = None,
    *,
    marker_role=None,
    classes=None,
    start=None,
    end=None,
    include_inactive: bool = False,
) -> list:
    """Find stored equipment of a class in one facility of a Parquet store.

    ``store`` is a :class:`~camber.store.ParquetStore` or its root path. Equipment is matched on
    the ``equip_class`` recorded at ingest (never inferred from the name): pass ``equip_class``
    for one class, ``classes`` for several (``equip_class="TERMINAL"`` means
    :data:`TERMINAL_CLASSES`), or neither for every piece of equipment. ``marker_role`` (a
    :class:`Role` or slug) keeps only equipment that has that role stored -- the store analogue of
    :func:`discover`'s marker file. ``start``/``end`` bound every returned ref's reads. Returns
    :class:`StoreEquipRef` s sorted by equip.

    A facility whose lifecycle state is not ``active`` (suspended, provisioning, offboarding,
    archived -- see :mod:`camber.portfolio`) is skipped with a ``UserWarning`` and yields ``[]``;
    pass ``include_inactive=True`` to analyse it anyway.
    """
    st = _store_of(store)
    if not include_inactive and not _facility_is_active(st, facility_id):
        return []
    if classes is None and equip_class is not None:
        classes = TERMINAL_CLASSES if equip_class == "TERMINAL" else (equip_class,)
    wanted = None if classes is None else set(classes)
    eqs = st.equipment(facility_id=facility_id).get(facility_id, {})
    if marker_role is not None:
        slug = marker_role.value if isinstance(marker_role, Role) else str(marker_role)
        having = {k.equip for k in st.points(facility_id=facility_id) if k.role == slug}
    else:
        having = None
    out = []
    for equip in sorted(eqs):
        cls = eqs[equip]
        if wanted is not None and cls not in wanted:
            continue
        if having is not None and equip not in having:
            continue
        out.append(
            StoreEquipRef(
                equip=equip,
                equip_class=cls,
                facility_id=facility_id,
                store_root=os.path.abspath(st.root),
                start=None if start is None else str(start),
                end=None if end is None else str(end),
            )
        )
    return out


def _facility_is_active(store, facility_id: str, *, warn: bool = True) -> bool:
    """True if ``facility_id``'s lifecycle state is ``active`` (unregistered counts as active).

    Otherwise emits a ``UserWarning`` naming the state (unless ``warn=False``) and returns False --
    the check store-backed discovery and config runs use to skip suspended facilities.
    """
    state = _store_of(store).facility_state(facility_id)
    if state == "active":
        return True
    if warn:
        warnings.warn(
            f"facility {facility_id!r} is {state}; skipping it (analyses run on active facilities "
            'only -- pass include_inactive=True, or set "include_inactive": true in the config '
            "source, to analyse it anyway)",
            UserWarning,
            stacklevel=3,
        )
    return False


# Per-equipment cache of the full stored role frame (stored grid, every role). Keyed on the ref's
# identity; each entry carries the facility partition's modification signature, so a write or a
# drop invalidates it without the store having to know about the cache.
_STORE_CACHE_MAX = 64
_STORE_CACHE: OrderedDict = OrderedDict()
_STORE_LOCK = threading.Lock()


def _facility_signature(store_root: str, facility_id: str) -> tuple:
    """mtimes of the facility partition dir and its year dirs (changes on any write/drop)."""
    fdir = os.path.join(store_root, f"facility_id={facility_id}")
    try:
        sig = [os.stat(fdir).st_mtime_ns]
        for d in sorted(os.listdir(fdir)):
            p = os.path.join(fdir, d)
            sig.append(os.stat(p).st_mtime_ns)
            sig.append(len(os.listdir(p)) if os.path.isdir(p) else 0)
        return tuple(sig)
    except OSError:
        return ()


def clear_store_cache(store_root=None, facility_id: str | None = None) -> int:
    """Drop cached store frames (all, one store's, or one facility's); returns entries removed.

    Called by :meth:`camber.store.ParquetStore.drop_facility`; callers rarely need it directly
    (the cache already invalidates itself when a facility's partition changes).
    """
    root = None if store_root is None else os.path.abspath(os.fspath(store_root))
    with _STORE_LOCK:
        doomed = [
            k
            for k in _STORE_CACHE
            if (root is None or k[0] == root) and (facility_id is None or k[1] == facility_id)
        ]
        for k in doomed:
            del _STORE_CACHE[k]
    return len(doomed)


def _store_frame(ref: StoreEquipRef) -> pd.DataFrame:
    """The full stored role frame of ``ref`` (every role, stored grid), via the cache."""
    root = os.path.abspath(ref.store_root)
    key = (root, ref.facility_id, ref.equip, ref.start, ref.end)
    sig = _facility_signature(root, ref.facility_id)
    with _STORE_LOCK:
        hit = _STORE_CACHE.get(key)
        if hit is not None and hit[0] == sig:
            _STORE_CACHE.move_to_end(key)
            return hit[1]
    frame = _store_of(root).read_role_frame(
        facility_id=ref.facility_id, equip=ref.equip, start=ref.start, end=ref.end
    )
    with _STORE_LOCK:
        _STORE_CACHE[key] = (sig, frame)
        _STORE_CACHE.move_to_end(key)
        while len(_STORE_CACHE) > _STORE_CACHE_MAX:
            _STORE_CACHE.popitem(last=False)
    return frame


def _resolve_store(ref: StoreEquipRef, roles, resample: str | None) -> pd.DataFrame:
    """Role subset of a stored equipment, resampled like the folder path.

    Status roles are stored as per-bin duty (0..1), so a coarser resample takes their **mean**
    (duty-preserving); the WARMUP/COOLDOWN exclusion flags take the **max** ("on at any moment of
    the bin"); every other role takes the mean.
    """
    full = _store_frame(ref)
    if full.empty:
        return pd.DataFrame()
    want = [r for r in dict.fromkeys(roles) if r in full.columns]
    if not want:
        return pd.DataFrame()
    frame = full[want]
    if resample:
        cols = {}
        for role in want:
            s = frame[role].resample(resample)
            cols[role] = s.max() if role in _ANY_ON_ROLES else s.mean()
        frame = pd.concat(cols, axis=1)
    else:
        frame = frame.copy()
    out = normalize_percent_frame(frame)
    if full.attrs:
        out.attrs.update(full.attrs)  # dataset provenance (camber._provenance)
    return out


def _candidate_tokens(folder: str, equip: str):
    """Raw measure tokens present for ``equip`` (the parts after ``<equip>_``)."""
    toks = []
    prefix = f"{equip}_"
    for p in glob(os.path.join(folder, f"{prefix}*.csv")):
        toks.append(os.path.basename(p)[len(prefix) : -4])
    return toks


def resolve(
    equip_ref: EquipRef | StoreEquipRef,
    mapping: MappingProvider | None,
    roles,
    *,
    resample: str = "1h",
) -> pd.DataFrame:
    """Load the requested ``roles`` for one equipment into a role-named frame.

    Only roles whose tokens exist for this equipment appear as columns (callers
    request a superset freely). Columns are :class:`Role` enum members. Roles in
    :data:`STATUS_ROLES` (text/event status & command points) are loaded via
    ``load_status`` (text -> 0/1 step series, resampled to the time-weighted *duty* of each bin
    so runtime verdicts don't depend on the resample interval; the WARMUP/COOLDOWN exclusion
    flags keep "on at any moment of the bin"); the rest via the numeric loader.

    ``equip_ref`` may carry ``extra_tokens`` (see :class:`EquipRef`) to pull in
    points that live under a *different* equipment token in the same folder -- e.g.
    a plant whose status sits on ``HotWaterPlant_B1`` but whose temps sit on
    ``HotWaterPlant``.

    A :class:`StoreEquipRef` is read from its Parquet store instead (roles were fixed at ingest,
    so ``mapping`` is ignored and may be None); see :func:`discover_store`.
    """
    if isinstance(equip_ref, StoreEquipRef):
        return _resolve_store(equip_ref, roles, resample)
    if mapping is None:
        raise ValueError("a folder-backed EquipRef needs a MappingProvider")
    tz, strict_tz = equip_ref.timezone, equip_ref.strict_timezone
    cols = {}
    for full_equip in equip_ref.all_equips():
        for folder in equip_ref.all_folders():
            present = mapping.roles_present(_candidate_tokens(folder, full_equip))
            for role in roles:
                if role in cols or role not in present:
                    continue
                tok = present[role]
                path = realio.find_point(folder, full_equip, tok)
                if not path:
                    continue
                if role in STATUS_ROLES:
                    # duty-preserving (time-weighted) bins, except the prep-mode *exclusion*
                    # flags, where a bin touched by warm-up/cool-down at all is excluded
                    how = "any" if role in _ANY_ON_ROLES else "duty"
                    cols[role] = realio.load_status(
                        path,
                        name=role,
                        resample=resample,
                        how=how,
                        timezone=tz,
                        strict_timezone=strict_tz,
                    )
                else:
                    s = realio.load_point(path, name=role, timezone=tz, strict_timezone=strict_tz)
                    cols[role] = s.resample(resample).mean() if resample else s
    if not cols:
        return pd.DataFrame()
    # normalize valve/damper/speed columns to percent (no-op on 0-100 sources)
    return normalize_percent_frame(pd.concat(cols, axis=1))


def occupied(frame: pd.DataFrame, *, start_hour: int = 7, end_hour: int = 18):
    """Occupied-hours mask for a role-named frame, using the single shared filter.

    Uses the OCCUPANCY role if present, else a weekday daytime window, minus
    WARMUP/COOLDOWN prep modes when those roles are present.
    """
    occ_series = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
    warm = frame[Role.WARMUP] if Role.WARMUP in frame.columns else None
    cool = frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None
    return occupied_mask(
        frame.index,
        start_hour=start_hour,
        end_hour=end_hour,
        occ=occ_series,
        warmup=warm,
        cooldown=cool,
    )
