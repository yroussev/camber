"""Tests for period-aware chiller approach drift: PeriodRule, run_periods, and the frozen store."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_approach_baseline  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import discover  # noqa: E402
from camber.rules.base import PeriodRule, Registry, Rule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_approach_rule import ChillerApproachFouling  # noqa: E402
from camber.rules.chiller_drift_rule import ChillerApproachDrift  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_INTERCEPT_F = 2.0
_SLOPE_F_PER_TON = 0.01
_SIGMA_F = 0.25
_CHWS, _CHWR = 44.0, 56.0  # a fixed 12 degF loop dT, so tons = flow * 12 / 24 = flow / 2


def _tons_profile(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    tons = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(tons + offset, 40.0, 400.0)


def _role_frame(n=24 * 30, *, start="2025-05-01", seed=0, ramp_f=0.0, tons_offset=0.0):
    """An hourly chiller role-frame: cond approach plus the CHW points tons derive from."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons_profile(n, seed=seed, offset=tons_offset)
    approach = (
        _INTERCEPT_F
        + _SLOPE_F_PER_TON * tons
        + ramp_f * np.linspace(0.0, 1.0, n)
        + rng.normal(0, _SIGMA_F, n)
    )
    return pd.DataFrame(
        {
            Role.COND_APPROACH_TEMP: approach,
            Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
            Role.CHW_SUPPLY_TEMP: np.full(n, _CHWS),
            Role.CHW_RETURN_TEMP: np.full(n, _CHWR),
        },
        index=idx,
    )


def _rule(store, **kw):
    return ChillerApproachDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


# --------------------------------------------------------------------- protocol / interface


def test_period_rule_protocol_is_separate_from_rule():
    drift = _rule(BaselineStore())
    assert isinstance(drift, PeriodRule)
    # the drift rule is period-only; the single-frame protocol is untouched by its existence
    assert not isinstance(drift, Rule)
    assert isinstance(SimultaneousHeatCool(), Rule)
    assert not isinstance(SimultaneousHeatCool(), PeriodRule)


def test_existing_rules_and_registry_run_are_unaffected():
    # the drift rule needs an injected store, so it is not auto-registered
    assert "chiller_approach_drift" not in rule_names()
    assert "chiller_approach_drift" not in builtin_registry().names()
    # and the fouling rule still produces exactly what it did: median vs design, no time term
    frame = _role_frame()
    f = ChillerApproachFouling().analyze("CH_1", frame)
    median = float(frame[Role.COND_APPROACH_TEMP].median())
    assert f.rule == "chiller_approach_fouling"
    assert f.metrics["cond_approach_f"] == round(median, 2)
    assert f.metrics["cond_design_f"] == 5.0


# --------------------------------------------------------------------- the drift comparison


def test_drifting_chiller_flags_against_a_frozen_baseline():
    store = BaselineStore()
    rule = _rule(store)
    baseline = _role_frame(start="2025-05-01", seed=1)
    current = _role_frame(start="2025-06-01", seed=2, ramp_f=4.0)

    f = rule.analyze_periods("CH_1", baseline, current)
    assert f.rule == "chiller_approach_drift"
    assert f.severity == "fault"
    assert f.metrics["cond_drift_f"] > 1.5
    assert f.metrics["cond_drift_sigma"] > 5.0
    assert 3.0 < f.metrics["cond_slope_f_per_month"] < 5.5  # still climbing
    assert f.metrics["thresholds_provisional"] is True  # severities are not yet field-validated
    assert f.metrics["cond_baseline_frozen_at"] == "2025-07-01T00:00"
    # this rule's severity is a magnitude claim only -- it makes no claim about timing
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert "temporal_threshold_confidence" not in f.metrics


def test_a_stable_chiller_does_not_flag():
    store = BaselineStore()
    f = _rule(store).analyze_periods(
        "CH_1", _role_frame(start="2025-05-01", seed=1), _role_frame(start="2025-06-01", seed=2)
    )
    assert f.severity == "ok"
    assert abs(f.metrics["cond_drift_f"]) < 0.3


def test_a_merely_busier_period_is_not_drift():
    """Load normalization: the same machine working harder must not read as degradation."""
    store = BaselineStore()
    baseline = _role_frame(start="2025-05-01", seed=3)
    busy = _role_frame(start="2025-06-01", seed=4, tons_offset=90.0)
    # a level-vs-level comparison would see a real-looking rise
    raw = float(busy[Role.COND_APPROACH_TEMP].median()) - float(
        baseline[Role.COND_APPROACH_TEMP].median()
    )
    assert raw > 0.7

    f = _rule(store).analyze_periods("CH_1", baseline, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["cond_drift_f"]) < 0.3
    # and the extrapolation beyond the fitted envelope is declared, not hidden
    assert any("load envelope" in c for c in f.caveats)


# --------------------------------------------------------------------- freeze / accept-new-normal


def test_the_baseline_is_frozen_and_never_silently_refit():
    store = BaselineStore()
    rule = _rule(store)
    baseline = _role_frame(start="2025-05-01", seed=1)
    rule.analyze_periods("CH_1", baseline, _role_frame(start="2025-06-01", seed=2, ramp_f=4.0))
    coeffs = dict(store.get("SITE", "CH_1", "chiller_approach_cond").coefficients)

    # a later run over a *different*, more-degraded window must not move the reference
    later = _role_frame(start="2025-07-01", seed=5, ramp_f=6.0)
    f = rule.analyze_periods("CH_1", later, later)
    assert store.get("SITE", "CH_1", "chiller_approach_cond").coefficients == coeffs
    assert f.severity == "fault"  # judged against the original baseline, so the drift still shows


def test_accept_new_normal_moves_the_reference_and_silences_prior_drift():
    store = BaselineStore()
    rule = _rule(store)
    baseline = _role_frame(start="2025-05-01", seed=1)
    drifted = _role_frame(start="2025-06-01", seed=2, ramp_f=4.0)
    assert rule.analyze_periods("CH_1", baseline, drifted).severity == "fault"

    # the operator services the machine and accepts the post-service performance as normal
    refit = fit_approach_baseline(
        pd.DataFrame(
            {
                "tons": drifted[Role.CHW_FLOW] / 2.0,
                Role.COND_APPROACH_TEMP: drifted[Role.COND_APPROACH_TEMP],
            }
        ),
        approach_col=Role.COND_APPROACH_TEMP,
    )
    rec = store.accept_new_normal(
        refit,
        site="SITE",
        equip="CH_1",
        kind="chiller_approach_cond",
        accepted_by="plant.operator",
        reason="condenser tubes brushed; post-service performance accepted",
        at="2025-06-30T09:00",
    )
    assert rec.accepted_by == "plant.operator"
    assert rec.supersedes == "2025-07-01T00:00"
    assert len(rec.history) == 1  # the old baseline is retained, not overwritten

    # the same window that was a fault is now the reference, so it no longer drifts
    after = rule.analyze_periods("CH_1", baseline, drifted)
    assert after.severity == "ok"
    assert abs(after.metrics["cond_drift_f"]) < 0.5


def test_accept_new_normal_demands_attribution():
    store = BaselineStore()
    fit = fit_approach_baseline(
        pd.DataFrame(
            {
                "tons": _tons_profile(500),
                "approach_f": _INTERCEPT_F + _SLOPE_F_PER_TON * _tons_profile(500),
            }
        )
    )
    kw = dict(site="S", equip="CH_1", kind="chiller_approach_cond", at="t0")
    with pytest.raises(ValueError, match="accepted_by"):
        store.accept_new_normal(fit, accepted_by="  ", reason="because", **kw)
    with pytest.raises(ValueError, match="reason"):
        store.accept_new_normal(fit, accepted_by="op", reason="", **kw)


def test_freeze_refuses_to_overwrite_an_existing_baseline():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("CH_1", _role_frame(), _role_frame(start="2025-06-01"))
    frozen = store.get("SITE", "CH_1", "chiller_approach_cond")
    with pytest.raises(ValueError, match="accept_new_normal"):
        store.freeze(
            frozen.model(),
            site="SITE",
            equip="CH_1",
            kind="chiller_approach_cond",
            frozen_at="later",
        )


def test_store_round_trips_coefficients_and_provenance(tmp_path):
    path = str(tmp_path / "baselines.json")
    store = BaselineStore(path)
    rule = _rule(store)
    rule.analyze_periods("CH_1", _role_frame(), _role_frame(start="2025-06-01"))
    assert store.save() == 1

    again = BaselineStore.load(path)
    rec = again.get("SITE", "CH_1", "chiller_approach_cond")
    assert rec is not None
    assert rec.reason.startswith("initial baseline")
    assert rec.period_start.startswith("2025-05-01")
    original = store.get("SITE", "CH_1", "chiller_approach_cond").model()
    assert again.model_for("SITE", "CH_1", "chiller_approach_cond") == original


def test_no_frozen_baseline_and_freezing_disabled_declines():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods(
        "CH_1", _role_frame(), _role_frame(start="2025-06-01")
    )
    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert any("no frozen baseline" in c for c in f.caveats)


# --------------------------------------------------------------------- run_periods, end to end

_HDR = "Timestamp,Value\n"


def _write(folder, equip, measure, series):
    rows = [
        f"{t.strftime('%d-%b-%y %I:%M:%S %p')} PDT,{v}\n"
        for t, v in zip(series.index, series.values)
    ]
    with open(os.path.join(folder, f"{equip}_{measure}.csv"), "w", encoding="utf-8") as fh:
        fh.write(_HDR + "".join(rows))


def _fixture_folder(folder, *, ramp_f):
    """One chiller, 60 days hourly: a May baseline month then a June current month."""
    frame = _role_frame(n=24 * 60, start="2025-05-01", seed=7, ramp_f=ramp_f)
    for role, measure in (
        (Role.COND_APPROACH_TEMP, "Cond_Approach"),
        (Role.CHW_FLOW, "CHW_Flow"),
        (Role.CHW_SUPPLY_TEMP, "CHWS_Temp"),
        (Role.CHW_RETURN_TEMP, "CHWR_Temp"),
    ):
        _write(folder, "CH_1", measure, frame[role])
    return MappingProvider.from_dict(
        {
            "aliases": {
                "Cond_Approach": "cond_approach_temp",
                "CHW_Flow": "chw_flow",
                "CHWS_Temp": "chw_supply_temp",
                "CHWR_Temp": "chw_return_temp",
            }
        }
    )


def _run(folder, mapping, store, *, baseline, current):
    reg = Registry()
    reg.register(_rule(store))
    refs = discover(folder, "CH", marker_measure="Cond_Approach")
    return reg.run_periods(
        "chiller_approach_drift", refs, mapping, baseline=baseline, current=current
    )


def test_run_periods_end_to_end_flags_the_drifting_month(tmp_path):
    folder = str(tmp_path)
    mapping = _fixture_folder(folder, ramp_f=8.0)  # 8 degF across the whole 60 days
    findings = _run(
        folder,
        mapping,
        BaselineStore(),
        baseline=("2025-05-01", "2025-05-31 23:00"),
        current=("2025-06-01", None),  # open-ended current side
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.equip == "CH_1" and f.rule == "chiller_approach_drift"
    assert f.severity in ("warn", "fault")
    assert f.metrics["cond_drift_f"] > 1.0


def test_run_periods_end_to_end_is_quiet_on_a_stable_chiller(tmp_path):
    folder = str(tmp_path)
    mapping = _fixture_folder(folder, ramp_f=0.0)
    findings = _run(
        folder,
        mapping,
        BaselineStore(),
        baseline=("2025-05-01", "2025-05-31 23:00"),
        current=("2025-06-01", None),
    )
    assert len(findings) == 1 and findings[0].severity == "ok"


def test_run_periods_declines_loudly_on_an_empty_window(tmp_path):
    folder = str(tmp_path)
    mapping = _fixture_folder(folder, ramp_f=4.0)
    findings = _run(
        folder,
        mapping,
        BaselineStore(),
        baseline=("2025-05-01", "2025-05-31 23:00"),
        current=("2030-01-01", "2030-02-01"),  # no data there
    )
    # a chiller dropped silently from a drift report reads as "no drift" -- it must say so
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert findings[0].metrics["empty_periods"] == ["current"]
    assert findings[0].caveats


def test_run_periods_rejects_a_malformed_period(tmp_path):
    folder = str(tmp_path)
    mapping = _fixture_folder(folder, ramp_f=0.0)
    with pytest.raises(ValueError, match="after its end"):
        _run(
            folder,
            mapping,
            BaselineStore(),
            baseline=("2025-05-31", "2025-05-01"),
            current=("2025-06-01", None),
        )
    with pytest.raises(ValueError, match=r"\(start, end\) pair"):
        _run(folder, mapping, BaselineStore(), baseline="2025-05-01", current=("2025-06-01", None))
