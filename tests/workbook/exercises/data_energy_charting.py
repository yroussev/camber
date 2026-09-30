"""Answer key: workbook exercise ``data-energy-charting`` (docs/workbook/data-energy-charting.md).

Real-data figures were recorded from::

    camber datasets fetch bdg2
    camber datasets fetch valladolid-uva
    camber datasets ingest bdg2 valladolid-uva --store lab_store
    camber datasets config bdg2 --facility ds-bdg2-fox --store lab_store --out fox.json
    camber run fox.json --out fox_out
    camber datasets config valladolid-uva --store lab_store --out uva.json
    camber run uva.json --out uva_out

and the Python read of the same store shown on the page (``camber.demand.analyze_demand`` and
``baseload_anomaly``, ``camber.loadprofile.weekday_weekend_profiles``, calendar year 2016).

(CAMBER 0.97.0-dev, bdg2 and valladolid-uva default subsets, 2026-09-29.)
"""

from __future__ import annotations

from _practice_standins import bdg2_fox, valladolid
from _workbook import REAL, Check, Exercise, Finding, Metric, Run

from camber.demand import analyze_demand, baseload_anomaly
from camber.loadprofile import weekday_weekend_profiles
from camber.model.roles import Role
from camber.store import ParquetStore

AUDREY = "Fox_assembly_Audrey__electricity"
STEPHEN_EL = "Fox_lodging_Stephen__electricity"
STEPHEN_CHW = "Fox_lodging_Stephen__chilledwater"


def _load(ctx, run: str, equip: str):
    fid = ctx.config(run)["source"]["facility_id"]
    frame = ParquetStore(ctx.store).read_role_frame(facility_id=fid, equip=equip)
    return frame[Role.POWER].loc["2016"]


def _weekends(ctx) -> None:
    """Q1: the university buildings drop at weekends; the assembly building does not."""
    ratio = {}
    for run, eq in (("uva", "UVA_A"), ("uva", "UVA_B"), ("fox", AUDREY)):
        wd, we = weekday_weekend_profiles(_load(ctx, run, eq))
        ratio[eq] = we.max() / wd.max()
    assert ratio["UVA_A"] < 0.6 and ratio["UVA_B"] < 0.6, ratio
    assert ratio[AUDREY] > 0.9, ratio
    if ctx.mode == REAL:
        assert round(ratio["UVA_A"], 2) == 0.45, ratio


def _night_base(ctx) -> None:
    """Q2: out-of-hours load as a share of the weekday 07-18 load (baseload_anomaly)."""
    got = {
        eq: baseload_anomaly(_load(ctx, run, eq))
        for run, eq in (("uva", "UVA_A"), ("uva", "UVA_B"), ("fox", AUDREY), ("fox", STEPHEN_EL))
    }
    sev = {eq: r.severity for eq, r in got.items()}
    assert sev == {"UVA_A": "ok", "UVA_B": "ok", AUDREY: "fault", STEPHEN_EL: "fault"}, sev
    assert got[STEPHEN_EL].baseload_ratio > got[AUDREY].baseload_ratio, got
    if ctx.mode == REAL:
        want = {"UVA_A": 0.555, AUDREY: 0.897, STEPHEN_EL: 1.039}
        for eq, r in want.items():
            assert abs(got[eq].baseload_ratio - r) < 0.002, (eq, got[eq].baseload_ratio)


def _load_factor(ctx) -> None:
    """Q3: the lodging building's load is the flattest (highest load factor and base-to-peak)."""
    lodging = analyze_demand(_load(ctx, "fox", STEPHEN_EL))
    uni = analyze_demand(_load(ctx, "uva", "UVA_B"))
    assert lodging.load_factor > uni.load_factor, (lodging.load_factor, uni.load_factor)
    assert lodging.baseload_frac > 2 * uni.baseload_frac, (lodging, uni)
    if ctx.mode == REAL:
        assert (lodging.load_factor, uni.load_factor) == (0.589, 0.308), (lodging, uni)
        assert (lodging.baseload_frac, uni.baseload_frac) == (0.407, 0.085), (lodging, uni)


def _cooling_model(ctx) -> None:
    """Q4: the chilled-water meter's baseline is a cooling-only change-point model."""
    f = ctx.finding("mv_baseline", STEPHEN_CHW, "fox")
    assert f is not None and f.metrics["model"] == "3PC", f and f.metrics.get("model")


def standin(store) -> None:
    """ds-bdg2-fox and ds-valladolid-uva (see _practice_standins)."""
    bdg2_fox(store)
    valladolid(store)


EXERCISE = Exercise(
    id="data-energy-charting",
    title="Energy charting: load profiles, base load and weather dependence",
    issue=83,
    references=("pnnl-retuning-ch4", "pnnl-guide-occupancy-scheduling"),
    datasets=("bdg2", "valladolid-uva"),
    runs=(
        Run(dataset="bdg2", name="fox", facility="ds-bdg2-fox"),
        Run(dataset="valladolid-uva", name="uva"),
    ),
    commands=(
        "camber datasets fetch bdg2",
        "camber datasets fetch valladolid-uva",
        "camber datasets ingest bdg2 valladolid-uva --store lab_store",
        "camber datasets config bdg2 --facility ds-bdg2-fox --store lab_store --out fox.json",
        "camber run fox.json --out fox_out",
        "camber datasets config valladolid-uva --store lab_store --out uva.json",
        "camber run uva.json --out uva_out",
    ),
    expect=(
        Check("weekday vs weekend profiles", _weekends, quote="0.45"),
        Check("night and weekend base load", _night_base, quote="0.90"),
        Check("night base load, UVA_A", _night_base, on=REAL, quote="0.56"),
        Check("night base load, lodging", _night_base, on=REAL, quote="1.04"),
        Check("load factor and base-to-peak", _load_factor, quote="0.59"),
        Check("load factor, UVA_B", _load_factor, on=REAL, quote="0.31"),
        Check("base-to-peak, lodging", _load_factor, on=REAL, quote="0.41"),
        Check("base-to-peak, UVA_B", _load_factor, on=REAL, quote="0.085"),
        # weather dependence: the change-point baselines of the default configs
        Finding("mv_baseline", STEPHEN_CHW, severity=("ok",), run="fox"),
        Check("the chilled-water baseline is cooling-only (3PC)", _cooling_model),
        Finding("mv_baseline", STEPHEN_EL, severity=("info",), run="fox"),
        Finding("mv_baseline", "UVA_A", severity=("info",), run="uva"),
        Finding("mv_baseline", "UVA_B", severity=("info",), run="uva"),
        Metric("mv_baseline", STEPHEN_CHW, "r2", 0.86, 0.005, run="fox", on=REAL, quote="0.86"),
        Metric("mv_baseline", STEPHEN_EL, "r2", 0.32, 0.005, run="fox", on=REAL, quote="0.32"),
        Metric("mv_baseline", "UVA_A", "r2", 0.12, 0.005, run="uva", on=REAL, quote="0.12"),
        Metric("mv_baseline", "UVA_A", "cv_rmse", 0.261, 0.001, run="uva", on=REAL, quote="26.1%"),
    ),
    standin=standin,
)
