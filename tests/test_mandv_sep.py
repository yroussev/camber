"""SEP 50001 M&V Protocol arithmetic (#21 phase 21b; camber.mandv.sep).

Golden values from the SEP 2019 Guidance's worked examples that the plan on #21 reproduced
(Eagleston 7.38%, Ashton 13.53%); the Guidance's range-check numbers are deliberately NOT used --
they contain arithmetic errors. Equation numbers are the SEP 2019 Ed. 2 Protocol's.
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.methods import MethodResult  # noqa: E402
from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.sep import (  # noqa: E402
    ANNEX_B_MULTIPLIERS,
    RF_THRESHOLD,
    aggregate_energy_types,
    bottom_up_reconciliation,
    chained_senpi,
    improvement_pct,
    primary_energy,
    senpi,
    sep_range_check,
    top_down_savings,
)

# --------------------------------------------------------------------------- golden arithmetic


def test_eagleston_ratio_model_7_38_percent():
    """Guidance: a ratio model E = b x (production). 2012: 90,120 MMBtu for 25,005 units; 2015:
    91,230 MMBtu for 27,331 units. Forecast the 2012 ratio onto 2015 production (Eq 8 / Eq 5)."""
    b = 90120 / 25005
    adjusted = b * 27331  # E^a_b|r
    s = senpi(91230, adjusted)
    assert round(improvement_pct(s), 2) == 7.38
    # the Guidance's own arithmetic on the rounded ratios lands on the same figure
    assert round((3.604 - 3.338) / 3.604 * 100, 2) == 7.38
    assert top_down_savings(
        "forecast", adjusted_baseline=adjusted, observed_reporting=91230
    ) == pytest.approx(adjusted - 91230)


def test_ashton_forecast_13_53_percent():
    """Guidance: forecast 299,792 MMBtu against 259,223 actual -- 13.53% (reported as "14%")."""
    s = senpi(259223, 299792)
    assert round(improvement_pct(s), 2) == 13.53
    esp = top_down_savings("forecast", adjusted_baseline=299792, observed_reporting=259223)
    assert esp == 40569
    assert esp / 299792 == pytest.approx(1 - s)


def test_eq6_chained_senpi_is_a_product():
    o_b, a_ib, a_ir, o_r = 1000.0, 940.0, 1210.0, 1100.0
    backcast_enpi = a_ib / o_b  # baseline -> intermediate
    forecast_enpi = o_r / a_ir  # intermediate -> reporting
    assert chained_senpi(o_b, a_ib, a_ir, o_r) == pytest.approx(backcast_enpi * forecast_enpi)
    # and NOT the sum of the two improvements (EnPI V5's pre-model-year bookkeeping)
    additive = 1 - ((1 - backcast_enpi) + (1 - forecast_enpi))
    assert chained_senpi(o_b, a_ib, a_ir, o_r) != pytest.approx(additive, abs=1e-6)


def test_eq11_chained_savings_are_additive():
    o_b, a_ib, a_ir, o_r = 1000.0, 940.0, 1210.0, 1100.0
    esp = top_down_savings(
        "chaining",
        observed_baseline=o_b,
        intermediate_at_baseline=a_ib,
        intermediate_at_reporting=a_ir,
        observed_reporting=o_r,
    )
    backcast = top_down_savings("backcast", observed_baseline=o_b, adjusted_reporting=a_ib)
    forecast = top_down_savings("forecast", adjusted_baseline=a_ir, observed_reporting=o_r)
    assert esp == pytest.approx(backcast + forecast) == pytest.approx(170.0)


def test_eq9_eq10_and_term_checking():
    assert top_down_savings("backcast", observed_baseline=10, adjusted_reporting=8) == 2
    assert top_down_savings("standard_conditions", adjusted_baseline=10, adjusted_reporting=7) == 3
    with pytest.raises(ValueError, match="missing"):
        top_down_savings("forecast", adjusted_baseline=10)
    with pytest.raises(ValueError, match="unexpected"):
        top_down_savings(
            "forecast", adjusted_baseline=10, observed_reporting=9, observed_baseline=1
        )
    with pytest.raises(ValueError, match="unknown SEP method"):
        top_down_savings("sequential_chain", adjusted_baseline=1)
    with pytest.raises(ValueError, match="zero baseline"):
        senpi(1.0, 0.0)


def test_eq12_reconciliation_threshold():
    assert RF_THRESHOLD == 0.80
    at = bottom_up_reconciliation(80.0, 100.0, 12.0)  # RF exactly 0.80: not scaled
    assert at.rf == pytest.approx(0.80) and not at.scaled
    assert at.verified_improvement_pct == 12.0
    below = bottom_up_reconciliation(79.0, 100.0, 12.0)
    assert below.scaled and below.verified_improvement_pct == pytest.approx(12.0 * 0.79)
    above = bottom_up_reconciliation(150.0, 100.0, 12.0)  # never scaled up
    assert not above.scaled and above.verified_improvement_pct == 12.0
    none = bottom_up_reconciliation(50.0, -10.0, -2.0)
    assert none.rf is None and none.verified_improvement_pct == -2.0 and none.caveats
    json.dumps(below.as_dict())


# --------------------------------------------------------------------------- primary energy


def test_annex_b_rows_and_eq1():
    # rows verified against SEP 2019 Ed. 2 Annex B Tables 4A/4B
    assert ANNEX_B_MULTIPLIERS["grid_electricity"] == 3.0
    assert ANNEX_B_MULTIPLIERS["steam_fired_boiler"] == 1.33
    assert ANNEX_B_MULTIPLIERS["chilled_water_electric"] == 0.72
    assert ANNEX_B_MULTIPLIERS["chilled_water_fired_absorption_chiller"] == 1.25
    assert ANNEX_B_MULTIPLIERS["solar_electricity"] == 1.0
    assert ANNEX_B_MULTIPLIERS["natural_gas"] == 1.0
    # the Protocol's §5.1.1 example: 30 MMBtu of fired-boiler steam is 39.9 MMBtu primary
    pe = primary_energy({"steam_fired_boiler": 30.0})
    assert pe.total == pytest.approx(39.9)
    mixed = primary_energy({"grid_electricity": 100.0, "natural_gas": 50.0})
    assert mixed.total == 350.0 and mixed.by_type["grid_electricity"] == 300.0
    assert not mixed.caveats


def test_user_multipliers_negative_net_and_unknown_types():
    pe = primary_energy(
        {"grid_electricity": 100.0, "district_heat": 10.0},
        multipliers={"grid_electricity": 2.5, "district_heat": 1.2},
    )
    assert pe.total == pytest.approx(262.0)
    assert any("Verification Body" in c and "3" in c for c in pe.caveats)
    assert any("district_heat" in c for c in pe.caveats)
    with pytest.raises(ValueError, match="no primary energy multiplier"):
        primary_energy({"district_heat": 1.0})
    neg = primary_energy({"natural_gas": -5.0})
    assert neg.total == 0.0 and "§5.1.2" in neg.caveats[0]
    with pytest.raises(ValueError, match="positive"):
        primary_energy({"natural_gas": 1.0}, multipliers={"natural_gas": 0})


def _mr(method, terms, *, band=10.0, declined=False):
    return MethodResult(
        method=method,
        basis="x",
        kernel="g14",
        savings=1.0,
        projected=None,
        measured=None,
        savings_pct=None,
        fractional_uncertainty=None,
        abs_uncertainty=band,
        confidence=0.90,
        df=100,
        declined=declined,
        sep_terms=terms,
    )


def test_aggregate_energy_types_on_primary_energy():
    elec = _mr("forecast", {"adjusted_baseline": 1000.0, "observed_reporting": 900.0})
    gas = _mr("forecast", {"adjusted_baseline": 500.0, "observed_reporting": 480.0})
    fac = aggregate_energy_types({"grid_electricity": elec, "natural_gas": gas})
    # primary: baseline 3000 + 500, reporting 2700 + 480
    assert fac.terms == {"adjusted_baseline": 3500.0, "observed_reporting": 3180.0}
    assert fac.senpi == pytest.approx(3180 / 3500)
    assert fac.esp_td == pytest.approx(320.0)
    assert fac.improvement_pct == pytest.approx((1 - 3180 / 3500) * 100)
    assert fac.esp_td_uncertainty == pytest.approx(np.sqrt(30**2 + 10**2), rel=1e-6)
    assert fac.senpi_uncertainty == pytest.approx(fac.senpi * fac.esp_td_uncertainty / 3500)
    json.dumps(fac.as_dict())


def test_aggregate_refuses_mixed_methods_and_non_sep_results():
    f = _mr("forecast", {"adjusted_baseline": 1.0, "observed_reporting": 1.0})
    b = _mr("backcast", {"observed_baseline": 1.0, "adjusted_reporting": 1.0})
    with pytest.raises(ValueError, match="same adjustment model method"):
        aggregate_energy_types({"grid_electricity": f, "natural_gas": b})
    s = _mr("sequential_chain", None)
    with pytest.raises(ValueError, match="not an SEP method"):
        aggregate_energy_types({"grid_electricity": s})
    d = _mr("forecast", {"observed_reporting": 1.0}, declined=True)
    agg = aggregate_energy_types({"grid_electricity": f, "natural_gas": d})
    assert agg.declined and agg.senpi is None and "natural_gas" in agg.declined_reason


def test_aggregate_chaining_uses_eq6_on_summed_terms():
    t = {
        "observed_baseline": 100.0,
        "intermediate_at_baseline": 95.0,
        "intermediate_at_reporting": 110.0,
        "observed_reporting": 99.0,
    }
    fac = aggregate_energy_types({"natural_gas": _mr("chaining", t)})
    assert fac.senpi == pytest.approx(chained_senpi(100, 95, 110, 99))
    assert fac.esp_td == pytest.approx(5.0 + 11.0) and fac.senpi_uncertainty is None


# --------------------------------------------------------------------------- range rule (D2)


def test_sep_range_rule_uses_the_mean():
    rng = np.random.default_rng(0)
    T = rng.uniform(40, 80, 200)
    m = fit_model(T, 20 + 2 * np.maximum(0, T - 60) + rng.normal(0, 1, 200), "3PC")
    ok = sep_range_check(m, np.full(30, 60.0))
    assert ok.valid is True and ok.variables[0]["in_observed_range"]
    # a mean of 85 lies above the fitted range but within 3 sd of the fit mean (~60 +/- 3*11.5)
    near = sep_range_check(m, np.full(30, 85.0))
    assert near.valid is True
    assert not near.variables[0]["in_observed_range"] and near.variables[0]["within_3sd"]
    far = sep_range_check(m, np.full(30, 110.0))
    assert far.valid is False and "§6.4.2.1" in far.caveats[0]
    # hot extremes around a mild mean pass the mean rule -- why per-point coverage is primary
    hidden = sep_range_check(m, np.r_[np.full(15, 35.0), np.full(15, 95.0)])
    assert hidden.valid is True
    json.dumps(far.as_dict())


def test_sep_range_rule_not_evaluated_without_support():
    class Duck:
        def predict(self, T):
            return np.asarray(T)

    r = sep_range_check(Duck(), [1.0, 2.0])
    assert r.valid is None and "not evaluated" in r.caveats[0]
