"""Answer key: workbook exercise ``zone-bad-box`` (docs/workbook/zone-bad-box.md).

Real-data figures were recorded from::

    camber datasets fetch ornl-frp-vav
    camber datasets ingest ornl-frp-vav --store lab_store
    camber datasets config ornl-frp-vav --store lab_store --out vav.json
    camber run vav.json --out vav_out
    camber datasets score ornl-frp-vav --store lab_store --findings vav_out/findings.json

(CAMBER 0.97.0-dev, ornl-frp-vav default subset, 2026-09-29.) The trend comparisons (damper,
airflow, the rooftop unit's airflow and static) read the same ingested store through
``camber.store.ParquetStore.read_role_frame``, over each day's occupied samples (the boxes'
occupancy point): what the lab's trend viewer shows.
"""

from __future__ import annotations

import numpy as np
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, Score
from _zone_standins import ORNL_SCENARIOS, ornl_rooms, ornl_standin

from camber.model.roles import Role
from camber.store import ParquetStore

FID = "ds-ornl-frp-vav"
STUCK = [s for s in ORNL_SCENARIOS if s != "fault_free"]


def _box(sc: str, room: str = "205") -> str:
    return f"RTU_VAV_{room}__d3_{sc}"


def _occupied_mean(ctx, equip: str, role: Role, *, occ_from: str | None = None) -> float:
    store = ParquetStore(ctx.store)
    frame = store.read_role_frame(facility_id=FID, equip=equip)
    occ_frame = store.read_role_frame(facility_id=FID, equip=occ_from or equip)
    occ = occ_frame[Role.OCCUPANCY].reindex(frame.index).fillna(0) > 0.5
    return float(frame.loc[occ, role].mean())


def _damper_is_flat(ctx) -> None:
    """On every faulted day box 205's damper sits at one position while occupied; on the
    fault-free day it modulates like its neighbours."""
    store = ParquetStore(ctx.store)
    for sc in ORNL_SCENARIOS:
        f = store.read_role_frame(facility_id=FID, equip=_box(sc))
        occ = f[Role.OCCUPANCY] > 0.5
        spread = float(f.loc[occ, Role.DAMPER].std())
        if sc == "fault_free":
            assert spread > 1.0, f"205 fault-free damper barely moves (std {spread:.2f})"
        else:
            assert spread < 0.5, f"205 damper moves on {sc} (std {spread:.2f})"


def _airflow_extremes(ctx) -> None:
    """Stuck shut, box 205 delivers the least air of its cohort; stuck fully open, the most."""
    for sc, pick in (("stuck_000", min), ("stuck_100", max)):
        flows = {r: _occupied_mean(ctx, _box(sc, r), Role.AIRFLOW) for r in ornl_rooms(ctx)}
        assert pick(flows, key=flows.get) == "205", f"{sc}: box airflows {flows}"


def _ahu_reacts(ctx) -> None:
    """The rooftop unit moves more air as the stuck box opens (airflow rises from the 0 % day to
    the 100 % day at every step) and its duct static falls (lower on the 100 % day than the 0 %
    day, never rising between steps by more than sensor noise)."""
    flow, static = [], []
    for sc in STUCK:
        rtu = f"RTU__d3_{sc}"
        flow.append(_occupied_mean(ctx, rtu, Role.AIRFLOW))
        static.append(_occupied_mean(ctx, rtu, Role.DUCT_STATIC))
    assert all(np.diff(flow) > 0), f"RTU airflow by stuck position {flow}"
    assert static[-1] < static[0], f"RTU duct static by stuck position {static}"
    assert max(np.diff(static)) < 0.05, f"RTU duct static by stuck position {static}"


def _rogue_is_205(ctx) -> None:
    """The SAT rogue-zone census, grouped per day under that day's rooftop unit, names box 205
    as the zone dragging the reset on the day it is stuck at 20 %."""
    f = ctx.finding("sat_rogue_zone_census", "<fleet>")
    assert f is not None, "no sat_rogue_zone_census finding"
    by = f.metrics.get("rogue_by_group") or {}
    assert by.get("RTU__d3_stuck_020") == [_box("stuck_020")], by


def _rogue_days_real(ctx) -> None:
    """On the real data: box 205 is the rogue on the fault-free, 20 % and 40 % days, and box 105
    on the 80 % day."""
    f = ctx.finding("sat_rogue_zone_census", "<fleet>")
    assert f is not None, "no sat_rogue_zone_census finding"
    want = {
        "RTU__d3_fault_free": [_box("fault_free")],
        "RTU__d3_stuck_020": [_box("stuck_020")],
        "RTU__d3_stuck_040": [_box("stuck_040")],
        "RTU__d3_stuck_080": [_box("stuck_080", "105")],
    }
    assert f.metrics.get("rogue_by_group") == want, f.metrics.get("rogue_by_group")


EXERCISE = Exercise(
    id="zone-bad-box",
    title="Terminal units: one bad box in a fleet",
    issue=81,
    references=("pnnl-retuning-ch7",),
    datasets=("ornl-frp-vav",),
    runs=(Run(dataset="ornl-frp-vav"),),
    commands=(
        "camber datasets fetch ornl-frp-vav",
        "camber datasets ingest ornl-frp-vav --store lab_store",
        "camber datasets config ornl-frp-vav --store lab_store --out vav.json",
        "camber run vav.json --out vav_out",
        "camber datasets score ornl-frp-vav --store lab_store --findings vav_out/findings.json",
    ),
    expect=(
        # the trends give the stuck box away; the comfort rule only sees some of the days
        Check("box 205's damper is flat on every faulted day", _damper_is_flat),
        Check("box 205 is the cohort's airflow extreme", _airflow_extremes),
        Finding("unmet_setpoint_hours", _box("stuck_020"), severity=("fault",)),
        Finding("unmet_setpoint_hours", _box("stuck_040"), severity=("fault",)),
        Finding("unmet_setpoint_hours", _box("fault_free"), severity=("fault",)),
        Metric(
            "unmet_setpoint_hours", _box("stuck_020"), "unmet_pct", 53.33, 0.2, on=REAL, quote="53%"
        ),
        Metric(
            "unmet_setpoint_hours",
            _box("fault_free"),
            "too_hot_pct",
            21.67,
            0.2,
            on=REAL,
            quote="22%",
        ),
        Finding("unmet_setpoint_hours", _box("stuck_000"), present=False),
        Finding("unmet_setpoint_hours", _box("stuck_060"), present=False),
        Finding("unmet_setpoint_hours", _box("stuck_080"), present=False),
        Finding("unmet_setpoint_hours", _box("stuck_100"), present=False),
        # the neighbours: room 102 is cold on every day, faulted or not
        *(
            Finding("unmet_setpoint_hours", _box(sc, "102"), severity=("fault",))
            for sc in ORNL_SCENARIOS
        ),
        # the zone cohort: 205 drags the SAT reset; the cohort as a whole is not starved
        Finding("sat_rogue_zone_census", "<fleet>", severity=("warn",)),
        Check("the rogue zone on the 20 % day is 205", _rogue_is_205),
        Check("rogue zones by day", _rogue_days_real, on=REAL),
        Finding("sat_cohort_starvation", "<fleet>", severity=("ok",)),
        # the air handler reacts to the stuck box
        Check("the rooftop unit's airflow and static follow the stuck box", _ahu_reacts),
        # the label score: 2 of 6 stuck days found, and the fault-free day flagged too
        Score("unmet_setpoint_hours", tpr=2 / 6, fpr=1.0, tol=0.01, quote="TPR 33%"),
    ),
    standin=ornl_standin,
)
