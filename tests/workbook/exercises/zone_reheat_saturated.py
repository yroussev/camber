"""Answer key: workbook exercise ``zone-reheat-saturated`` (docs/workbook/zone-reheat-saturated.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-fpu
    camber datasets ingest lbnl-fpu --store lab_store
    camber datasets config lbnl-fpu --exercise zone-reheat-saturated --store lab_store --out rh.json
    camber run rh.json --out rh_out
    camber datasets fetch ornl-frp-vav
    camber datasets ingest ornl-frp-vav --store lab_store
    camber datasets config ornl-frp-vav --exercise zone-reheat-saturated--ornl-frp-vav \\
        --store lab_store --out rh_ornl.json
    camber run rh_ornl.json --out rh_ornl_out

(CAMBER 0.97.0-dev, lbnl-fpu and ornl-frp-vav default subsets, 2026-09-29.)
"""

from __future__ import annotations

from _workbook import REAL, Check, Exercise, Finding, Metric, Run
from _zone_standins import fpu_standin, ornl_rooms, ornl_standin

FF = "PFPU__fault_free"
D50 = "PFPU__VAVDMPRStuck_50pct"
VSTUCK = "PFPU__ReheatVLVStuck_0pct"
LEAK = "PFPU__ReheatVLVLeak_50pctMaxFlow"
COLD_ROOM = "RTU_VAV_102__d3_fault_free"


def standin(store) -> None:
    """Both datasets' stand-ins (shared with zone-reheat-overcooling and zone-bad-box)."""
    fpu_standin(store)
    ornl_standin(store)


def _capacity_not_airflow(ctx) -> None:
    """The stuck-shut valve's shortfall happens at the airflow the box asks for: the cause CAMBER
    points to is heating capacity, not airflow."""
    f = ctx.finding("reheat_capacity_shortfall", VSTUCK, "fpu")
    assert f is not None, f"no reheat_capacity_shortfall finding on {VSTUCK}"
    assert f.metrics.get("likely_cause") == "capacity", f.metrics.get("likely_cause")
    ratio = f.metrics.get("airflow_to_sp_ratio")
    assert ratio is not None and ratio >= 0.9, f"airflow/setpoint in the shortfall = {ratio}"


def _shortfall_not_overcooling(ctx) -> None:
    """overcooling_severity sets the cold, valve-saturated samples apart as a heating shortfall:
    no overcooling warn/fault, but its shortfall grade is warn or fault."""
    f = ctx.finding("overcooling_severity", VSTUCK, "fpu")
    assert f is not None, f"no overcooling_severity finding on {VSTUCK}"
    assert f.severity in ("ok", "info"), f"overcooling severity {f.severity}"
    assert f.metrics.get("shortfall_severity") in ("warn", "fault"), f.metrics


def _no_valve_no_verdict(ctx) -> None:
    """With the reheat not trended as a valve, reheat_capacity_shortfall judges no ORNL box."""
    got = [f.equip for f in ctx.findings("ornl") if f.rule == "reheat_capacity_shortfall"]
    assert not got, f"reheat_capacity_shortfall judged {got}"
    boxes = {f.equip for f in ctx.findings("ornl") if f.rule == "unmet_setpoint_hours"}
    want = {f"RTU_VAV_{r}__d3_fault_free" for r in ornl_rooms(ctx)}
    assert boxes == want, f"unmet_setpoint_hours ran on {sorted(boxes)}"


EXERCISE = Exercise(
    id="zone-reheat-saturated",
    title="Terminal units: a zone below setpoint with its reheat maxed out",
    issue=81,
    references=("pnnl-guide-zone-heat-cool", "pnnl-retuning-ch7"),
    datasets=("lbnl-fpu", "ornl-frp-vav"),
    runs=(
        Run(dataset="lbnl-fpu", name="fpu", config="zone-reheat-saturated"),
        Run(dataset="ornl-frp-vav", name="ornl", config="zone-reheat-saturated--ornl-frp-vav"),
    ),
    commands=(
        "camber datasets fetch lbnl-fpu",
        "camber datasets ingest lbnl-fpu --store lab_store",
        "camber datasets config lbnl-fpu --exercise zone-reheat-saturated --store lab_store "
        "--out rh.json",
        "camber run rh.json --out rh_out",
        "camber datasets fetch ornl-frp-vav",
        "camber datasets ingest ornl-frp-vav --store lab_store",
        "camber datasets config ornl-frp-vav --exercise zone-reheat-saturated--ornl-frp-vav "
        "--store lab_store --out rh_ornl.json",
        "camber run rh_ornl.json --out rh_ornl_out",
    ),
    expect=(
        # the stuck-shut valve: cold zone, reheat demand saturated, airflow as asked
        Finding("reheat_capacity_shortfall", VSTUCK, severity=("fault",), run="fpu"),
        Finding("unmet_setpoint_hours", VSTUCK, severity=("fault",), run="fpu"),
        Check("the cause is capacity, not airflow", _capacity_not_airflow),
        Check("a heating shortfall, not overcooling", _shortfall_not_overcooling),
        Metric(
            "reheat_capacity_shortfall",
            VSTUCK,
            "shortfall_pct",
            31.75,
            0.1,
            run="fpu",
            on=REAL,
            quote="31.8%",
        ),
        Metric(
            "reheat_capacity_shortfall",
            VSTUCK,
            "shortfall_hours",
            663.0,
            1.0,
            run="fpu",
            on=REAL,
            quote="663 h",
        ),
        Metric(
            "reheat_capacity_shortfall",
            VSTUCK,
            "median_deficit_f",
            4.48,
            0.05,
            run="fpu",
            on=REAL,
            quote="4.5 °F",
        ),
        Metric(
            "unmet_setpoint_hours",
            VSTUCK,
            "too_cold_pct",
            37.41,
            0.2,
            run="fpu",
            on=REAL,
            quote="37%",
        ),
        Metric(
            "reheat_capacity_shortfall",
            VSTUCK,
            "discharge_temp_f",
            60.8,
            0.1,
            run="fpu",
            on=REAL,
            quote="60.8 °F",
        ),
        Metric(
            "overcooling_severity",
            VSTUCK,
            "shortfall_warn_pct",
            29.93,
            0.1,
            run="fpu",
            on=REAL,
            quote="30%",
        ),
        # the other runs have heat to spare
        Finding("reheat_capacity_shortfall", FF, present=False, run="fpu"),
        Finding("reheat_capacity_shortfall", LEAK, present=False, run="fpu"),
        Finding("reheat_capacity_shortfall", D50, present=False, run="fpu"),
        # the trap: the penalty rule reads the controller's demand as reheat delivered
        Finding("reheat_penalty", VSTUCK, severity=("fault",), run="fpu"),
        Metric(
            "reheat_penalty", VSTUCK, "valve_open_pct", 50.71, 0.2, run="fpu", on=REAL, quote="51%"
        ),
        # ORNL: electric reheat logged as energy -- cold rooms, but no reheat verdict
        Check("no reheat verdict without a reheat valve", _no_valve_no_verdict),
        Finding("unmet_setpoint_hours", COLD_ROOM, severity=("fault",), run="ornl"),
        Metric(
            "unmet_setpoint_hours",
            COLD_ROOM,
            "too_cold_pct",
            33.33,
            0.2,
            run="ornl",
            on=REAL,
            quote="33%",
        ),
        Finding(
            "overcooling_severity",
            "RTU_VAV_104__d3_fault_free",
            severity=("fault",),
            run="ornl",
            on=REAL,
        ),
        Metric(
            "overcooling_severity",
            "RTU_VAV_104__d3_fault_free",
            "max_depth_f",
            4.57,
            0.05,
            run="ornl",
            on=REAL,
            quote="4.6 °F",
        ),
    ),
    standin=standin,
)
