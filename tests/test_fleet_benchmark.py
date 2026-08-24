"""The fleet benchmark runner emits valid metrics and passes its own committed baseline at tol 0.0.

Guards the CI gate: the generated-fleet accuracy is deterministic, so the runner must reproduce the
committed baseline exactly (the same check `.github/workflows/benchmark.yml` runs).
"""

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.eval import check_against_baseline  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BENCH = os.path.join(_ROOT, "examples", "fleet_fdd", "benchmark.py")
_BASELINE = os.path.join(_ROOT, "examples", "fleet_fdd", "benchmark-baseline.json")


def _runner():
    spec = importlib.util.spec_from_file_location("fleet_benchmark", _BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_metrics_dict_keys_are_valid_floats():
    m = _runner().metrics_dict()
    assert m["coverage.n_fleet_scored"] == 6
    rates = {k: v for k, v in m.items() if k != "coverage.n_fleet_scored"}
    for k, v in rates.items():
        assert isinstance(v, float) and v == v and 0.0 <= v <= 1.0, k
    # every detector reports tpr + fpr + attribution
    assert sum(1 for k in m if k.endswith(".attribution")) == 6


def test_runner_passes_its_committed_baseline_at_tol_zero():
    m = _runner().metrics_dict()
    baseline = json.load(open(_BASELINE))
    chk = check_against_baseline(m, baseline, tol=0.0)
    assert chk.passed, f"regressions={chk.regressions} missing={chk.missing}"


def test_baseline_file_matches_current_metrics_exactly():
    # the committed baseline must be regenerated when the harness changes (no silent drift)
    assert _runner().metrics_dict() == json.load(open(_BASELINE))
