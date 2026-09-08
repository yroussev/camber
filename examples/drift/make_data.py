"""Generate a small synthetic site for the ``camber drift`` walkthrough.

Writes per-point CSVs for three air handlers into ``trends/`` next to this file:

* ``AHU_1`` — a healthy baseline month followed by a current month with a **loading filter**
  injected (rising filter DP and the fan working harder against it);
* ``AHU_2`` — healthy throughout, so the run has an honest negative;
* ``AHU_3`` — carries only an airflow point, so no detector can run on it. It exists to show that
  CAMBER reports it as *not evaluated* rather than quietly calling it steady.

The frames come from :mod:`camber.ahusim`, the same physics generator the drift family's accuracy
harness uses, so this needs no download and no network.

    python examples/drift/make_data.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from camber.ahusim import simulate_case  # noqa: E402
from camber.model.roles import Role  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TRENDS = os.path.join(HERE, "trends")


def token(role) -> str:
    """The BAS-style measure token this example writes a role out as (``oat`` -> ``Oat``)."""
    value = role.value if isinstance(role, Role) else str(role)
    return "".join(part.capitalize() for part in value.split("_"))


def write_point(equip: str, measure: str, series) -> None:
    """Write one point as a two-column ``Timestamp,Value`` CSV, BAS export style."""
    stamps = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": stamps, "Value": series.values}).to_csv(
        os.path.join(TRENDS, f"{equip}_{measure}.csv"), index=False
    )


def main() -> int:
    """Write the three air handlers' trends to ``trends/``."""
    os.makedirs(TRENDS, exist_ok=True)
    for equip, case in (
        ("AHU_1", simulate_case("filter_loading", 4, seed=3)),
        ("AHU_2", simulate_case(None, 0, seed=9)),
    ):
        frame = pd.concat([case.baseline, case.current])
        for column in frame.columns:
            write_point(equip, token(column), frame[column])

    healthy = simulate_case(None, 0, seed=1)
    index = pd.concat([healthy.baseline, healthy.current]).index
    write_point("AHU_3", "Airflow", pd.Series(9000.0, index=index))

    print(f"wrote {len(os.listdir(TRENDS))} point files to {TRENDS}")
    print("next: camber drift freeze examples/drift/config.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
