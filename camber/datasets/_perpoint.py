"""The ``per_point`` ingest adapter: one time series file per sensor, plus a point index.

Some datasets publish every sensor as its own file (``data/<sensor>.parquet``: a timestamp and a
value) beside an index table naming each sensor's group (a building) and class (a point type),
with no equipment topology at all. The adapter turns that into:

* one **facility per group** (``<ingest.facility>-<group>``, e.g. ``ds-at-30bldg-sensors-b01``);
* one **equipment per sensor** (the sensor id), whose CAMBER class and single role come from the
  entry's ``class_map`` (``{"TeVentSu": ["SENSOR_TEVENT", "supply_air_temp"]}``); sensor classes
  the map leaves out are not ingested (the entry says why);
* the subset's ``groups`` (``"all"`` or a list) choose the facilities.

The ingest spec (validated by :mod:`._catalog`)::

    "adapter": "per_point",
    "index": {"file": "data.zip", "member": "data/index.csv",
              "id": "sensor_id", "group": "building", "class": "sensor_class"},
    "series": {"file": "data.zip", "member": "data/series/{id}.parquet",
               "timestamp": "datetime", "value": "value"},
    "class_map": {...}, "units": {"supply_air_temp": "degC"},
    "resample": "1h", "hold": "8h5min"

**Sampling.** A change-of-value log records a value only when it changes (plus a periodic
heartbeat), so a plain bin mean leaves most bins empty. With ``hold`` the adapter treats the log
as sample-and-hold: each bin is the mean of the samples inside it, and a bin with none holds the
last sample seen, if it is at most ``hold`` old (a longer silence stays a gap). Without ``hold``
bins are plain means. Each series file is read by :func:`._readers.read_point`, the reader of
every one-point file (``.parquet`` or ``.csv``): duplicate timestamps keep their first value, as
every CAMBER table read does, and the count is recorded per facility.
"""

from __future__ import annotations

import re

import pandas as pd

from ..model.roles import Role
from ..store import ParquetStore
from ..units import normalize_percent_frame
from ._readers import read_point
from ._units import convert_frame, plausibility_warnings

_SAFE = re.compile(r"[^a-z0-9-]+")

__all__ = ["hold_resample", "facility_id", "ingest_per_point"]


def facility_id(prefix: str, group) -> str:
    """The facility of one group (``ds-x`` + ``B01`` -> ``ds-x-b01``)."""
    return f"{prefix}-{_SAFE.sub('-', str(group).lower()).strip('-')}"


def hold_resample(s: pd.Series, rule: str, hold: str | None = None) -> pd.Series:
    """``s`` on a regular ``rule`` grid: bin means, empty bins holding the last sample (see module).

    A held value is carried only while the last sample is at most ``hold`` old at the bin's
    start; ``hold=None`` gives plain bin means (empty bins stay NaN).
    """
    s = s.dropna().sort_index()
    if s.empty:
        return s
    binned = s.resample(rule).mean()
    if not hold:
        return binned
    last_val = s.resample(rule).last().ffill()
    stamps = pd.Series(s.index, index=s.index)
    last_t = stamps.resample(rule).last().ffill().shift(1)  # latest sample before each bin
    prev_val = last_val.shift(1)
    age = binned.index.to_series() - last_t
    fill = binned.isna() & (age <= pd.Timedelta(hold))
    return binned.where(~fill, prev_val)


def ingest_per_point(entry, subset, inputs, root, staging, progress, extract) -> tuple:
    """Write each chosen group's sensors into ``staging``; returns the adapter tuple.

    ``extract(members)`` takes ``[{"file", "member"}, ...]`` and returns ``{(file, member):
    path}`` (archive members extracted into the cache; plain files resolve to their download).
    """
    spec = entry.ingest
    pts, ser = spec["index"], spec["series"]
    cmap = spec.get("class_map") or {}
    units = spec.get("units") or {}
    rule = spec.get("resample", "15min")
    hold = spec.get("hold")
    idx_path = extract([{"file": pts["file"], "member": pts.get("member")}])[
        (pts["file"], pts.get("member"))
    ]
    table = pd.read_csv(idx_path, dtype=str)
    wanted = entry.subset(subset).get("groups", "all")
    table = table[table[pts["class"]].isin(list(cmap))]
    if wanted != "all":
        table = table[table[pts["group"]].isin(list(wanted))]
    members = [
        {"file": ser["file"], "member": ser["member"].format(id=i)} for i in table[pts["id"]]
    ]
    paths = extract(members)
    st = ParquetStore(staging)
    out: dict = {}
    warns: list = []
    groups = sorted(table[pts["group"]].unique())
    for gi, group in enumerate(groups, 1):
        fid = facility_id(spec["facility"], group)
        rows_g = table[table[pts["group"]] == group]
        rows = n_eq = dups = 0
        classes: dict = {}
        if progress:
            progress(f"{entry.id}: {group} ({gi}/{len(groups)}, {len(rows_g)} sensors)")
        for sid, scls in zip(rows_g[pts["id"]], rows_g[pts["class"]]):
            eq_cls, role_slug = cmap[scls]
            s, dup = read_point(
                paths[(ser["file"], ser["member"].format(id=sid))],
                {"timestamp": ser["timestamp"]},
                value=ser["value"],
            )
            dups += dup
            role = Role(role_slug)
            frame = pd.DataFrame({role: hold_resample(s, rule, hold)})
            frame = normalize_percent_frame(convert_frame(frame, units))
            if frame.dropna(how="all").empty:
                warns.append(f"{fid}/{sid}: no samples")
                continue
            rows += st.write_role_frame(frame, facility_id=fid, equip=sid, equip_class=eq_cls)
            n_eq += 1
            classes[scls] = classes.get(scls, 0) + 1
            warns += plausibility_warnings(frame, label=f"{fid}/{sid}")
        extra = {
            "group": str(group),
            "sensor_classes": dict(sorted(classes.items())),
            "duplicate_stamps_dropped": dups,
            "resample": rule,
            "hold": hold,
        }
        out[fid] = (f"{entry.title} -- {group}", rows, n_eq, extra)
    return out, "", warns


def check_spec(did: str, ing: dict, file_names: dict, subsets: dict, errs: list) -> None:
    """Validation of a ``per_point`` ingest spec (called by the catalog validator)."""
    if "points" in ing:
        errs.append(f"{did}: ingest.points was renamed ingest.index (the sensor index table)")
    for key in ("index", "series"):
        blk = ing.get(key)
        if not isinstance(blk, dict) or blk.get("file") not in file_names:
            errs.append(f"{did}: ingest.{key}.file must name one of the entry's files")
            continue
        need = ("id", "group", "class") if key == "index" else ("member", "timestamp", "value")
        for k in need:
            if not (isinstance(blk.get(k), str) and blk[k]):
                errs.append(f"{did}: ingest.{key}.{k} must be a non-empty string")
        if blk.get("member") and file_names[blk["file"]].get("archive") is None:
            errs.append(f"{did}: ingest.{key}.member only applies to an archive file")
    member = str((ing.get("series") or {}).get("member") or "")
    if "{id}" not in member:
        errs.append(f"{did}: ingest.series.member must contain '{{id}}'")
    cmap = ing.get("class_map")
    if not isinstance(cmap, dict) or not cmap:
        errs.append(f"{did}: ingest.class_map must map source classes to [class, role]")
    else:
        roles = {r.value for r in Role}
        for k, v in cmap.items():
            if not (isinstance(v, list) and len(v) == 2 and v[0] and v[1] in roles):
                errs.append(f"{did}: class_map[{k!r}] must be [CAMBER class, known role]")
    hold = ing.get("hold")
    if hold is not None:
        try:
            pd.Timedelta(hold)
        except (TypeError, ValueError):
            errs.append(f"{did}: ingest.hold must be a duration (e.g. '8h5min')")
    for sname, sub in subsets.items():
        g = sub.get("groups", "all")
        if g != "all" and not (isinstance(g, list) and g and all(isinstance(x, str) for x in g)):
            errs.append(f"{did}/{sname}: groups must be 'all' or a list of group names")
