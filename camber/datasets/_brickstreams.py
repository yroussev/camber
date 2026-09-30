"""The ``brick_streams`` ingest adapter: per-site Brick models whose points name their series.

Some datasets publish each site as a Brick model, a point index and an archive holding one file
per series. The model links every point to its series through a literal (``senaps:stream_id
"<uuid>"``), the index lists each stream's Brick class, and the series files are named by a
counter, not by the stream they hold (BTS, the Building TimeSeries dataset behind the Brick by
Brick 2024 challenge). The adapter turns that into:

* one **facility per site** (``<ingest.facility>-<site>``, e.g. ``ds-bts-b``); the subset's
  ``groups`` (``"all"`` or a list of site keys) choose the sites;
* a **role per point** from the Brick class, by :func:`camber.interop.brick.brick_mapping_report`
  (mapped and alias classes; ambiguous and unmapped points are counted, never guessed);
* **equipment from the model**: the point's ``isPointOf`` owner, or the first entity up the
  ``hasPart`` / ``isPartOf`` chain whose Brick class ``equip_classes`` maps to a CAMBER class
  (:func:`._brickgroup.grouping_from_brick`); a point with no such owner goes to the site
  equipment ``site_equip`` (``["site", "SITE"]`` by default);
* one role per equipment: a second point with the same role on the same owner (an air handler's
  second supply-air sensor, the site's many unowned zone temperatures) becomes its own equipment
  ``<equip>-2``, ``-3``, ... of the same class, in archive order, and is counted
  (``duplicate_roles_split``) -- nothing is averaged and no point is dropped.

The ingest spec (validated by :func:`check_spec`)::

    "adapter": "brick_streams",
    "sites": {"B": {"model": "Site_B.ttl", "index": "Site_B_metadata.csv",
                    "series": "Site_Baa.zip", "source_timezone": "UTC",
                    "local_timezone": "Australia/Sydney"}},
    "index": {"id": "StreamID", "class": "Brick"},
    "stream_predicate": "stream_id",
    "series_format": "bts_pickle",
    "equip_classes": {"Air_Handler_Unit": "AHU", ...},
    "units": {"supply_air_temp": "degC"}, "resample": "15min", "hold": "1h"

The Brick model is read with the core (``minimal``) Turtle reader whatever extras are installed,
so the plan does not depend on whether ``rdflib`` is present.

**Series files.** ``bts_pickle`` members are Python pickles of ``[name, timestamps, values]``
(``name`` = ``Site_<X>_<stream id>.pickle``, a ``datetime64`` and a float array). A pickle can
run arbitrary code when loaded, so :func:`load_series_pickle` uses a restricted unpickler that
resolves only the four numpy globals an array needs (``ndarray``, ``dtype``, ``_reconstruct``,
``_frombuffer``) and refuses everything else; the arrays must be a datetime and a real numeric
array of one length. The stream id sits at the start of each pickle, so the adapter reads only a
member's first bytes (:func:`peek_stream_id`) to decide whether it needs the file at all, and
reads archive members straight from the verified zip (nothing is extracted to disk).

**Corrections.** The entry's quirks run on each stream's raw samples, as a one-column frame named
by its role, with the site key as the run name (``"runs": ["C"]`` selects a site); ``fix``
quirks are skipped with ``corrections=False`` like every adapter's.

**Sampling.** Samples arrive every few minutes, irregularly, and some points log only on change;
series are resampled to ``resample`` sample-and-hold (:func:`._perpoint.hold_resample`: an empty
bin holds the last sample while it is at most ``hold`` old). Timestamps are instants in each
site's ``source_timezone`` (default UTC), moved to its wall clock (``local_timezone``).
"""

from __future__ import annotations

import io
import pickle
import re
import zipfile
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..store import ParquetStore
from ..units import normalize_percent_frame
from ._perpoint import facility_id, hold_resample
from ._quirks import apply_quirks
from ._readers import to_local_clock
from ._units import convert_frame, plausibility_warnings

__all__ = [
    "SERIES_FORMATS",
    "StreamPoint",
    "load_series_pickle",
    "peek_stream_id",
    "site_points",
    "stream_series",
    "iter_site_series",
    "ingest_brick_streams",
    "check_spec",
]

#: Series-file formats the adapter reads.
SERIES_FORMATS = ("bts_pickle",)

_UUID = re.compile(rb"([0-9a-f]{8}(?:_[0-9a-f]{4}){3}_[0-9a-f]{12})")
_UUID_TEXT = re.compile(r"([0-9a-f]{8}(?:_[0-9a-f]{4}){3}_[0-9a-f]{12})")
_PEEK_BYTES = 512


# --------------------------------------------------------------------------- safe pickle reading


def _numpy_globals() -> dict:
    """The only globals a pickled numpy array needs, under both numpy 1 and numpy 2 names."""
    out = {("numpy", "ndarray"): np.ndarray, ("numpy", "dtype"): np.dtype}
    for mod in ("numpy.core.multiarray", "numpy._core.multiarray"):
        out[(mod, "_reconstruct")] = _resolve("_reconstruct", "multiarray")
    for mod in ("numpy.core.numeric", "numpy._core.numeric"):
        out[(mod, "_frombuffer")] = _resolve("_frombuffer", "numeric")
    return out


def _resolve(name: str, sub: str):
    import importlib

    for mod in (f"numpy._core.{sub}", f"numpy.core.{sub}"):
        try:
            return getattr(importlib.import_module(mod), name)
        except (ImportError, AttributeError):
            continue
    raise ImportError(f"numpy has no {sub}.{name}")  # pragma: no cover - every numpy has one


class _ArrayUnpickler(pickle.Unpickler):
    """Resolves the numpy array globals and nothing else (no code runs while loading)."""

    _allowed: dict = {}

    def find_class(self, module, name):
        if not self._allowed:
            type(self)._allowed = _numpy_globals()
        fn = self._allowed.get((module, name))
        if fn is None:
            raise pickle.UnpicklingError(
                f"refusing to load {module}.{name}: a series file may hold numpy arrays only"
            )
        return fn


def load_series_pickle(data: bytes) -> tuple:
    """``(name, timestamps, values)`` from a ``bts_pickle`` file's bytes, loaded safely.

    ``ValueError`` when the content is not ``[str, datetime64 array, numeric array]`` of one
    length; ``pickle.UnpicklingError`` when it references anything but a numpy array.
    """
    obj = _ArrayUnpickler(io.BytesIO(data)).load()
    if not (isinstance(obj, (list, tuple)) and len(obj) == 3 and isinstance(obj[0], str)):
        raise ValueError("a bts_pickle series is [name, timestamps, values]")
    name, t, v = obj
    if not (isinstance(t, np.ndarray) and isinstance(v, np.ndarray)):
        raise ValueError(f"{name}: timestamps and values must be numpy arrays")
    if t.dtype.kind != "M" or v.dtype.kind not in "fiub" or t.shape != v.shape or t.ndim != 1:
        raise ValueError(
            f"{name}: expected 1-D datetime64 timestamps and numeric values of one length, got "
            f"{t.dtype}{t.shape} and {v.dtype}{v.shape}"
        )
    return name, t, v.astype(float)


def peek_stream_id(head: bytes) -> str | None:
    """The stream id (a UUID written with underscores) in a series file's first bytes."""
    m = _UUID.search(head)
    return m.group(1).decode("ascii") if m else None


def _series(t: np.ndarray, v: np.ndarray) -> tuple:
    """``(series on a sorted UTC-naive index, duplicate stamps dropped)``; NaT/NaN rows dropped."""
    s = pd.Series(v, index=pd.DatetimeIndex(t))
    s = s[s.index.notna()]
    s = s.sort_index(kind="stable")
    dup = s.index.duplicated(keep="first")
    return s[~dup].dropna(), int(dup.sum())


# --------------------------------------------------------------------------- the site model


@dataclass(frozen=True)
class StreamPoint:
    """One stream of a site: its Brick point and class, and where it lands in CAMBER."""

    stream_id: str
    point: str
    brick_class: str
    role: Role | None
    status: str  # mapped | alias | ambiguous | unmapped | not_a_point
    equip: str = ""
    equip_class: str = ""
    owner_class: str = ""


def _stream_links(ttl: str, predicate: str) -> dict:
    """``{point local name: [stream id, ...]}`` from the model's stream-id literals."""
    from ..interop.brick import _predicate_links

    links = _predicate_links(ttl, predicate, "minimal")
    return {p: [s.strip().strip('"') for s in ids] for p, ids in links.items()}


def site_points(ttl: str, index: pd.DataFrame, spec: dict) -> dict:
    """``{stream id: StreamPoint}`` for every stream of the site's ``index`` table.

    The role comes from the Brick class (``brick_mapping_report``), the equipment from the owner
    chain (``grouping_from_brick``); a stream whose point the model does not type as a point keeps
    the index's class with status ``not_a_point``. A point with several stream-id literals is
    matched through the one the index lists.
    """
    from ..interop.brick import _parse, brick_mapping_report
    from ._brickgroup import grouping_from_brick

    ix = spec.get("index") or {}
    id_col, cls_col = ix.get("id", "StreamID"), ix.get("class", "Brick")
    listed = dict(zip(index[id_col].astype(str), index[cls_col].astype(str)))
    report = {p.point: p for p in brick_mapping_report(ttl, backend="minimal").points}
    grouping = grouping_from_brick(ttl, dict(spec.get("equip_classes") or {}), backend="minimal")
    types = _parse(ttl, "minimal")[0]
    site_equip, site_class = spec.get("site_equip") or ["site", "SITE"]
    short: dict = {}
    out: dict = {}
    for point, ids in _stream_links(ttl, spec.get("stream_predicate", "stream_id")).items():
        for sid in ids:
            if sid not in listed:
                continue
            rep = report.get(point)
            if rep is None:
                out[sid] = StreamPoint(
                    sid, point, types.get(point, listed[sid]), None, "not_a_point"
                )
                continue
            g = grouping.points.get(point.lower())
            equip, eq_cls = (site_equip, site_class)
            if g is not None and g.source != "fallback":
                equip, eq_cls = _short_equip(g.equip, g.equip_class, short), g.equip_class
            out[sid] = StreamPoint(
                sid,
                point,
                rep.brick_class,
                rep.role,
                rep.status,
                equip if rep.role is not None else "",
                eq_cls if rep.role is not None else "",
                rep.owner_class,
            )
    for sid, cls in listed.items():  # listed in the index, absent from the model
        out.setdefault(sid, StreamPoint(sid, "", cls, None, "not_in_model"))
    return out


def _short_equip(owner: str, cls: str, taken: dict) -> str:
    """A readable equipment id for an anonymised owner (``AHU_da5873de``), unique per site.

    The owner's local name is a chain of UUIDs (up to ~190 characters); the id keeps the CAMBER
    class and the start of the last UUID, lengthened only if two owners would collide.
    """
    if owner in taken:
        return taken[owner]
    tail = re.sub(r"[^0-9A-Za-z]", "", owner.split(".")[-1]) or "x"
    used = set(taken.values())
    for n in (8, 12, len(tail)):
        cand = f"{cls}_{tail[:n]}"
        if cand not in used:
            break
    else:  # pragma: no cover - identical tails under one class
        cand = f"{cls}_{tail}_{len(used)}"
    taken[owner] = cand
    return cand


def stream_series(
    t: np.ndarray,
    v: np.ndarray,
    spec: dict,
    tz: str | None,
    source: str = "UTC",
    *,
    role: Role | None = None,
    site: str | None = None,
    corrections: bool = True,
    notes: list | None = None,
) -> tuple:
    """Raw arrays -> ``(series resampled on the site's wall clock, duplicate stamps dropped)``;
    ``source`` is the zone the stamps are in, ``tz`` the site's (``None``: left as published).

    The entry's quirks run on the raw samples first, as a one-column frame named by the ``role``
    slug with the ``site`` key as the run name (a quirk's ``runs`` selects sites); ``fix`` quirks
    are skipped with ``corrections=False``. Quirk notes are appended to ``notes``.
    """
    s, dups = _series(t, v)
    quirks = spec.get("quirks") or []
    if quirks and role is not None and not s.empty:
        col = role.value
        fixed, qn = apply_quirks(s.to_frame(col), quirks, run=site, corrections=corrections)
        s = fixed[col].dropna() if col in fixed.columns else s.iloc[:0]
        if notes is not None:
            notes.extend(n for n in qn if n not in notes)
    if tz and not s.empty:
        clock = {"source_timezone": source or "UTC", "local_timezone": tz}
        s = to_local_clock(s.to_frame("v"), clock)["v"]
    return hold_resample(s, spec.get("resample", "15min"), spec.get("hold")), dups


def _junk(name: str) -> bool:
    """An archiver's by-product, not a series: macOS resource forks and Finder files."""
    base = name.rsplit("/", 1)[-1]
    return name.startswith("__MACOSX/") or base.startswith("._") or base == ".DS_Store"


def iter_site_series(zip_path: str, wanted, *, fmt: str = "bts_pickle"):
    """Yield ``(stream id, timestamps, values)`` for the members of ``zip_path`` whose stream id
    is in ``wanted`` (a set, or ``None`` for every member), reading each member from the archive.

    Directories and archiver by-products (``__MACOSX/``, ``._*``, ``.DS_Store``) are skipped.
    Only a member's first bytes are read to find its stream id; the rest is read (and safely
    unpickled) only when the stream is wanted; an unwanted member yields ``(id, None, None)``. A
    member whose stored name disagrees with the peeked id yields ``("!" + id, None, None)``.
    """
    if fmt not in SERIES_FORMATS:
        raise ValueError(f"unknown series format {fmt!r} (known: {SERIES_FORMATS})")
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir() or _junk(info.filename):
                continue
            with z.open(info) as fh:
                sid = peek_stream_id(fh.read(_PEEK_BYTES))
            if sid is None or (wanted is not None and sid not in wanted):
                yield sid, None, None
                continue
            name, t, v = load_series_pickle(z.read(info))
            m = _UUID_TEXT.search(name)
            if not m or m.group(1) != sid:  # pragma: no cover - never seen in published files
                yield f"!{sid}", None, None
                continue
            yield sid, t, v


# --------------------------------------------------------------------------- the adapter


def ingest_brick_streams(entry, subset, inputs, root, staging, progress, corrections=True):
    """Write each chosen site's mapped streams into ``staging``; returns the adapter tuple."""
    spec = entry.ingest
    sites = spec["sites"]
    wanted_sites = entry.subset(subset).get("groups", "all")
    keys = sorted(sites) if wanted_sites == "all" else list(wanted_sites)
    units = spec.get("units") or {}
    st = ParquetStore(staging)
    out: dict = {}
    warns: list = []
    for si, key in enumerate(keys, 1):
        site = sites[key]
        fid = facility_id(spec["facility"], key)
        if progress:
            progress(f"{entry.id}: site {key} ({si}/{len(keys)}): reading the Brick model")
        with open(inputs[site["model"]][0], encoding="utf-8") as fh:
            ttl = fh.read()
        index = pd.read_csv(inputs[site["index"]][0], dtype=str)
        points = site_points(ttl, index, spec)
        mapped = {sid: p for sid, p in points.items() if p.role is not None}
        seen_files = 0
        found: set = set()
        used: set = set()
        frames: dict = {}
        dups = split = 0
        qnotes: list = []
        for sid, t, v in iter_site_series(
            inputs[site["series"]][0], set(mapped), fmt=spec.get("series_format", "bts_pickle")
        ):
            seen_files += 1
            if sid is not None:
                found.add(sid.lstrip("!"))
            if t is None:
                continue
            p = mapped[sid]
            s, d = stream_series(
                t,
                v,
                spec,
                site.get("local_timezone"),
                site.get("source_timezone", "UTC"),
                role=p.role,
                site=key,
                corrections=corrections,
                notes=qnotes,
            )
            dups += d
            if s.dropna().empty:
                warns.append(f"{fid}/{p.equip}: stream {sid} ({p.brick_class}) has no samples")
                continue
            equip, k = p.equip, 1
            while (equip, p.role) in used:  # a second point with this role: its own equipment
                k += 1
                equip = f"{p.equip}-{k}"
            split += k > 1
            used.add((equip, p.role))
            frames.setdefault(equip, (p.equip_class, {}))[1][p.role] = s
            if progress and len(used) % 100 == 0:
                progress(f"{entry.id}: site {key}: {len(used)} mapped streams read")
        rows = 0
        for equip in sorted(frames):
            cls, cols = frames[equip]
            frame = normalize_percent_frame(convert_frame(pd.DataFrame(cols), units))
            rows += st.write_role_frame(frame, facility_id=fid, equip=equip, equip_class=cls)
            warns += plausibility_warnings(frame, label=f"{fid}/{equip}")
        status: dict = {}
        for p in points.values():
            status[p.status] = status.get(p.status, 0) + 1
        unmapped_classes: dict = {}
        for p in points.values():
            if p.role is None:
                unmapped_classes[p.brick_class] = unmapped_classes.get(p.brick_class, 0) + 1
        extra = {
            "site": key,
            "local_timezone": site.get("local_timezone", ""),
            "streams_listed": len(points),
            "series_files": seen_files,
            "streams_without_file": len(set(points) - found),
            "streams_by_status": dict(sorted(status.items())),
            "streams_mapped": len(mapped),
            "streams_ingested": len(used),
            "mapped_without_file": len(set(mapped) - found),
            "duplicate_roles_split": split,
            "duplicate_stamps_dropped": dups,
            "unmapped_classes": dict(sorted(unmapped_classes.items(), key=lambda kv: -kv[1])[:40]),
            "resample": spec.get("resample", "15min"),
            "hold": spec.get("hold"),
            "grouping": "brick",
            "quirks": qnotes,
        }
        out[fid] = (f"{entry.title} -- site {key}", rows, len(frames), extra)
    return out, "", warns


def check_spec(did: str, ing: dict, file_names: dict, subsets: dict, errs: list) -> None:
    """Validation of a ``brick_streams`` ingest spec (called by the catalog validator)."""
    sites = ing.get("sites")
    if not isinstance(sites, dict) or not sites:
        errs.append(f"{did}: ingest.sites must map site keys to their model, index and series")
        sites = {}
    for key, site in sites.items():
        where = f"{did}: ingest.sites[{key!r}]"
        if not isinstance(site, dict):
            errs.append(f"{where} must be an object")
            continue
        for k in ("model", "index", "series"):
            if site.get(k) not in file_names:
                errs.append(f"{where}.{k} must name one of the entry's files")
        ser = file_names.get(site.get("series")) or {}
        if ser and ser.get("archive") != "zip":
            errs.append(f"{where}.series must be a zip archive")
        for k in ("source_timezone", "local_timezone"):
            tz = site.get(k)
            if tz is not None:
                from ._catalog import _valid_tz

                if not _valid_tz(tz):
                    errs.append(f"{where}.{k} must be an IANA zone")
    if ing.get("series_format", "bts_pickle") not in SERIES_FORMATS:
        errs.append(f"{did}: ingest.series_format must be one of {SERIES_FORMATS}")
    ix = ing.get("index") or {}
    for k in ("id", "class"):
        if not (isinstance(ix.get(k), str) and ix[k]):
            errs.append(f"{did}: ingest.index.{k} must name the index table's column")
    ec = ing.get("equip_classes")
    if (
        not isinstance(ec, dict)
        or not ec
        or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in ec.items())
    ):
        errs.append(f"{did}: ingest.equip_classes must map Brick classes to CAMBER classes")
    se = ing.get("site_equip")
    if se is not None and not (
        isinstance(se, list) and len(se) == 2 and all(isinstance(x, str) and x for x in se)
    ):
        errs.append(f"{did}: ingest.site_equip must be [equipment id, CAMBER class]")
    hold = ing.get("hold")
    if hold is not None:
        try:
            pd.Timedelta(hold)
        except (TypeError, ValueError):
            errs.append(f"{did}: ingest.hold must be a duration (e.g. '1h')")
    for sname, sub in subsets.items():
        g = sub.get("groups", "all")
        if g == "all":
            continue
        if not (isinstance(g, list) and g and all(isinstance(x, str) for x in g)):
            errs.append(f"{did}/{sname}: groups must be 'all' or a list of site keys")
        elif any(x not in sites for x in g):
            errs.append(f"{did}/{sname}: groups {g} must be keys of ingest.sites")
        else:
            files = sub.get("files", "all")
            if files != "all":
                need = {sites[x][k] for x in g for k in ("model", "index", "series")}
                missing = sorted(need - set(files))
                if missing:
                    errs.append(f"{did}/{sname}: the subset's sites need files {missing}")
