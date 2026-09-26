"""Regressions for hydronic loop / pump defects found by running CAMBER on open real plant datasets.

Each test reproduces one defect on **synthetic** data shaped like what the real data showed (a DP
trended in inH2O, a 0-1 pump speed, an all-NaN power column). Nothing here is drawn from a measured
dataset; the before/after numbers on the real data are recorded in docs/VALIDATION.md.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.hwpump_rule import HWPumpDPReset  # noqa: E402
from camber.rules.loop_dp_rule import DP_PLAUSIBLE, LoopDPDrift, dp_plausible_range  # noqa: E402
from camber.rules.pump_power_rule import PumpPowerDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# =========================================================================== 5. loop DP units


def _loop(n, start, seed, scale, offset=0.0):
    rng = np.random.default_rng(seed)
    flow = rng.uniform(150, 1000, n)
    dp = (12.0 + 0.001 * (flow - 680) + offset + rng.normal(0, 0.5, n)) * scale
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame({Role.CHW_FLOW: flow, Role.CHW_DIFF_PRESS: dp}, index=idx)


@pytest.mark.parametrize("scale", [1.0, 27.68, 6.895])  # psi, inH2O, kPa
def test_loop_dp_is_unit_free(scale):
    base, cur = _loop(720, "2025-05-01", 1, scale), _loop(720, "2025-06-01", 2, scale, offset=6.0)
    f = LoopDPDrift(BaselineStore()).analyze_periods("L", base, cur)
    assert f.metrics.get("declined") is not True
    assert f.severity == "fault"
    assert f.metrics["loop_dp_drift_sigma"] == pytest.approx(12.0, rel=0.2)


def test_loop_dp_decline_names_the_real_cause():
    base = _loop(720, "2025-05-01", 1, 40.0)  # ~480 inH2O
    f = LoopDPDrift(BaselineStore(), dp_range=DP_PLAUSIBLE).analyze_periods("L", base, base)
    assert f.metrics["declined"] is True
    msg = " ".join(f.caveats)
    assert "plausible range" in msg and "units" in msg
    assert "too few loaded samples" not in msg


def test_dp_plausible_range_rejects_sentinels():
    lo, hi = dp_plausible_range(pd.Series([480.0] * 50 + [9999.0, -9999.0]))
    assert lo < 0 < 480 < hi < 9999


# =========================================================================== 8. HW pump


def test_hw_pump_rule_reads_a_fractional_speed_and_evaluates_dp_reset():
    n = 24 * 14
    idx = pd.date_range("2025-01-06", periods=n, freq="1h")
    spd = np.full(n, 0.95)  # 0-1 fraction, pinned near full
    flat = pd.DataFrame({Role.HW_PUMP_SPEED: spd, Role.HW_DIFF_PRESS_SP: 480.5}, index=idx)
    f = HWPumpDPReset().analyze("HWP", flat)
    assert f.severity == "fault"
    assert f.metrics["dp_sp_reset_present"] is False and "flat DP setpoint" in f.summary
    reset = flat.assign(**{Role.HW_DIFF_PRESS_SP: 8 + 4 * np.sin(np.arange(n) / 24)})
    assert HWPumpDPReset().analyze("HWP", reset).metrics["dp_sp_reset_present"] is True
    bare = HWPumpDPReset().analyze("HWP", flat.drop(columns=[Role.HW_DIFF_PRESS_SP]))
    assert bare.metrics["dp_sp_reset_present"] is None
    assert "not evaluated" in bare.summary and bare.caveats


# =========================================================================== 9. messages


def test_all_nan_metric_is_named_not_blamed_on_load():
    rng = np.random.default_rng(18)
    n = 720
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    flow = rng.uniform(100, 400, n)
    base = pd.DataFrame(
        {Role.HW_FLOW: flow, Role.POWER: 1 + 0.01 * flow + rng.normal(0, 0.1, n)}, index=idx
    )
    cur = base.assign(**{Role.POWER: np.nan})
    f = PumpPowerDrift(BaselineStore(), flow_role=Role.HW_FLOW).analyze_periods("P", base, cur)
    assert f.metrics["declined"] is True
    assert any("power has no numeric values" in c for c in f.caveats)
    assert not any("no loaded samples" in c for c in f.caveats)
