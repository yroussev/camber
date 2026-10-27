"""Rule: hot-water plant loop low-deltaT (PNNL Ch.8 — heating-side overpumping).

The heating-loop analogue of the chilled-water low-deltaT rule: a hot-water loop
running at low delta-T (HWS - HWR) over boiler-running hours is moving a lot of water
for little heat transfer -- oversized/constant-speed pumps, three-way valves dumping
flow, or coils bypassing. It wastes pump energy and flattens the supply-temp reset.
Adapts :func:`camber.plant.analyze_hw_plant` to the role-frame interface; the design
loop delta-T is system-specific, so it is a constructor parameter.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..plant import analyze_hw_plant
from ..schedules import occupied_mask
from ._boilerrun import BOILER_RUN_ANY_OF, GAS_RUN_CAVEAT, with_boiler_status
from .base import Finding

_ROLE_TO_COL = {
    Role.BOILER_STATUS: "BoilerStatus",
    Role.HW_SUPPLY_TEMP: "HWS_Temp",
    Role.HW_RETURN_TEMP: "HWR_Temp",
    Role.OAT: "OAT",
}


class HWPlantDeltaT:
    """Detects low hot-water loop delta-T at the heating plant (PNNL Re-tuning Ch.8)."""

    name = "hw_plant_deltat"
    # 0.98 (#86 item 4a): a run status OR the gas input it can be inferred from (_boilerrun.py)
    roles_required = (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP)
    roles_any_of = BOILER_RUN_ANY_OF
    roles_optional = (Role.OAT,)

    def __init__(self, design_deltaT_min_f: float = 20.0):
        # System-specific: confirm against the HW loop design delta-T.
        self.design_deltaT_min_f = design_deltaT_min_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        frame, run_source = with_boiler_status(frame)  # 0.98 (#86 item 4a): gas fallback
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_hw_plant(legacy, equip, design_deltaT_min_f=self.design_deltaT_min_f)
        if res is None or res.deltaT_median_f != res.deltaT_median_f:  # None / NaN dT
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient running data for loop delta-T",
            )
        low = res.low_deltaT_pct
        if low >= 50.0:
            severity = "fault"
        elif low >= 20.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "deltaT_median_f": res.deltaT_median_f,
                "design_deltaT_min_f": res.design_deltaT_min_f,
                "low_deltaT_pct": res.low_deltaT_pct,
                "n_running": res.n_running,
                **({"run_source": run_source} if run_source else {}),  # 0.98 (#86 item 4a)
            },
            summary=(
                f"{equip}: HW loop deltaT median {res.deltaT_median_f:.1f}F "
                f"({res.low_deltaT_pct:.0f}% of running hours < "
                f"{res.design_deltaT_min_f:.0f}F design)"
            ),
            caveats=[GAS_RUN_CAVEAT] if run_source else [],  # 0.98 (#86 item 4a)
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J (0.111, #298): the loop temperatures with the occupied running samples whose
        delta-T is under the design minimum (the samples behind ``low_deltaT_pct``) shaded."""
        from ..charts.evidence import Evidence

        frame, _src = with_boiler_status(frame)
        need = (Role.BOILER_STATUS, Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP)
        if any(r not in frame.columns for r in need):
            return None
        # as analyze_hw_plant reads it: occupied, firing for most of the sample, a physical dT
        occ = pd.Series(occupied_mask(frame.index), index=frame.index).astype(bool)
        duty = pd.to_numeric(frame[Role.BOILER_STATUS], errors="coerce").clip(0.0, 1.0)
        dt = pd.to_numeric(frame[Role.HW_SUPPLY_TEMP], errors="coerce") - pd.to_numeric(
            frame[Role.HW_RETURN_TEMP], errors="coerce"
        )
        judged = (occ & (duty > 0.5) & (dt > -2) & (dt < 120)).fillna(False)  # #310: its n
        low = judged & (dt < self.design_deltaT_min_f)
        return Evidence(
            renderer="multitrend",
            roles=[r for r in (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP) if r in frame.columns],
            mask=low.fillna(False),
            judged=judged,
            judged_basis="occupied running samples",
            label=f"delta-T under {self.design_deltaT_min_f:g}°F",
            title=f"{equip}: HW loop delta-T",
        )
