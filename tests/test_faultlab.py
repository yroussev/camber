"""Tests for the synthetic fault-injection accuracy harness (camber.faultlab).

Proves the harness is deterministic and that every accuracy-scored rule fires on its injected fault
and stays silent on its clean frame — the CI-runnable complement to the real-data LBNL benchmark.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.eval import benchmark, check_against_baseline  # noqa: E402


def test_every_scored_rule_detects_its_own_fault_and_is_clean():
    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    for name, c in rep.per_detector.items():
        assert c.total, f"{name} produced no records"
        assert c.true_positive_rate == 1.0, f"{name} did not fire on its injected fault"
        assert c.false_positive_rate == 0.0, f"{name} fired on its clean frame"
    assert rep.overall.true_positive_rate == 1.0 and rep.overall.false_positive_rate == 0.0


def test_scenarios_key_on_real_single_equipment_rules():
    from camber.rules.builtin import builtin_registry, is_fleet

    reg = builtin_registry()
    for name in faultlab.SCENARIOS:
        r = reg.get(name)
        assert r is not None and not is_fleet(r), f"{name} is not a single-equipment rule"


def test_coverage_partitions_the_registry():
    from camber.rules.builtin import builtin_registry

    reg = builtin_registry()
    cov = faultlab.coverage()
    assert len(cov["scored"]) == len(faultlab.SCENARIOS)
    assert len(cov["scored"]) + len(cov["fixture_only"]) == cov["n_single"]
    # scored + fixture-only + fleet accounts for every registered rule
    assert len(cov["scored"]) + len(cov["fixture_only"]) + len(cov["fleet"]) == len(reg.names())
    # 0.6: the whole single-equipment suite is accuracy-scored — no fixture-only rules remain
    assert cov["fixture_only"] == [] and len(cov["scored"]) == cov["n_single"]


def test_records_are_deterministic():
    a = faultlab.labeled_records()
    b = faultlab.labeled_records()
    assert a == b  # same frames + rules -> identical records (stable baseline)


def test_g36_engine_detects_every_covered_fault_and_stays_quiet():
    g = faultlab.g36_accuracy()
    assert g["n_fc"] >= 6
    for fc, d in g["per_fc"].items():
        assert d["detected"], f"G36 FC{fc} did not trip on its injected fault"
        assert d["clean_quiet"], f"G36 FC{fc} fired on its clean frame"
    assert g["tpr"] == 1.0 and g["fpr"] == 0.0


def test_cross_fire_reports_only_co_detections_not_self():
    xf = faultlab.cross_fire()
    for name, others in xf.items():
        assert name not in others  # a rule never lists itself as cross-firing


def test_benchmark_gate_passes_against_committed_baseline():
    # load the example runner under a unique module name (the LBNL example is also named
    # `benchmark`)
    import importlib.util

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "examples", "synthetic_fdd", "benchmark.py")
    spec = importlib.util.spec_from_file_location("synthetic_fdd_benchmark", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    baseline = json.load(
        open(os.path.join(root, "examples", "synthetic_fdd", "benchmark-baseline.json"))
    )
    chk = check_against_baseline(bench.metrics_dict(), baseline, tol=0.0)
    assert chk.passed, f"synthetic benchmark regressed vs committed baseline: {chk.regressions}"
