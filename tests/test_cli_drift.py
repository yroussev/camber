"""Tests for the ``camber drift`` subcommands: run / report / freeze / list.

The write policy is the thing under test as much as the output: ``run`` and ``report`` must leave
the baseline store byte-identical, and ``freeze`` must never overwrite an existing reference.
"""

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahusim import simulate_case  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _token(role) -> str:
    value = role.value if isinstance(role, Role) else str(role)
    return "".join(p.capitalize() for p in value.split("_"))


def _make_site(root):
    """A drifting AHU, a steady one, and one carrying only the marker point."""
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
    healthy = simulate_case(None, 0, seed=1)
    idx = pd.concat([healthy.baseline, healthy.current]).index
    _write_point(trends, "AHU_3", "Airflow", pd.Series(9000.0, index=idx))
    aliases["Airflow"] = "airflow"

    cfg = {
        "site": "CliHQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {"aliases": aliases},
        "equipment": [{"class": "AHU", "marker": "Airflow"}],
        "drift": {
            "store": "baselines.json",
            "baseline": ["2025-05-01", "2025-05-30"],
            "current": ["2025-06-01", "2025-07-01"],
            "families": [{"class": "AHU", "family": "ahu"}],
        },
    }
    path = os.path.join(root, "config.json")
    open(path, "w").write(json.dumps(cfg))
    return path, os.path.join(root, "baselines.json")


def test_freeze_creates_then_leaves_the_store_alone(tmp_path, capsys):
    cfg, store = _make_site(str(tmp_path))

    assert main(["drift", "freeze", cfg, "--run-id", "2025-06-01"]) == 0
    out = capsys.readouterr().out
    assert "froze 10 new baseline(s)" in out
    assert os.path.exists(store)
    first = open(store).read()

    assert main(["drift", "freeze", cfg, "--run-id", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    assert "froze 0 new baseline(s); 10 already frozen (left untouched)" in out
    assert open(store).read() == first  # a second freeze never moves a reference


def test_freeze_dry_run_writes_nothing(tmp_path, capsys):
    cfg, store = _make_site(str(tmp_path))
    assert main(["drift", "freeze", cfg, "--dry-run"]) == 0
    assert "dry run: would freeze 10 new baseline(s)" in capsys.readouterr().out
    assert not os.path.exists(store)


def test_run_scores_drift_and_never_writes_the_store(tmp_path, capsys):
    cfg, store = _make_site(str(tmp_path))
    main(["drift", "freeze", cfg, "--run-id", "2025-06-01"])
    capsys.readouterr()
    before = open(store).read()

    out_dir = os.path.join(str(tmp_path), "out")
    assert main(["drift", "run", cfg, "--out", out_dir]) == 0
    out = capsys.readouterr().out

    assert "[fault]" in out and "AHU_1" in out
    assert "screening-grade" in out and "provisional" in out  # the banner cannot be suppressed
    assert open(store).read() == before

    payload = json.load(open(os.path.join(out_dir, "drift.json")))
    assert payload["threshold_confidence"]["magnitude_threshold_confidence"] == "screening-grade"
    assert os.path.exists(os.path.join(out_dir, "findings.json"))


def test_run_reports_untested_equipment_rather_than_calling_it_steady(tmp_path, capsys):
    cfg, _ = _make_site(str(tmp_path))
    main(["drift", "freeze", cfg])
    capsys.readouterr()
    main(["drift", "run", cfg])
    out = capsys.readouterr().out

    line = next(ln for ln in out.splitlines() if "AHU_3" in ln)
    assert "not evaluated" in line and "no required role resolved" in line
    assert "steady" not in line


def test_run_before_freeze_claims_nothing(tmp_path, capsys):
    """Every detector declines with no frozen baseline — no equipment may be reported ok."""
    cfg, _ = _make_site(str(tmp_path))
    assert main(["drift", "run", cfg]) == 0
    out = capsys.readouterr().out

    assert "no equipment produced a verdict" in out
    assert "[ok" not in out
    assert out.count("every detector declined") == 2


def test_report_writes_html_with_the_banner_and_the_unevaluated_table(tmp_path, capsys):
    cfg, _ = _make_site(str(tmp_path))
    main(["drift", "freeze", cfg])
    dest = os.path.join(str(tmp_path), "drift.html")

    assert main(["drift", "report", cfg, "--out", dest]) == 0
    html = open(dest).read()

    assert "AHU air-side drift diagnosis" in html
    assert "screening-grade" in html
    assert "Equipment not evaluated" in html and "AHU_3" in html


def test_list_shows_provenance_and_filters(tmp_path, capsys):
    cfg, _ = _make_site(str(tmp_path))
    main(["drift", "freeze", cfg, "--run-id", "2025-06-01"])
    capsys.readouterr()

    assert main(["drift", "list", cfg]) == 0
    out = capsys.readouterr().out
    assert "AHU_1" in out and "AHU_2" in out and "frozen_at=2025-06-01" in out

    dest = os.path.join(str(tmp_path), "recs.json")
    assert main(["drift", "list", cfg, "--equip", "AHU_1", "--json", dest]) == 0
    out = capsys.readouterr().out
    assert "AHU_2" not in out
    recs = json.load(open(dest))
    assert {r["equip"] for r in recs} == {"AHU_1"}
    assert {r["kind"] for r in recs} == {
        "fan_efficiency",
        "filter_loading",
        "duct_static",
        "economizer_damper",
        "coil_valve_cool",
    }


def test_list_before_freeze_says_so(tmp_path, capsys):
    cfg, _ = _make_site(str(tmp_path))
    assert main(["drift", "list", cfg]) == 0
    assert "no frozen baselines" in capsys.readouterr().out
