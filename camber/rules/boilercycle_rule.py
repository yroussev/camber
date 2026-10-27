"""Rule: boiler short-cycling (firing starts per day; PNNL Ch.8 / boiler guidance).

Flags a boiler firing in short bursts -- too many starts per day -- which wastes
purge-cycle heat, stresses the boiler, and lowers seasonal efficiency; usually an
oversized boiler or too-tight staging hysteresis. Adapts
:func:`camber.boilercycle.analyze_boiler_cycling` to the role-frame interface.
``max_starts_per_day`` is manufacturer/min-cycle-time dependent, so it is a
constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..boilercycle import _coverage_caveat, _resolution_check, analyze_boiler_cycling
from ..model.roles import Role
from ._boilerrun import BOILER_RUN_ANY_OF, GAS_RUN_CAVEAT, with_boiler_status
from .base import Finding

_ROLE_TO_COL = {Role.BOILER_STATUS: "BoilerStatus"}


class BoilerShortCycle:
    """Detects a boiler short-cycling (excess firing starts per day) (PNNL Re-tuning Ch.8)."""

    name = "boiler_short_cycle"
    # 0.98 (#86 item 4a): a run status OR the gas input it can be inferred from (_boilerrun.py)
    roles_required = ()
    roles_any_of = BOILER_RUN_ANY_OF
    roles_optional = ()

    def __init__(self, max_starts_per_day: float = 6.0, max_interval_min: float = 15.0):
        # Manufacturer/min-cycle-time dependent; confirm against the boiler's controls.
        self.max_starts_per_day = max_starts_per_day
        # 0.104 (#143): the coarsest trend interval whose count can vouch for "ok"; coarser, a
        # count below the limit with part-on samples declines (camber.boilercycle)
        self.max_interval_min = max_interval_min

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        frame, run_source = with_boiler_status(frame)  # 0.98 (#86 item 4a): gas fallback
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_boiler_cycling(legacy, equip)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need boiler status)",
            )
        cyc = res.starts_per_day
        if cyc >= 2 * self.max_starts_per_day:
            severity = "fault"
        elif cyc >= self.max_starts_per_day:
            severity = "warn"
        else:
            severity = "ok"
        # 0.104 (#143): a coarse trend's count is a floor; below the limit with part-on samples
        # the interval hid starts, so the verdict declines rather than reading "ok"
        severity, res_caveats, declined = _resolution_check(
            res,
            severity,
            max_starts_per_day=self.max_starts_per_day,
            max_interval_min=self.max_interval_min,
        )
        if run_source:  # the gas caveat already says sub-interval firing is invisible
            res_caveats = res_caveats if declined else []
        summary = (
            f"{equip}: {res.starts_per_day:.1f} boiler starts/day "
            f"(threshold {self.max_starts_per_day:.0f}), firing "
            f"{res.runtime_pct:.0f}% of the time"
        )
        if declined:
            summary += f"; cannot confirm on a {res.interval_min:g}-minute trend"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "starts_per_day": res.starts_per_day,
                "max_starts_per_day": self.max_starts_per_day,
                "runtime_pct": res.runtime_pct,
                "n_starts": res.n_starts,
                "n_days": res.n_days,
                "n_days_observed": res.n_days_observed,  # 0.108 (#237)
                "interval_min": res.interval_min,  # 0.104 (#143)
                "n_partial": res.n_partial,  # 0.104 (#143)
                **({"run_source": run_source} if run_source else {}),  # 0.98 (#86 item 4a)
            },
            summary=summary,
            caveats=([GAS_RUN_CAVEAT] if run_source else [])
            + res_caveats
            + _coverage_caveat(res, "boiler"),  # 0.108 (#237)
        )

    def _legacy(self, frame: pd.DataFrame) -> pd.DataFrame:
        status_frame, _src = with_boiler_status(frame)
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in status_frame.columns}
        return status_frame.rename(columns=cols)

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The firing samples on days with at least ``max_starts_per_day`` starts (0.111, #298;
        0.102 marked the starts themselves, now :meth:`start_mask`), read from the same run
        status the rule counts (the gas-input fallback included)."""
        from ..boilercycle import _short_cycle_firing

        m = _short_cycle_firing(self._legacy(frame), max_starts_per_day=self.max_starts_per_day)
        return m.reindex(frame.index, fill_value=False)

    def start_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The firing starts on days with at least ``max_starts_per_day`` of them (0.102, #119)."""
        from ..boilercycle import _short_cycle_samples

        m = _short_cycle_samples(self._legacy(frame), max_starts_per_day=self.max_starts_per_day)
        return m.reindex(frame.index, fill_value=False)

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the boiler's firing trend, the firing on short-cycling days shaded (#119,
        #298), with the starts on those days as the counted events.

        The trend is the run status, or -- when firing is read from the gas input -- the gas
        input itself, the data the rule examined."""
        from ..charts.evidence import Evidence

        status_frame, run_source = with_boiler_status(frame)
        if Role.BOILER_STATUS not in status_frame.columns:
            return None
        role = Role.GAS_INPUT_RATE if run_source else Role.BOILER_STATUS
        return Evidence(
            renderer="multitrend",
            roles=[role],
            mask=self.violation_mask(frame),
            events=self.start_mask(frame),
            # 0.111 (#310): the rule reads every status sample (runtime_pct is their mean duty)
            judged=pd.to_numeric(status_frame[Role.BOILER_STATUS], errors="coerce").notna(),
            judged_basis="boiler-status samples",
            label=f"firing, on a day with >= {self.max_starts_per_day:g} starts",
            title=f"{equip}: boiler short-cycling",
        )
