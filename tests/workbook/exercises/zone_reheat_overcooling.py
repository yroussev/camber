"""Answer key: workbook exercise ``zone-reheat-overcooling``
(docs/workbook/zone-reheat-overcooling.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-fpu
    camber datasets ingest lbnl-fpu --store lab_store
    camber datasets config lbnl-fpu --exercise zone-reheat-overcooling --store lab_store \\
        --out fpu.json
    camber run fpu.json --out fpu_out
    camber datasets score lbnl-fpu --store lab_store --findings fpu_out/findings.json

(CAMBER 0.97.0-dev, lbnl-fpu default subset, 2026-09-29; the valve stuck shut re-checked on
0.98.0-dev, 2026-09-30, with the measured valve position mapped.)
"""

from __future__ import annotations

from _workbook import REAL, Check, Exercise, Finding, Metric, Run, Score
from _zone_standins import fpu_standin

FF = "PFPU__fault_free"
D50 = "PFPU__VAVDMPRStuck_50pct"
D100 = "PFPU__VAVDMPRStuck_100pct"
VSTUCK = "PFPU__ReheatVLVStuck_0pct"
LEAK = "PFPU__ReheatVLVLeak_50pctMaxFlow"


def _stuck_valve_is_named(ctx) -> None:
    """0.98 (#85): both rules read the measured position (0 %), stay ok, and caveat the demand
    that calls for full heat while the valve reads shut."""
    for rule in ("reheat_penalty", "overcooling_min_flow"):
        f = ctx.finding(rule, VSTUCK)
        assert f is not None, f"no {rule} finding on {VSTUCK}"
        assert f.metrics.get("valve_signal") == "position", (rule, f.metrics)
        assert (f.metrics.get("valve_divergence_share") or 0) >= 0.25, (rule, f.metrics)
        assert any("stuck or failed valve" in c for c in f.caveats), (rule, f.caveats)
    rp = ctx.finding("reheat_penalty", VSTUCK)
    assert rp.metrics.get("valve_open_pct") == 0.0, rp.metrics


EXERCISE = Exercise(
    id="zone-reheat-overcooling",
    title="Terminal units: the reheat penalty and overcooling at minimum airflow",
    issue=81,
    references=("pnnl-guide-zone-heat-cool", "pnnl-retuning-ch7"),
    datasets=("lbnl-fpu",),
    runs=(Run(dataset="lbnl-fpu", config="zone-reheat-overcooling"),),
    commands=(
        "camber datasets fetch lbnl-fpu",
        "camber datasets ingest lbnl-fpu --store lab_store",
        "camber datasets config lbnl-fpu --exercise zone-reheat-overcooling --store lab_store "
        "--out fpu.json",
        "camber run fpu.json --out fpu_out",
        "camber datasets score lbnl-fpu --store lab_store --findings fpu_out/findings.json",
    ),
    expect=(
        # a stuck damper: airflow leaves its setpoint, and the extra cold air is reheated
        Finding("airflow_tracking", D50, severity=("fault",)),
        Finding("airflow_tracking", D100, severity=("fault",)),
        Finding("airflow_tracking", FF, present=False),
        Finding("reheat_penalty", D50, severity=("fault",)),
        Finding("reheat_penalty", D100, severity=("fault",)),
        Metric("airflow_tracking", D100, "mean_abs_rel_error", 2.914, 0.01, on=REAL, quote="291%"),
        Metric("reheat_penalty", D100, "valve_open_pct", 98.89, 0.2, on=REAL, quote="99%"),
        Metric("reheat_penalty", D50, "valve_open_pct", 66.6, 0.2, on=REAL, quote="67%"),
        # fully open, the box cannot hold the heating setpoint on cold mornings
        Finding("unmet_setpoint_hours", D100, severity=("warn",)),
        Finding("unmet_setpoint_hours", FF, present=False),
        Metric("unmet_setpoint_hours", D100, "too_cold_pct", 10.14, 0.2, on=REAL, quote="10%"),
        # the fault-free box: a high minimum overcools the zone, and some of it is reheated
        Finding("overcooling_min_flow", FF, severity=("warn",)),
        Finding("reheat_penalty", FF, severity=("warn",)),
        Metric("reheat_penalty", FF, "valve_open_pct", 10.9, 0.2, on=REAL, quote="11%"),
        Metric(
            "overcooling_min_flow", FF, "overcool_at_minflow_pct", 84.54, 0.2, on=REAL, quote="85%"
        ),
        Metric(
            "overcooling_min_flow",
            FF,
            "median_minflow_fraction",
            0.483,
            0.005,
            on=REAL,
            quote="48%",
        ),
        # the stuck-open box is never "at minimum", so the minimum-flow rule is quiet on it
        Finding("overcooling_min_flow", D100, present=False),
        # a valve stuck shut delivers no reheat: read from the measured position, both rules are
        # ok and name the stuck valve (on the demand alone, before 0.98, both were faults)
        Finding("reheat_penalty", VSTUCK, severity=("ok",)),
        Finding("overcooling_min_flow", VSTUCK, severity=("ok",)),
        Check("the stuck-shut valve is named, not counted as reheat", _stuck_valve_is_named),
        Finding("reheat_penalty", LEAK, present=False),
        # the label score: airflow_tracking finds both stuck dampers and nothing else
        Score("airflow_tracking", tpr=1.0, fpr=0.0, quote="TPR 100%"),
        Score(None, tpr=0.5, fpr=0.0, quote="TPR 50%"),
    ),
    standin=fpu_standin,
)
