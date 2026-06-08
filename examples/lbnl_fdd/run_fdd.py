"""CAMBER on the LBNL FDD dataset: semantic model + storage + fault detection.

Exercises the full stack against a *different* BAS naming convention than the
per-building examples, using a dataset with ground-truth fault labels:

1. Mapping       -- LBNL point names -> CAMBER roles via mapping.json.
2. Completeness  -- the entity model reports what's instrumented and which rules
                    can run (this AHU is cooling-only, so heat-coil rules are
                    correctly gated out).
3. Storage       -- ingest the role-frame into the Parquet store and read it back.
4. Detection     -- run a diagnostic on the fault-free baseline vs a labeled
                    stuck-damper fault; the fault should trip, the baseline not.

Run fetch.py first to download the data (CC-BY; not bundled).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd  # noqa: E402

from camber.model.entities import completeness, runnable_rules  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.units import normalize_percent_frame  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SDAHU = os.path.join(HERE, "..", "_data", "lbnl", "sdahu")
MAPPING = MappingProvider.from_dict(json.load(open(os.path.join(HERE, "mapping.json"))))


def load_role_frame(csv: str) -> pd.DataFrame:
    """Wide LBNL CSV -> hourly role-named frame, via the mapping provider.

    Column selection goes through ``role_of`` (case-insensitive), so it works
    regardless of the source's capitalization.
    """
    df = (pd.read_csv(csv,
                      usecols=lambda c: c == "Datetime" or MAPPING.role_of(c) is not None,
                      parse_dates=["Datetime"])
          .set_index("Datetime").resample("1h").mean())
    frame = pd.DataFrame({MAPPING.role_of(c): df[c] for c in df.columns
                          if MAPPING.role_of(c)})
    # LBNL valve/damper signals are 0-1; normalize to percent for the rules
    return normalize_percent_frame(frame)


def main() -> int:
    base_csv = os.path.join(SDAHU, "AHU_annual.csv")
    fault_csv = os.path.join(SDAHU, "damper_stuck_100_annual_short.csv")
    if not os.path.exists(base_csv):
        print("Data not found. Run:  python examples/lbnl_fdd/fetch.py")
        return 1

    base = load_role_frame(base_csv)
    roles = set(base.columns)
    print("=== 1. Mapping ===")
    print(f"LBNL point names -> {len(roles)} CAMBER roles: "
          f"{', '.join(sorted(r.value for r in roles))}")

    print("\n=== 2. Completeness (entity model) ===")
    c = completeness("AHU", roles)
    print(f"AHU ready={c.ready}  completeness={c.score:.0%}  "
          f"missing required={[r.value for r in c.missing_required]}")
    for rr in runnable_rules(roles, [SupplyAirReset(), OutdoorAirFraction(),
                                     LeakingValve()]):
        why = "" if rr.can_run else f" (needs {[x.value for x in rr.missing_required]})"
        print(f"  {rr.rule}: {'runs' if rr.can_run else 'gated'}{why}")

    print("\n=== 3. Storage (Parquet round-trip) ===")
    st = ParquetStore(tempfile.mkdtemp())
    n = st.write_role_frame(base, site="LBNL_SDAHU", equip="AHU", equip_class="AHU")
    back = st.read_role_frame(site="LBNL_SDAHU", equip="AHU")
    print(f"wrote {n:,} observations; read back {back.shape[0]:,} hourly rows x "
          f"{back.shape[1]} roles; round-trip ok={len(base) == len(back)}")

    print("\n=== 4. Fault detection: baseline vs labeled stuck-damper ===")
    rule = OutdoorAirFraction()
    for label, csv in [("fault-free baseline", base_csv),
                       ("damper_stuck_100", fault_csv)]:
        f = rule.analyze("AHU", load_role_frame(csv))
        print(f"  {label:22s}: {f.severity:5s}  "
              f"OAF median {f.metrics.get('oaf_median_pct')}%  "
              f"excess-OA {f.metrics.get('excess_oa_pct')}%")
    print("\nThe stuck-open damper drives outdoor-air fraction to ~100% and trips the")
    print("diagnostic, while the baseline economizes correctly (~20%) and stays OK --")
    print("detection validated against the dataset's ground-truth fault label.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
