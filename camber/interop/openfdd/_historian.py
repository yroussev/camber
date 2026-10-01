"""Read open-fdd's canonical historian Parquet layout (provisional, 0.99).

open-fdd documents (``docs/architecture/historian.md`` at the pinned commit) a Hive-partitioned
layout whose rows carry ``timestamp_utc`` and one column per canonical SQL role::

    <root>/history/building_id=<B>/equipment_id=<E>/year=<YYYY>/month=<MM>/part-*.parquet
    <root>/weather/building_id=<B>/year=<YYYY>/month=<MM>/part-*.parquet

Hidden (``.``/``_``-prefixed) files -- open-fdd's compaction tombstones and candidates -- are not
read. The layout carries no equipment types, units or site time zone: ``timezone`` and
``unit_system`` are required, and ``equip_types`` (``{equipment_id: equipType}``, e.g. from the
package the data came from) classifies the equipment; without it every equipment is unclassified.
The legacy (pre-H2) layouts are not read.
"""

from __future__ import annotations

import os

import pandas as pd
import pyarrow.dataset as pads

from ._crosswalk import Crosswalk, load_crosswalk
from ._reader import (
    UNCLASSIFIED,
    WEATHER_EQUIP,
    OpenFddEquipment,
    OpenFddPackage,
    _resample,
    _role_frame,
    _sha256,
    check_args,
    map_columns,
)


def _hidden(name: str) -> bool:
    return name.startswith((".", "_"))


def _parts(d: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = sorted(x for x in dirnames if not _hidden(x))
        out += [
            os.path.join(dirpath, f)
            for f in sorted(filenames)
            if f.endswith(".parquet") and not _hidden(f)
        ]
    return out


def _partition_value(name: str, key: str) -> str | None:
    return name[len(key) + 1 :] if name.startswith(key + "=") else None


def _read_table(files: list) -> pd.DataFrame:
    tbl = pads.dataset(files, format="parquet").to_table()
    df = tbl.to_pandas()
    return df.drop(columns=[c for c in ("building_id", "equipment_id", "year", "month") if c in df])


def _to_site(ts: pd.Series, timezone: str) -> pd.Series:
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    return t.dt.tz_convert(timezone).dt.tz_localize(None)


def read_historian(
    root,
    *,
    building: str,
    timezone: str,
    unit_system: str,
    equip_types: dict | None = None,
    resample: str | None = None,
    crosswalk: Crosswalk | None = None,
) -> OpenFddPackage:
    """Read one building of an open-fdd historian Parquet root into CAMBER roles (provisional).

    Columns are open-fdd SQL roles (``sat``, ``zone_t``, ...) and map through the same crosswalk
    as a package; see the module docstring for the layout and what must be supplied.
    """
    tz, us = check_args(timezone, unit_system)
    cw = crosswalk or load_crosswalk()
    root = os.fspath(root)
    bdir = os.path.join(root, "history", f"building_id={building}")
    if not os.path.isdir(bdir):
        raise ValueError(
            f"no open-fdd historian data for building {building!r} under {root} "
            "(expected history/building_id=<B>/equipment_id=<E>/year=<YYYY>/month=<MM>/)"
        )
    pkg = OpenFddPackage(
        building_id=building,
        source_kind="historian",
        timezone=tz,
        unit_system=us,
        crosswalk_version=cw.version,
        crosswalk_docs_commit=cw.docs_commit,
    )
    types = dict(equip_types or {})
    if not types:
        pkg.notes.append("no equip_types given: every historian equipment is unclassified")
    targets = []
    for name in sorted(os.listdir(bdir)):
        eid = _partition_value(name, "equipment_id")
        if eid is not None and not _hidden(name):
            targets.append((eid, os.path.join(bdir, name), types.get(eid)))
    wdir = os.path.join(root, "weather", f"building_id={building}")
    if os.path.isdir(wdir):
        targets.append((WEATHER_EQUIP, wdir, "weather"))
    for eid, d, stamp in targets:
        files = _parts(d)
        for f in files:
            pkg.files[os.path.relpath(f, root).replace(os.sep, "/")] = _sha256(f)
        if not files:
            pkg.warnings.append(f"{eid}: no Parquet parts")
            continue
        df = _read_table(files)
        if "timestamp_utc" not in df:
            raise ValueError(f"{eid}: historian parts have no timestamp_utc column")
        cls = cw.equip_class(stamp) or UNCLASSIFIED
        eq = OpenFddEquipment(
            equip=eid, equip_class=cls, equip_type=stamp, path=os.path.relpath(d, root)
        )
        eq.columns, quantities = map_columns(
            [c for c in df.columns if c != "timestamp_utc"],
            names={},
            csv_meta={},
            equip_type=stamp,
            unit_system=us,
            crosswalk=cw,
            source="historian",
        )
        for c in eq.columns:
            if c.source == "identity":
                c.source = "historian"
        eq.rows_read = len(df)
        idx = _to_site(df["timestamp_utc"], tz)
        good = idx.notna().to_numpy()
        eq.rows_bad_timestamp = int((~good).sum())
        raw = df.loc[good].drop(columns=["timestamp_utc"])
        raw.index = pd.DatetimeIndex(idx[good].to_numpy())
        if eq.mapped:
            frame = _role_frame(raw, eq.mapped, quantities, eid, pkg.warnings)
            eq.frame = _resample(frame.sort_index(kind="stable"), resample)
        pkg.equipment[eid] = eq
    if not pkg.equipment:
        raise ValueError(f"{building}: no historian equipment with Parquet parts")
    return pkg


def is_historian_root(path) -> bool:
    """True if ``path`` looks like an open-fdd historian root (has ``history/building_id=*``)."""
    h = os.path.join(os.fspath(path), "history")
    return os.path.isdir(h) and any(n.startswith("building_id=") for n in os.listdir(h))
