"""Inventory per-point trend CSVs.

A per-point BAS export is one CSV per point, named ``<EQUIP>_<ID>_<MEASURE>.csv``
(e.g. ``VAV_117_HWValve.csv``, ``CHW_SYS_CHWP3_DiffPress.csv``). This module
parses those filenames into (equip_type, equip_id, measure), records the unit
from each file's header, and builds a manifest so we know what diagnostics are
possible before running any.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, asdict
from glob import glob

# Equipment-type prefixes seen in the export. Order matters: longer/multiword
# prefixes (CHW_SYS, HW_SYS) must be tried before bare CHW/HW.
EQUIP_PREFIXES = [
    "CHW_SYS", "HHW_SYS", "HW_SYS", "CW_SYS", "CHW_BTU_METER", "HHW_BTU_METER",
    "AHU", "RTU", "VAV", "CAV", "FCAV", "FCU", "EF", "SF", "RF",
    "CHWP", "CHWP_", "CHW", "HHW", "HW", "CW", "Chiller", "Boiler", "CT",
]


@dataclass
class PointFile:
    """One trend-log CSV: its path and parsed equipment/measure/unit metadata."""

    path: str
    fname: str
    equip_type: str
    equip_id: str
    measure: str
    unit: str
    n_rows: int


def parse_name(fname: str):
    """Split ``<EQUIP>_<ID>_<MEASURE>.csv`` -> (equip_type, equip_id, measure).

    Strategy: strip ``.csv``; match the longest known equipment prefix; the
    next ``_``-delimited token is the id; the remainder is the measure. Falls
    back to a generic 3-part split when no known prefix matches.
    """
    stem = fname[:-4] if fname.lower().endswith(".csv") else fname
    for pre in sorted(EQUIP_PREFIXES, key=len, reverse=True):
        if stem == pre:
            return pre, "", ""
        if stem.startswith(pre + "_"):
            rest = stem[len(pre) + 1 :]
            parts = rest.split("_", 1)
            if len(parts) == 2:
                equip_id, measure = parts
            else:
                equip_id, measure = "", parts[0]
            return pre, equip_id, measure
    # generic fallback: TYPE_ID_MEASURE
    parts = stem.split("_")
    if len(parts) >= 3:
        return parts[0], parts[1], "_".join(parts[2:])
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return stem, "", ""


def _unit_and_rows(path: str):
    """Read the header unit (from ``Value (<unit>)``) and count data rows cheaply."""
    unit = ""
    n = 0
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            first = f.readline()
            m = re.search(r"\(([^)]*)\)", first)
            if m:
                unit = m.group(1).strip()
            for _ in f:
                n += 1
    except OSError:
        pass
    return unit, n


def inventory(folders, count_rows: bool = True):
    """Return a list of :class:`PointFile` for every CSV in ``folders``."""
    out = []
    for folder in folders:
        for path in sorted(glob(os.path.join(folder, "*.csv"))):
            fname = os.path.basename(path)
            etype, eid, meas = parse_name(fname)
            unit, n = _unit_and_rows(path) if count_rows else ("", -1)
            out.append(PointFile(path, fname, etype, eid, meas, unit, n))
    return out


def to_rows(points):
    """Convert a list of PointFile into a list of plain dict rows."""
    return [asdict(p) for p in points]


if __name__ == "__main__":
    import json
    import sys

    folders = sys.argv[1:] or ["."]
    pts = inventory(folders, count_rows=False)
    by_type = {}
    by_meas = {}
    for p in pts:
        by_type[p.equip_type] = by_type.get(p.equip_type, 0) + 1
        by_meas[p.measure] = by_meas.get(p.measure, 0) + 1
    print(f"total points: {len(pts)}")
    print("\nby equipment type:")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")
    print("\nby measure (top 30):")
    for k, v in sorted(by_meas.items(), key=lambda x: -x[1])[:30]:
        print(f"  {v:4d}  {k}")
