"""Tests for the economizer high-limit rule's OA-fraction path and per-rule config params.

A high-outside-air design (units correctly near their design-minimum OA in hot weather) must
NOT be reported as a stuck economizer. Two mechanisms: judge on outside-air *fraction* when
mixed/return-air temps are present (damper % is a weak proxy), and make the minimum settable.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import make_rule  # noqa: E402
from camber.rules.economizer_lockout_rule import EconomizerHighLimit  # noqa: E402


def _frame(mat=None, rat=74.0, damper=0.5, n=200):
    """Half the hours hot (90F), half mild (55F); RAT fixed, MAT sets the OA-fraction."""
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    oat = pd.Series(np.where(np.arange(n) % 2 == 0, 90.0, 55.0), index=idx)
    cols = {Role.OAT: oat, Role.OA_DAMPER: pd.Series(damper, index=idx)}
    if mat is not None:
        cols[Role.MIXED_AIR_TEMP] = pd.Series(mat, index=idx)
        cols[Role.RETURN_AIR_TEMP] = pd.Series(rat, index=idx)
    return pd.DataFrame(cols)


# OAF = 100*(RAT-MAT)/(RAT-OAT); with RAT=74, OAT=90 -> MAT 86.8~80%, 82~50%, 77.2~20%


def test_oaf_path_flags_stuck_open_not_at_minimum():
    stuck = EconomizerHighLimit().analyze("AHU", _frame(mat=86.8))  # OAF ~80% when hot
    at_min = EconomizerHighLimit().analyze("AHU", _frame(mat=77.2))  # OAF ~20% (locked out)
    assert stuck.severity == "fault" and stuck.metrics["basis"] == "OA-fraction"
    assert at_min.severity == "ok"
    assert not any("weak proxy" in c for c in stuck.caveats)  # OAF is the strong signal
    # 80 % OA is excess whatever the design minimum is: it clears the generous 50 % bound too
    assert stuck.metrics["min_oa_source"] == "unknown"
    configured = EconomizerHighLimit(min_oa_pct=20.0).analyze("AHU", _frame(mat=86.8))
    assert configured.metrics["min_oa_source"] == "configured" and not configured.caveats


def test_high_oa_design_not_faulted_when_minimum_configured():
    # A 50%-OA design sitting at ~50% OA in hot weather is correct behaviour.
    f = _frame(mat=82.0)  # OAF ~50%
    # with no configured minimum this used to fault against an assumed 20 % -- a false fault on a
    # high-OA design. Now it declines: whether 50 % is excess depends on the unknown minimum.
    unknown = EconomizerHighLimit().analyze("AHU", f)
    assert unknown.severity == "info" and unknown.metrics["not_locked_out_pct"] is None
    assert unknown.metrics["excess_over_assumed_min_pct"] == 100.0
    assert any("design minimum OA unknown" in c for c in unknown.caveats)
    assert EconomizerHighLimit(min_oa_pct=20.0).analyze("AHU", f).severity == "fault"
    tuned = EconomizerHighLimit(min_oa_pct=55.0).analyze("AHU", f)  # its real design minimum
    assert tuned.severity == "ok"


def test_damper_fallback_caveats_the_weak_proxy():
    # No mixed/return-air temps: falls back to damper %, and says so.
    f = _frame(mat=None, damper=0.5)
    default = EconomizerHighLimit().analyze("AHU", f)
    assert default.severity == "fault" and default.metrics["basis"] == "damper position"
    assert any("weak proxy" in c for c in default.caveats)
    # configuring the design-minimum damper clears the false fault
    tuned = EconomizerHighLimit(min_damper=0.55).analyze("AHU", f)
    assert tuned.severity == "ok"


def test_damper_scale_agnostic_percent_or_fraction():
    """The pipeline delivers OA_DAMPER in percent (0-100); a stray source may give 0-1.

    Both must be judged identically against the fraction threshold -- the mis-scaling that
    made every open damper read 'not locked out' (≈99.99% of hot hours in the field) must not recur.
    """
    idx = pd.date_range("2024-07-01", periods=200, freq="1h")
    oat = pd.Series(np.where(np.arange(200) % 2 == 0, 90.0, 55.0), index=idx)

    def econ(damper_hot):  # damper_hot applied during hot hours, near-closed when mild
        damper = pd.Series(np.where(oat > 65, damper_hot, damper_hot * 0.3), index=idx)
        return EconomizerHighLimit().analyze(
            "AHU", pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: damper})
        )

    # a unit genuinely at ~24% OA (below the 25% default min) must NOT fault, in either scale
    assert econ(0.24).severity == "ok"  # fraction
    assert econ(24.0).severity == "ok"  # percent -- same verdict
    # a clearly-open economizer faults in either scale
    assert econ(0.70).severity == "fault"
    assert econ(70.0).severity == "fault"


def test_never_hot_is_info():
    idx = pd.date_range("2024-01-01", periods=100, freq="1h")
    cold = pd.DataFrame(
        {Role.OAT: pd.Series(40.0, index=idx), Role.OA_DAMPER: pd.Series(0.5, index=idx)}
    )
    assert EconomizerHighLimit().analyze("AHU", cold).severity == "info"


def test_make_rule_applies_and_validates_params():
    r = make_rule("economizer_high_limit", high_limit_f=75.0, min_damper=0.45)
    assert r.high_limit_f == 75.0 and r.min_damper == 0.45
    assert make_rule("economizer_high_limit").high_limit_f == 65.0  # default preserved
    for bad, exc in [
        (("nonesuch_rule", {}), KeyError),
        (("economizer_high_limit", {"z": 1}), TypeError),
    ]:
        try:
            make_rule(bad[0], **bad[1])
        except exc:
            pass
        else:
            raise AssertionError(f"expected {exc.__name__}")


def _measured(oa_hot_pct, n=400, rat=74.0, with_temps=True):
    """Measured OA/supply airflow on a hot-weather frame (OAT 90 F, above RAT)."""
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    oat = pd.Series(90.0, index=idx)
    sa = pd.Series(4000.0, index=idx)
    cols = {
        Role.OAT: oat,
        Role.OA_DAMPER: pd.Series(40.0, index=idx),
        Role.OA_AIRFLOW: sa * oa_hot_pct / 100.0,
        Role.AIRFLOW: sa,
    }
    if with_temps:
        # a temperature balance that *over*-reads the OA fraction (sensor bias near the coil)
        cols[Role.RETURN_AIR_TEMP] = pd.Series(rat, index=idx)
        cols[Role.MIXED_AIR_TEMP] = pd.Series(rat + 0.9 * (90.0 - rat), index=idx)
    return pd.DataFrame(cols)


def test_measured_oa_airflow_is_used_and_an_unknown_minimum_is_not_faulted():
    # real case: four RTUs holding ~25-33 % OA above the high limit (a plausible design minimum,
    # measured by an OA flow station) all faulted against an assumed 20 % minimum, and the mapped
    # OA_AIRFLOW was ignored in favour of the temperature balance
    f = EconomizerHighLimit().analyze("RTU", _measured(oa_hot_pct=33.0))
    assert f.metrics["basis"] == "measured OA fraction"
    assert f.severity == "info", f.summary
    assert f.metrics["not_locked_out_pct"] is None
    assert any("design minimum OA unknown" in c for c in f.caveats)
    # configured at its real 33 % design minimum it is locked out; at 20 % it is excess
    assert EconomizerHighLimit(min_oa_pct=33.0).analyze("RTU", _measured(33.0)).severity == "ok"
    assert EconomizerHighLimit(min_oa_pct=20.0).analyze("RTU", _measured(33.0)).severity == "fault"
    # a genuinely stuck-open economizer (85 % OA) faults with no minimum configured at all
    assert EconomizerHighLimit().analyze("RTU", _measured(oa_hot_pct=85.0)).severity == "fault"


def test_differential_changeover_is_not_a_missing_lockout():
    # OAT 68 F against a 74 F return: a differential dry-bulb economizer correctly economizes
    idx = pd.date_range("2024-07-01", periods=200, freq="1h")
    f = pd.DataFrame(
        {
            Role.OAT: pd.Series(68.0, index=idx),
            Role.OA_DAMPER: pd.Series(100.0, index=idx),
            Role.RETURN_AIR_TEMP: pd.Series(74.0, index=idx),
            Role.MIXED_AIR_TEMP: pd.Series(68.3, index=idx),  # ~95 % OA
        }
    )
    got = EconomizerHighLimit().analyze("AHU", f)
    assert got.severity == "info" and "no hours above" in got.summary
    assert any("differential dry-bulb" in c for c in got.caveats)
    fixed = EconomizerHighLimit(differential=False, min_oa_pct=20.0).analyze("AHU", f)
    assert fixed.severity == "fault"
