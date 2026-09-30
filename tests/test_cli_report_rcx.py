"""`camber report --layout`: the rcx layout end to end, plugin layouts, and the config's
equipment-name filter used to report on one scenario of a dataset."""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def test_cli_report_layout_rcx_end_to_end(tmp_path, capsys):
    fx.make_store(tmp_path)
    cfg = fx.config()
    cfg["report"]["layout"] = "audit"  # the flag overrides the config
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    notes = tmp_path / "n.json"
    notes.write_text(json.dumps({"issue:0000": "orphan"}))
    out, tmpl = tmp_path / "r.html", tmp_path / "t.json"
    rc = main(["report", str(path), "--out", str(out), "--layout", "rcx", "--week", "oat-range",
               "--paper", "a4", "--notes", str(notes), "--notes-template", str(tmpl)])  # fmt: skip
    assert rc == 0
    txt = capsys.readouterr().out
    assert "rcx:" in txt and "by oat-range" in txt and "matched no slot" in txt
    html = out.read_text()
    assert "size:A4" in html and "Appendix E" in html and "orphan" in html
    slots = json.loads(tmpl.read_text())
    assert "exec_summary" in slots and any(k.startswith("issue:") for k in slots)
    # the config's layout is used when no flag is given; the default stays audit
    cfg["report"]["layout"] = "rcx"
    path.write_text(json.dumps(cfg))
    assert main(["report", str(path), "--out", str(out)]) == 0
    assert "Retro-commissioning report" in out.read_text()
    del cfg["report"]["layout"]
    path.write_text(json.dumps(cfg))
    assert main(["report", str(path), "--out", str(out)]) == 0
    # the audit layout; titled neutrally (0.96, #78) as the config gives no benchmark / ECMs
    audit = out.read_text()
    assert "Building analytics report" in audit and "Retro-commissioning" not in audit
    assert audit.startswith("<!doctype html>") and "name='viewport'" in audit


def test_cli_unknown_layout_and_plugin_layout(tmp_path, capsys, monkeypatch):
    from camber import plugins

    fx.make_store(tmp_path)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(fx.config()))
    out = tmp_path / "r.html"
    assert main(["report", str(path), "--out", str(out), "--layout", "nope"]) == 2
    assert "unknown report layout 'nope'" in capsys.readouterr().err

    class Plain:
        def to_html(self, res):
            return f"<p>{len(res.findings)} findings</p>"

    real = plugins.PluginRegistry.load_entrypoints

    def fake(self, **kw):
        real(self, **kw)
        self.register("reports", Plain(), name="plain")
        self.register("reports", lambda res: "<p>callable</p>", name="fn")
        return self

    monkeypatch.setattr(plugins.PluginRegistry, "load_entrypoints", fake)
    assert main(["report", str(path), "--out", str(out), "--layout", "plain"]) == 0
    assert out.read_text().endswith("findings</p>")
    assert main(["report", str(path), "--out", str(out), "--layout", "fn"]) == 0
    assert out.read_text() == "<p>callable</p>"


def test_equipment_entry_can_name_the_equipment_to_keep(tmp_path):
    st = fx.make_store(tmp_path)
    st.write_role_frame(
        pd.DataFrame(
            {Role.OAT: np.arange(10.0)}, index=pd.date_range(fx.START, periods=10, freq="h")
        ),
        facility_id=fx.FID,
        equip="OtherAHU",
        equip_class="AHU",
    )
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": fx.FID},
        "equipment": [{"class": "AHU", "equip": ["DemoAHU"]}],
        "rules": [],
    }
    assert [r.equip for r in run_config(cfg, base_dir=str(tmp_path)).refs] == ["DemoAHU"]
    cfg["equipment"] = [{"class": "AHU", "equip": "OtherAHU"}]
    assert [r.equip for r in run_config(cfg, base_dir=str(tmp_path)).refs] == ["OtherAHU"]
    cfg["equipment"] = [{"class": "AHU"}]
    assert len(run_config(cfg, base_dir=str(tmp_path)).refs) == 3


def test_cli_rcx_reads_notes_named_in_the_config_relative_to_it(tmp_path, capsys):
    fx.make_store(tmp_path)
    cfg = fx.config(notes="notes.json")
    (tmp_path / "notes.json").write_text(json.dumps({"exec_summary": "Seen from the config."}))
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    out = tmp_path / "r.html"
    assert main(["report", str(path), "--out", str(out), "--layout", "rcx", "--lifecycle"]) == 0
    assert "Seen from the config." in out.read_text()
