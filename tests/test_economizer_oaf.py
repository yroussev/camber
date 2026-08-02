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
    assert not stuck.caveats  # OAF is the strong signal -> no weak-proxy caveat


def test_high_oa_design_not_faulted_when_minimum_configured():
    # A 50%-OA design sitting at ~50% OA in hot weather is correct behaviour.
    f = _frame(mat=82.0)  # OAF ~50%
    assert EconomizerHighLimit().analyze("AHU", f).severity == "fault"  # default 20% min: false
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
