"""Resolve equipment data into role-named frames.

This is the seam that lets diagnostics ask for *meaning* instead of filenames.
Given a data source (a per-point folder today; an ingest adapter later), a
:class:`~camber.model.mapping.MappingProvider`, and an equipment reference, it
returns a DataFrame whose columns are :class:`~camber.model.roles.Role` values.

A rule says ``frame[Role.HEAT_VALVE]`` and never sees ``HWValve`` / ``HHW_Valve``
/ whatever this BAS called it. Equipment discovery and occupancy filtering also
live here, so they are defined once rather than re-globbed and re-coded per rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from glob import glob
import os

import pandas as pd

from . import realio
from .model.mapping import MappingProvider
from .model.roles import Role, STATUS_ROLES
from .units import normalize_percent_frame
from .schedules import occupied_mask


@dataclass(frozen=True)
class EquipRef:
    """A discovered piece of equipment: where its data is and how it's named.

    Some equipment's points are split across more than one source token (e.g. a
    boiler's run status under ``HotWaterPlant_B1`` but its loop temps under
    ``HotWaterPlant``). ``extra_equips`` lists those sibling tokens; resolve()
    searches the primary ``equip`` first, then each extra, so one role-frame can
    span them. The primary token wins if a role is found in more than one.
    """

    equip: str        # primary equipment token incl. id, e.g. "VAV_117" / "AHU_1"
    equip_class: str  # e.g. "VAV", "AHU"
    folder: str       # primary source folder holding its per-point CSVs
    extra_equips: tuple = ()   # sibling tokens holding related points
    extra_folders: tuple = ()  # additional folders to search (multi-folder sources)

    def all_equips(self) -> tuple:
        """Primary token then any extras, in search order."""
        return (self.equip,) + tuple(self.extra_equips)

    def all_folders(self) -> tuple:
        """Primary folder then any extras, in search order."""
        return (self.folder,) + tuple(self.extra_folders)


# Terminal-unit (zone-level air terminal) equipment classes. A "VAV-only" sweep
# misses constant-volume and fan-powered constant-volume boxes, which serve real
# zones too -- a VAV-only sweep can silently miss a real fraction of the zones.
# Keep this in step with the terminal prefixes in :mod:`camber.inventory`.
TERMINAL_CLASSES: tuple = ("VAV", "CAV", "FCAV")


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
    primary: dict = {}     # equip -> folder where its marker was first found
    for fd in folders:
        for p in sorted(glob(os.path.join(fd, f"{equip_class}_*{suffix}"))):
            equip = os.path.basename(p)[: -len(suffix)]
            primary.setdefault(equip, fd)
    out = []
    for equip in sorted(primary):
        marker_fd = primary[equip]
        extras = tuple(f for f in folders if f != marker_fd)
        out.append(EquipRef(equip=equip, equip_class=equip_class,
                            folder=marker_fd, extra_folders=extras))
    return out


def discover_terminals(folder: str, marker_measure: str = "SpaceTemp",
                       classes: tuple = TERMINAL_CLASSES):
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


def _candidate_tokens(folder: str, equip: str):
    """Raw measure tokens present for ``equip`` (the parts after ``<equip>_``)."""
    toks = []
    prefix = f"{equip}_"
    for p in glob(os.path.join(folder, f"{prefix}*.csv")):
        toks.append(os.path.basename(p)[len(prefix):-4])
    return toks


def resolve(equip_ref: EquipRef, mapping: MappingProvider, roles, *,
            resample: str = "1h") -> pd.DataFrame:
    """Load the requested ``roles`` for one equipment into a role-named frame.

    Only roles whose tokens exist for this equipment appear as columns (callers
    request a superset freely). Columns are :class:`Role` enum members. Roles in
    :data:`STATUS_ROLES` (text/event status & command points) are loaded via
    ``load_status`` (text -> 0/1 step series); the rest via the numeric loader.

    ``equip_ref`` may carry ``extra_tokens`` (see :class:`EquipRef`) to pull in
    points that live under a *different* equipment token in the same folder -- e.g.
    a plant whose status sits on ``HotWaterPlant_B1`` but whose temps sit on
    ``HotWaterPlant``.
    """
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
                    cols[role] = realio.load_status(path, name=role, resample=resample)
                else:
                    s = realio.load_point(path, name=role)
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
    return occupied_mask(frame.index, start_hour=start_hour, end_hour=end_hour,
                         occ=occ_series, warmup=warm, cooldown=cool)
