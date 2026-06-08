"""Validate the simultaneous-H/C metric against the spec's synthetic fixtures."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.scatter import hec_metrics  # noqa: E402
from camber.synth import make_ahu_trends  # noqa: E402


def test_fixture_a_no_overlap():
    # Good operation: heating and cooling should essentially never overlap.
    df = make_ahu_trends(days=14, fault="none", seed=1)
    m = hec_metrics(df, 1, occupied_only=True)
    assert m.simultaneous_pct < 2.0, m.as_dict()
    assert m.simultaneous_pct_oat_gt_65 < 1.0, m.as_dict()


def test_fixture_b_reheat_fault():
    # Reheat fault: nonzero simultaneity, concentrated at high OAT.
    df = make_ahu_trends(days=14, fault="reheat", seed=1)
    m = hec_metrics(df, 1, occupied_only=True)
    assert m.simultaneous_pct > 5.0, m.as_dict()
    assert m.simultaneous_pct_oat_gt_65 > 5.0, m.as_dict()
    assert m.median_overlap > 0.0


def test_fault_worse_than_clean():
    clean = hec_metrics(make_ahu_trends(fault="none", seed=2), 1, occupied_only=True)
    fault = hec_metrics(make_ahu_trends(fault="reheat", seed=2), 1, occupied_only=True)
    assert fault.simultaneous_pct > clean.simultaneous_pct + 5.0
