"""Data-free demo: run a CAMBER FDD diagnostic on generated trends.

No external data needed. Builds a role-named frame with a deliberate simultaneous
heating/cooling fault (a heating valve left cracked open overnight while the
cooling valve runs in a hot-desert summer) and runs the rule on it -- showing the
role-frame -> rule -> Finding flow the rest of the toolkit is built on.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from camber.model.roles import Role  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402


def main() -> int:
    idx = pd.date_range("2025-07-01", periods=24 * 14, freq="1h")
    rng = np.random.default_rng(0)
    hour = idx.hour + idx.minute / 60.0

    # Hot-desert summer: OAT swings, cooling valve modulates open most of the day.
    oat = 92 + 14 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1.0, len(idx))
    cool = np.clip(55 + 25 * np.sin((hour - 9) / 24 * 2 * np.pi)
                   + rng.normal(0, 4, len(idx)), 0, 100)
    # Controls fault: a leaking reheat valve sits open through the occupied
    # afternoon (weekdays) while the cooling valve is also driving -- classic
    # simultaneous heating/cooling.
    weekday = idx.dayofweek < 5
    heat = np.where(weekday & (idx.hour >= 11) & (idx.hour < 16), 35.0, 0.0)

    frame = pd.DataFrame(
        {Role.OAT: oat, Role.COOL_VALVE: cool, Role.HEAT_VALVE: heat}, index=idx)

    finding = SimultaneousHeatCool().analyze("AHU_DEMO", frame)
    print("CAMBER synthetic demo -- simultaneous heating/cooling diagnostic")
    print(f"  equip:    {finding.equip}")
    print(f"  severity: {finding.severity}")
    for k, v in finding.metrics.items():
        print(f"  {k}: {v}")
    print("\nRole-named frame in, structured Finding out -- the same path every")
    print("CAMBER rule uses, on any building once its tags are mapped to roles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
