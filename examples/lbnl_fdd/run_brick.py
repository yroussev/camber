"""Brick interop: derive the role mapping from the LBNL Brick model, then analyze.

Instead of hand-writing mapping.json, parse the building's Brick (.ttl) model and
let CAMBER derive point -> role automatically (cooling vs heating valve, OA damper,
supply fan, etc. resolved from the equipment relationships). Then prove the derived
mapping drives the pipeline: completeness validation + a diagnostic, identical to
the hand-mapped path.

Run fetch.py first (it now also downloads the Brick model).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd  # noqa: E402

from camber.interop.brick import mapping_from_brick, roles_from_brick  # noqa: E402
from camber.model.entities import completeness  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from camber.units import normalize_percent_frame  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LBNL = os.path.join(HERE, "..", "_data", "lbnl")
TTL = os.path.join(LBNL, "ttl", "LBNL_FDD_Data_Sets_SDAHU_ttl.ttl")
BASELINE = os.path.join(LBNL, "sdahu", "AHU_annual.csv")


def main() -> int:
    if not os.path.exists(TTL):
        print("Brick model not found. Run:  python examples/lbnl_fdd/fetch.py")
        return 1

    ttl = open(TTL).read()
    derived = roles_from_brick(ttl)
    print(f"=== Roles derived from the Brick model ({len(derived)}) ===")
    for name, role in sorted(derived.items()):
        print(f"  {name:14s} -> {role.value}")

    # compare coverage to the hand-written mapping.json
    hand = json.load(open(os.path.join(HERE, "mapping.json")))["aliases"]
    hand_pts = {k.upper() for k in hand}
    auto_pts = {k.upper() for k in derived}
    print(f"\nhand-written mapping.json covers {len(hand_pts)} points; the Brick model "
          f"auto-derives {len(auto_pts)} (no hand mapping needed).")
    extra = sorted(auto_pts - hand_pts)
    if extra:
        print(f"  additionally derived from Brick: {', '.join(extra)}")

    if not os.path.exists(BASELINE):
        print("\n(CSV data absent -- skipping the analysis step.)")
        return 0

    # drive the pipeline straight from the Brick-derived mapping
    mp = mapping_from_brick(ttl)
    df = (pd.read_csv(BASELINE,
                      usecols=lambda c: c == "Datetime" or mp.role_of(c) is not None,
                      parse_dates=["Datetime"]).set_index("Datetime").resample("1h").mean())
    frame = normalize_percent_frame(
        pd.DataFrame({mp.role_of(c): df[c] for c in df.columns if mp.role_of(c)}))
    roles = set(frame.columns)
    c = completeness("AHU", roles)
    print(f"\n=== Pipeline on the Brick-derived role-frame ===")
    print(f"AHU completeness {c.score:.0%} (ready={c.ready}); "
          f"{len(roles)} roles resolved with zero hand mapping.")
    f = OutdoorAirFraction().analyze("AHU", frame)
    print(f"OA-fraction diagnostic: {f.severity} "
          f"(median OAF {f.metrics.get('oaf_median_pct')}%)")
    print("\nA Brick-tagged building is analyzed with no hand-written mapping --"
          "\nthe ontology supplies the roles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
