"""Store-backed analysis: ParquetStore.drop_facility/equipment, StoreEquipRef + discover_store,
the ``source.kind == "store"`` config branch, and the report's data-source/licence block.

The load-bearing claim is parity: a config that reads equipment from a Parquet store produces the
same findings as the same data read from per-point CSV folders, through the unchanged
``Registry.run`` / ``run_periods`` / ``run_fleet`` and drift paths.
"""

import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import resolve as resolve_mod  # noqa: E402
from camber.ahusim import simulate_case  # noqa: E402
from camber.config import (  # noqa: E402
    data_sources,
    load_config,
    run_config,
    run_drift_config,
)
from camber.model.roles import Role  # noqa: E402
from camber.report.audit import (  # noqa: E402
    RESEARCH_ONLY_BANNER,
    AuditReport,
    data_sources_html,
    data_sources_text,
)
from camber.report.drift import drift_report_html  # noqa: E402
from camber.resolve import (  # noqa: E402
    EquipRef,
    StoreEquipRef,
    clear_store_cache,
    discover_store,
    resolve,
)
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "ds-test"


def _ahu_frame(*, fault=False, periods=24 * 14, freq="1h"):
    idx = pd.date_range("2025-07-07", periods=periods, freq=freq)
    weekday = idx.dayofweek < 5
    midday = (idx.hour >= 11) & (idx.hour < 15)
    heat = np.where(weekday & midday, 40.0, 0.0) if fault else np.zeros(len(idx))
    return pd.DataFrame(
        {
            Role.COOL_VALVE: 60.0,
            Role.HEAT_VALVE: heat,
            Role.MIXED_AIR_TEMP: 72.0,
            Role.SUPPLY_AIR_TEMP: 55.0,
            Role.OAT: 88.0,
            Role.SUPPLY_FAN_STATUS: np.where(idx.hour % 2 == 0, 1.0, 0.0),
            Role.WARMUP: np.where(idx.minute == 0, 0.0, 1.0),
        },
        index=idx,
    )


def _store(tmp_path, **meta):
    """Store fixture; ``meta`` is provenance, recorded under the namespaced "dataset" key."""
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(
        _ahu_frame(fault=True), facility_id=FID, equip="AHU__faulted", equip_class="AHU"
    )
    st.write_role_frame(_ahu_frame(), facility_id=FID, equip="AHU__fault_free", equip_class="AHU")
    wx = pd.DataFrame({Role.OAT: _ahu_frame()[Role.OAT] - 10.0})
    st.write_role_frame(wx, facility_id=FID, equip="weather", equip_class="WEATHER")
    st.register_facility(FID, name="Test dataset", **({"dataset": meta} if meta else {}))
    return st


# --------------------------------------------------------------------------- store


def test_equipment_is_cached_and_carries_the_ingest_class(tmp_path):
    st = _store(tmp_path)
    eq = st.equipment()
    assert eq == {FID: {"AHU__fault_free": "AHU", "AHU__faulted": "AHU", "weather": "WEATHER"}}
    assert "equipment" in json.load(open(os.path.join(st.root, "_catalog.json")))
    assert st.equipment(facility_id=FID) == eq
    assert st.equipment(facility_id="nope") == {}
    # a catalog written before the equipment cache existed is rebuilt once
    json.dump({"points": []}, open(os.path.join(st.root, "_catalog.json"), "w"))
    assert st.equipment() == eq
    assert ParquetStore(str(tmp_path / "missing")).equipment() == {}


def test_drop_facility_removes_rows_keeps_or_forgets_the_registry(tmp_path):
    st = _store(tmp_path)
    st.write_role_frame(_ahu_frame(), facility_id="other", equip="AHU_1", equip_class="AHU")
    n_before = len(st.read_long(facility_id=FID))
    assert st.drop_facility(FID) == n_before
    assert FID not in st.facilities() and "other" in st.facilities()
    assert st.read_long(facility_id=FID).empty
    assert FID in st.facilities_meta()  # registry kept by default
    assert set(st.equipment()) == {"other"}  # catalog invalidated + rebuilt
    assert st.drop_facility(FID, forget=True) == 0  # already gone: no-op on rows
    assert FID not in st.facilities_meta()
    with pytest.raises(ValueError):
        st.drop_facility("bad/id")


def test_write_after_drop_does_not_duplicate(tmp_path):
    st = _store(tmp_path)
    ref = discover_store(st, FID, "AHU")[1]
    assert ref.equip == "AHU__faulted"
    before = resolve(ref, None, (Role.COOL_VALVE,), resample=None)
    st.drop_facility(FID)
    st.write_role_frame(
        _ahu_frame(fault=True), facility_id=FID, equip="AHU__faulted", equip_class="AHU"
    )
    after = resolve(ref, None, (Role.COOL_VALVE,), resample=None)
    assert len(after) == len(before)


# --------------------------------------------------------------------------- resolve


def test_discover_store_by_class_marker_and_terminal(tmp_path):
    st = _store(tmp_path)
    st.write_role_frame(
        pd.DataFrame({Role.SPACE_TEMP: _ahu_frame()[Role.OAT]}),
        facility_id=FID,
        equip="VAV_1",
        equip_class="VAV",
    )
    ahus = discover_store(st.root, FID, "AHU", start="2025-07-08", end="2025-07-10")
    assert [r.equip for r in ahus] == ["AHU__fault_free", "AHU__faulted"]
    assert all(r.equip_class == "AHU" and r.start == "2025-07-08" for r in ahus)
    assert ahus[0].all_equips() == ("AHU__fault_free",)
    assert [r.equip for r in discover_store(st, FID, "TERMINAL")] == ["VAV_1"]
    assert [r.equip for r in discover_store(st, FID, marker_role=Role.SPACE_TEMP)] == ["VAV_1"]
    assert [r.equip for r in discover_store(st, FID, marker_role="oat")] == [
        "AHU__fault_free",
        "AHU__faulted",
        "weather",
    ]
    assert len(discover_store(st, FID)) == 4
    assert discover_store(st, "ghost", "AHU") == []


def test_resolve_store_subsets_resamples_and_windows(tmp_path):
    st = _store(tmp_path)
    ref = discover_store(st, FID, "AHU", start="2025-07-08", end="2025-07-09 23:00")[1]
    frame = resolve(ref, None, (Role.COOL_VALVE, Role.SUPPLY_FAN_STATUS, Role.WARMUP, Role.CO2))
    assert set(frame.columns) == {Role.COOL_VALVE, Role.SUPPLY_FAN_STATUS, Role.WARMUP}
    assert frame.index.min() == pd.Timestamp("2025-07-08")
    assert frame.index.max() == pd.Timestamp("2025-07-09 23:00")
    daily = resolve(ref, None, (Role.SUPPLY_FAN_STATUS, Role.WARMUP), resample="1D")
    assert daily[Role.SUPPLY_FAN_STATUS].iloc[0] == pytest.approx(0.5)  # status -> mean (duty)
    assert daily[Role.WARMUP].iloc[0] == 0.0  # any-on role -> max of an all-zero day
    assert resolve(ref, None, (Role.CO2,)).empty  # role not stored
    ghost = StoreEquipRef("nope", "AHU", FID, st.root)
    assert resolve(ghost, None, (Role.OAT,)).empty


def test_resolve_store_normalizes_fraction_valves(tmp_path):
    st = ParquetStore(str(tmp_path / "s"))
    f = _ahu_frame()
    f[Role.COOL_VALVE] = 0.6
    st.write_role_frame(f, facility_id=FID, equip="AHU_1", equip_class="AHU")
    ref = discover_store(st, FID, "AHU")[0]
    assert resolve(ref, None, (Role.COOL_VALVE,))[Role.COOL_VALVE].max() == pytest.approx(60.0)


def test_folder_ref_still_needs_a_mapping(tmp_path):
    with pytest.raises(ValueError):
        resolve(EquipRef("AHU_1", "AHU", str(tmp_path)), None, (Role.OAT,))


def test_resolve_cache_hits_and_invalidates_on_write_and_drop(tmp_path, monkeypatch):
    st = _store(tmp_path)
    clear_store_cache()
    calls = []
    real = ParquetStore.read_role_frame

    def counting(self, **kw):
        calls.append(kw["equip"])
        return real(self, **kw)

    monkeypatch.setattr(ParquetStore, "read_role_frame", counting)
    ref = discover_store(st, FID, "AHU")[0]
    resolve(ref, None, (Role.OAT,))
    resolve(ref, None, (Role.COOL_VALVE,))
    assert calls == [ref.equip]  # second call served from the cache
    # a new write into a new year partition changes the facility signature
    later = _ahu_frame()
    later.index = later.index + pd.DateOffset(years=1)
    st.write_role_frame(later, facility_id=FID, equip=ref.equip, equip_class="AHU")
    resolve(ref, None, (Role.OAT,))
    assert len(calls) == 2
    assert clear_store_cache(st.root, FID) >= 1
    assert clear_store_cache() == 0
    # small cache bound evicts oldest
    monkeypatch.setattr(resolve_mod, "_STORE_CACHE_MAX", 1)
    for r in discover_store(st, FID):
        resolve(r, None, (Role.OAT,))
    assert len(resolve_mod._STORE_CACHE) == 1


def test_registry_paths_accept_store_refs(tmp_path):
    st = _store(tmp_path)
    refs = discover_store(st, FID, "AHU")
    reg = builtin_registry()
    found = {f.equip: f for f in reg.run("simultaneous_heat_cool", refs, None)}
    assert found["AHU__faulted"].severity == "fault"
    assert found["AHU__fault_free"].severity != "fault"
    # run_periods (PeriodRule) is exercised through the drift-parity test below
    shared = {Role.OAT: resolve(discover_store(st, FID, "WEATHER")[0], None, (Role.OAT,))[Role.OAT]}
    fleet = reg.run_fleet("damper_census", refs, None, shared=shared)
    assert fleet is None or fleet.rule == "damper_census"


# --------------------------------------------------------------------------- config


_PROV = {
    "dataset_id": "test-ds",
    "title": "A test dataset",
    "publisher": "Test Lab",
    "licence": "CC-BY-4.0",
    "access": "open",
    "citation": "Test Lab (2025). A test dataset.",
    "dois": ["10.0000/test"],
    "landing_url": "https://example.org/ds",
}


def _store_cfg(tmp_path, **extra):
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU", "marker_role": "cool_valve"}],
        "shared_oat": {"equip": "weather", "role": "oat"},
        "rules": ["simultaneous_heat_cool", "outdoor_air_fraction"],
        "report": {"level": 2, "out_text": "audit.txt", "out_html": "audit.html"},
    }
    cfg.update(extra)
    return cfg


def test_run_config_on_a_store_source(tmp_path):
    _store(tmp_path, **_PROV)
    res = run_config(_store_cfg(tmp_path), base_dir=str(tmp_path))
    assert res.site == "Test dataset"  # display name from the registry
    assert res.equipment == 2
    faults = {f.equip for f in res.findings if f.severity == "fault"}
    assert faults == {"AHU__faulted"}
    assert res.report.data_sources[0]["licence"] == "CC-BY-4.0"
    assert res.report.data_sources[0]["facility_id"] == FID
    txt = open(tmp_path / "audit.txt").read()
    html = open(tmp_path / "audit.html").read()
    for body in (txt, html):
        assert "Data source" in body and "CC-BY-4.0" in body and "Test Lab (2025)" in body
    assert RESEARCH_ONLY_BANNER not in html
    assert data_sources(_store_cfg(tmp_path), base_dir=str(tmp_path))[0]["dois"] == ["10.0000/test"]


def test_store_config_research_only_banner_and_share_alike(tmp_path):
    _store(tmp_path, **{**_PROV, "licence": "CC-BY-NC-ND-4.0", "access": "research_only"})
    res = run_config(_store_cfg(tmp_path), base_dir=str(tmp_path))
    assert RESEARCH_ONLY_BANNER in res.report.to_html()
    assert RESEARCH_ONLY_BANNER in res.report.to_text()
    sa = data_sources_text([{**_PROV, "licence": "CC-BY-SA-4.0"}])
    assert "Share-alike" in sa
    assert "Share-alike" in data_sources_html([{**_PROV, "licence": "CC-BY-SA-4.0"}])
    assert data_sources_text([]) == "" and data_sources_html(None) == ""
    assert "<li><b>x</b></li>" in data_sources_html([{"dataset_id": "x"}])


def test_store_config_without_provenance_and_shared_oat_file(tmp_path):
    _store(tmp_path)  # no provenance recorded
    oat = tmp_path / "oat.csv"
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    pd.DataFrame(
        {"Timestamp": idx.strftime("%d-%b-%y %I:%M:%S %p") + " PDT", "Value": 70.0}
    ).to_csv(oat, index=False)
    cfg = _store_cfg(tmp_path, shared_oat={"file": "oat.csv"}, site="Named")
    cfg["mapping"] = {"aliases": {"oat": "oat"}}
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.site == "Named" and res.report.data_sources == []
    assert data_sources({"source": {"kind": "perpoint_csv"}}) == []


def test_store_config_errors(tmp_path):
    _store(tmp_path)
    with pytest.raises(ValueError, match="facility_id"):
        run_config({"source": {"kind": "store", "store": "store"}}, base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="no data"):
        run_config(
            {"source": {"kind": "store", "store": "store", "facility_id": "ghost"}},
            base_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="shared_oat"):
        run_config(_store_cfg(tmp_path, shared_oat={"equip": "nope"}), base_dir=str(tmp_path))


def test_unknown_source_kind_warns_and_reads_folders(tmp_path):
    folder = tmp_path / "trends"
    folder.mkdir()
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    ts = idx.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": 60.0}).to_csv(
        folder / "AHU_1_CHW_Valve.csv", index=False
    )
    cfg = {
        "source": {"kind": "sql_warehouse", "folder": "trends"},
        "mapping": {"aliases": {"CHW_Valve": "cool_valve"}},
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": [],
    }
    with pytest.warns(UserWarning, match="sql_warehouse"):
        res = run_config(cfg, base_dir=str(tmp_path))
    assert res.equipment == 1
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg["source"]["kind"] = "perpoint_csv"
        assert run_config(cfg, base_dir=str(tmp_path)).equipment == 1
    del cfg["mapping"]
    with pytest.raises(KeyError):
        run_config(cfg, base_dir=str(tmp_path))


# --------------------------------------------------------------------------- drift parity


def _token(role) -> str:
    return "".join(p.capitalize() for p in role.value.split("_"))


def _drift_sites(root):
    """The same two-AHU drift data as a folder config and as a store config."""
    trends = os.path.join(root, "trends")
    os.makedirs(trends, exist_ok=True)
    st = ParquetStore(os.path.join(root, "store"))
    aliases = {}
    for equip, case in (
        ("AHU_1", simulate_case("filter_loading", 4, seed=3)),
        ("AHU_2", simulate_case(None, 0, seed=9)),
    ):
        frame = pd.concat([case.baseline, case.current])
        st.write_role_frame(frame, facility_id=FID, equip=equip, equip_class="AHU")
        for col in frame.columns:
            aliases[_token(col)] = col.value
            ts = frame.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
            pd.DataFrame({"Timestamp": ts, "Value": frame[col].values}).to_csv(
                os.path.join(trends, f"{equip}_{_token(col)}.csv"), index=False
            )
    st.register_facility(FID, name="Drift test", dataset=_PROV)
    drift = {
        "baseline": ["2025-05-01", "2025-05-30"],
        "current": ["2025-06-01", "2025-07-01"],
        "families": [{"class": "AHU", "family": "ahu"}],
    }
    folder = {
        "site": "S",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {"aliases": aliases},
        "equipment": [{"class": "AHU", "marker": "Airflow"}],
        "drift": {**drift, "store": "folder_baselines.json"},
    }
    store = {
        "site": "S",
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU", "marker_role": "airflow"}],
        "drift": {**drift, "store": "store_baselines.json"},
    }
    return folder, store


def _freeze_and_run(cfg, root):
    from camber.config import drift_store_path
    from camber.store.modelstore import BaselineStore

    bs = BaselineStore.load(drift_store_path(cfg, base_dir=root))
    run_drift_config(cfg, base_dir=root, freeze_if_missing=True, store=bs)
    bs.save()
    return run_drift_config(cfg, base_dir=root)


def test_drift_freeze_and_run_match_between_folder_and_store(tmp_path):
    root = str(tmp_path)
    folder_cfg, store_cfg = _drift_sites(root)
    a = _freeze_and_run(folder_cfg, root)
    b = _freeze_and_run(store_cfg, root)
    sev = lambda r: sorted((f.equip, f.rule, f.severity) for f in r.findings)  # noqa: E731
    assert sev(a) == sev(b)
    assert any(f.severity in ("warn", "fault") for f in b.findings)
    html = drift_report_html(b, data_sources=data_sources(store_cfg, base_dir=root))
    assert "Data source" in html and "CC-BY-4.0" in html


def test_cli_drift_report_and_report_carry_the_licence(tmp_path):
    from camber.cli import main

    root = str(tmp_path)
    _, store_cfg = _drift_sites(root)
    path = os.path.join(root, "cfg.json")
    json.dump(store_cfg, open(path, "w"))
    _freeze_and_run(load_config(path), root)
    out = os.path.join(root, "drift.html")
    assert main(["drift", "report", path, "--out", out]) == 0
    assert "Test Lab (2025)" in open(out).read()
    rep = os.path.join(root, "rep.html")
    assert main(["report", path, "--out", rep]) == 0
    assert "Data source" in open(rep).read()


def test_audit_report_default_has_no_sources():
    assert AuditReport(building="x", level=1).data_sources == []


# --------------------------------------------------------------------------- mv section


def test_mv_declines_without_data_or_weather(tmp_path, monkeypatch):
    st = ParquetStore(str(tmp_path / "s"))
    idx = pd.date_range("2016-01-01", periods=24 * 10, freq="1h")
    st.write_role_frame(
        pd.DataFrame({Role.POWER: 1.0}, index=idx), facility_id="f", equip="m1", equip_class="M"
    )
    st.write_role_frame(
        pd.DataFrame({Role.POWER: 1.0, Role.OAT: 50.0}, index=idx),
        facility_id="f",
        equip="m2",
        equip_class="M",
    )
    st.write_role_frame(
        pd.DataFrame({Role.OAT: 50.0}, index=idx), facility_id="f", equip="m3", equip_class="M"
    )
    cfg = {
        "source": {"kind": "store", "store": st.root, "facility_id": "f"},
        "equipment": [{"class": "M"}],
        "mv": [{"class": "M", "role": "power"}],
    }
    out = {f.equip: f for f in run_config(cfg).findings}
    assert "outdoor temperature" in out["m1"].summary
    assert "usable days" in out["m2"].summary
    assert "no power data" in out["m3"].summary
    cfg["mv"][0]["interval"] = "hourly"
    with pytest.raises(ValueError, match="daily"):
        run_config(cfg)


def _mv_store(tmp_path, *, saving=0.15):
    """One meter over a year: cooling energy above 65 F, a ``saving`` from 2016-07-01 on."""
    st = ParquetStore(str(tmp_path / "mvs"))
    idx = pd.date_range("2016-01-01", "2016-12-31 23:00", freq="1h")
    rng = np.random.default_rng(21)
    doy = idx.dayofyear.to_numpy()
    oat = (
        55
        - 25 * np.cos((doy - 15) / 365 * 2 * np.pi)
        + 8 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
        + rng.normal(0, 2, len(idx))
    )
    rate = 20 + 3.0 * np.maximum(0, oat - 60) + rng.normal(0, 1.5, len(idx))
    rate = np.where(idx >= "2016-07-01", rate * (1 - saving), rate)
    st.write_role_frame(
        pd.DataFrame({Role.POWER: rate, Role.OAT: oat}, index=idx),
        facility_id="f",
        equip="meter",
        equip_class="M",
    )
    return {
        "source": {"kind": "store", "store": st.root, "facility_id": "f"},
        "equipment": [{"class": "M"}],
        "mv": [{"class": "M", "role": "power"}],
    }


def test_mv_baseline_reports_its_oat_support(tmp_path):
    cfg = _mv_store(tmp_path)
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-06-30"]
    (f,) = [f for f in run_config(cfg).findings if f.rule == "mv_baseline"]
    m = f.metrics
    assert m["oat_fit_min"] <= m["oat_support_lo"] < m["oat_support_hi"] <= m["oat_fit_max"]
    assert 20 < m["oat_fit_min"] < 40 and 70 < m["oat_fit_max"] < 90
    assert m["rho"] is not None  # time_index passed: rho is estimated
    assert not [x for x in run_config(cfg).findings if x.rule == "mv_savings"]
    json.dumps(f.metrics, allow_nan=False)


def test_mv_reporting_period_emits_a_savings_finding(tmp_path):
    cfg = _mv_store(tmp_path)
    # the first half-year spans cold to warm; a mild autumn window sits inside it
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-06-30"]
    cfg["mv"][0]["reporting_period"] = ["2016-09-15", "2016-11-30"]
    out = run_config(cfg)
    (s,) = [f for f in out.findings if f.rule == "mv_savings"]
    m = s.metrics
    assert s.severity == "info" and m["declined"] is False
    for k in ("avoided_energy", "savings_pct", "fsu", "fsu_extrapolation_factor", "coverage_tier"):
        assert k in m
    assert m["coverage_tier"] in ("in_range", "moderate")
    assert 0.05 < m["savings_pct"] < 0.25 and m["fsu"] > 0
    assert m["reporting_period"] == ["2016-09-15", "2016-11-30"]
    assert "avoided energy" in s.summary
    json.dumps(m, allow_nan=False)


def test_mv_q1_baseline_against_q3_declines(tmp_path):
    cfg = _mv_store(tmp_path)
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-03-31"]
    cfg["mv"][0]["reporting_period"] = ["2016-07-01", "2016-09-30"]
    cfg["mv"][0]["min_days"] = 30
    (s,) = [f for f in run_config(cfg).findings if f.rule == "mv_savings"]
    assert s.severity == "info"
    assert s.metrics["declined"] is True and "SEVERE" in s.metrics["declined_reason"]
    assert s.metrics["avoided_energy"] is None and s.metrics["reporting_actual"] > 0
    assert s.metrics["coverage_tier"] == "severe"
    assert any("SEVERE extrapolation" in c for c in s.caveats)
    assert "declined" in s.summary
    # the policy is tunable from config: opting out computes it anyway, under the caveat
    cfg["mv"][0]["extrapolation"] = {"decline": False}
    (s2,) = [f for f in run_config(cfg).findings if f.rule == "mv_savings"]
    assert s2.metrics["declined"] is False and s2.metrics["avoided_energy"] is not None
    cfg["mv"][0]["extrapolation"] = {"nope": 1}
    with pytest.raises(ValueError, match="unknown extrapolation"):
        run_config(cfg)


def test_mv_savings_declines_without_baseline_or_reporting_data(tmp_path):
    cfg = _mv_store(tmp_path)
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-01-10"]  # too short for a baseline
    cfg["mv"][0]["reporting_period"] = ["2016-07-01", "2016-09-30"]
    by_rule = {f.rule: f for f in run_config(cfg).findings}
    assert by_rule["mv_savings"].metrics["declined"] is True
    assert "no baseline" in by_rule["mv_savings"].summary
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-06-30"]
    cfg["mv"][0]["reporting_period"] = ["2018-01-01", "2018-02-01"]  # no data there
    by_rule = {f.rule: f for f in run_config(cfg).findings}
    assert "no usable reporting-period days" in by_rule["mv_savings"].summary
    cfg["mv"][0]["reporting_period"] = ["2016-07-01"]
    with pytest.raises(ValueError, match="reporting_period"):
        run_config(cfg)
