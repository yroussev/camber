"""Tests for the config's ``drift`` section (camber.config): validation, merge, and write policy."""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahusim import simulate_case  # noqa: E402
from camber.config import load_config, run_config, run_drift_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _token(role) -> str:
    value = role.value if isinstance(role, Role) else str(role)
    return "".join(p.capitalize() for p in value.split("_"))


def _make_site(root, **drift_overrides):
    """A two-AHU site (one drifting, one steady) plus a config with a ``drift`` section."""
    trends = os.path.join(root, "trends")
    os.makedirs(trends, exist_ok=True)
    aliases = {}
    for equip, case in (
        ("AHU_1", simulate_case("filter_loading", 4, seed=3)),
        ("AHU_2", simulate_case(None, 0, seed=9)),
    ):
        frame = pd.concat([case.baseline, case.current])
        for col in frame.columns:
            aliases[_token(col)] = col.value if isinstance(col, Role) else str(col)
            _write_point(trends, equip, _token(col), frame[col])

    drift = {
        "store": "baselines.json",
        "run_id": "R1",
        "baseline": ["2025-05-01", "2025-05-30"],
        "current": ["2025-06-01", "2025-07-01"],
        "families": [{"class": "AHU", "family": "ahu"}],
    }
    drift.update(drift_overrides)
    cfg = {
        "site": "CfgHQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {"aliases": aliases},
        "equipment": [{"class": "AHU", "marker": "Airflow"}],
        "drift": drift,
    }
    path = os.path.join(root, "config.json")
    open(path, "w").write(json.dumps(cfg))
    return path


def _freeze(path):
    """Establish the references the way ``camber drift freeze`` does."""
    from camber.config import drift_store_path
    from camber.store.modelstore import BaselineStore

    base = os.path.dirname(os.path.abspath(path))
    cfg = load_config(path)
    store = BaselineStore.load(drift_store_path(cfg, base_dir=base))
    run_drift_config(cfg, base_dir=base, freeze_if_missing=True, store=store)
    store.save()
    return store


def test_run_config_merges_drift_findings_and_records_the_family(tmp_path):
    path = _make_site(str(tmp_path))
    _freeze(path)

    res = run_config(load_config(path), base_dir=str(tmp_path))

    assert res.drift is not None
    assert "drift:AHU:ahu" in res.rules_run
    rules = {f.rule for f in res.findings}
    assert "filter_loading_drift" in rules
    # the drift findings are the same objects the DriftResult carries
    assert set(id(f) for f in res.drift.findings) <= set(id(f) for f in res.findings)


def test_run_config_never_writes_the_baseline_store(tmp_path):
    """A scoring run must not mint the reference it scores against."""
    path = _make_site(str(tmp_path))
    store_path = os.path.join(str(tmp_path), "baselines.json")

    run_config(load_config(path), base_dir=str(tmp_path))
    assert not os.path.exists(store_path)  # nothing frozen, nothing written

    _freeze(path)
    before = open(store_path).read()
    run_config(load_config(path), base_dir=str(tmp_path))
    assert open(store_path).read() == before


def test_per_family_window_overrides_the_top_level(tmp_path):
    path = _make_site(
        str(tmp_path),
        families=[
            {
                "class": "AHU",
                "family": "ahu",
                "baseline": ["2025-05-02", "2025-05-20"],
                "current": ["2025-06-02", "2025-06-20"],
            }
        ],
    )
    res = run_drift_config(load_config(path), base_dir=str(tmp_path))
    fam = res.families[0]
    assert fam.baseline == ("2025-05-02", "2025-05-20")
    assert fam.current == ("2025-06-02", "2025-06-20")


def test_family_class_must_be_in_the_equipment_list(tmp_path):
    path = _make_site(str(tmp_path), families=[{"class": "CH", "family": "chiller"}])
    with pytest.raises(ValueError, match="not in the config's equipment list"):
        run_drift_config(load_config(path), base_dir=str(tmp_path))


def test_unknown_family_name_raises(tmp_path):
    path = _make_site(str(tmp_path), families=[{"class": "AHU", "family": "boiler"}])
    with pytest.raises(KeyError, match="unknown drift family"):
        run_drift_config(load_config(path), base_dir=str(tmp_path))


def test_missing_store_is_a_named_error(tmp_path):
    path = _make_site(str(tmp_path))
    cfg = load_config(path)
    del cfg["drift"]["store"]
    with pytest.raises(ValueError, match="drift.store is required"):
        run_drift_config(cfg, base_dir=str(tmp_path))


@pytest.mark.parametrize("bad", ["2025-05-01", ["2025-05-01"], ["a", "b", "c"]])
def test_malformed_window_names_the_offending_key(tmp_path, bad):
    path = _make_site(str(tmp_path))
    cfg = load_config(path)
    cfg["drift"]["baseline"] = bad
    with pytest.raises(ValueError, match=r"drift\.baseline must be a \[start, end\] pair"):
        run_drift_config(cfg, base_dir=str(tmp_path))


def test_no_drift_section_returns_none(tmp_path):
    path = _make_site(str(tmp_path))
    cfg = load_config(path)
    del cfg["drift"]
    assert run_drift_config(cfg, base_dir=str(tmp_path)) is None
    assert run_config(cfg, base_dir=str(tmp_path)).drift is None


def test_html_report_carries_the_drift_tables_and_the_banner(tmp_path):
    path = _make_site(str(tmp_path))
    _freeze(path)
    cfg = load_config(path)
    cfg["report"] = {"level": 2, "out_html": "audit.html"}

    run_config(cfg, base_dir=str(tmp_path))
    html = open(os.path.join(str(tmp_path), "audit.html")).read()

    assert "AHU air-side drift diagnosis" in html
    assert "screening-grade" in html
    assert "provisional" in html
