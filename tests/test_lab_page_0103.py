"""0.103 (#123, #125): the lab page and report gaps found while writing docs/LAB.md.

Covered: a port already in use (a clear message and exit 1, no traceback); the disk check (the
download with the fetch's own 5 % headroom -- checked against :func:`fetch_dataset`'s refusal at
the boundary byte --, archive extraction, the store estimate, and "Ingest (already fetched)"
against the store); a missing optional extra blocking a fetch before any download; the remove,
ingest-from-a-folder and force routes, with their auth / CSRF / JSON / allowlist rejections, path
validation and the workspace lifecycle; the audit report's evidence charts (CLI and lab) and
``compressor_short_cycle``'s Learn-more link and recommended action.

No network: the fake catalog entries of tests/test_lab.py, served by an injected opener.
"""

import collections
import hashlib
import json
import math
import os
import shutil
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402
from test_lab import PORT, _app, _cookie, _wait, get, post  # noqa: E402

from camber import cli  # noqa: E402
from camber.datasets import _ops, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.datasets._fetch import DISK_MARGIN, InsufficientSpace  # noqa: E402
from camber.lab import (  # noqa: E402
    TOKEN_HEADER,
    LabApp,
    PortInUse,
    _checks,
    dispatch_lab,
    make_lab_server,
)
from camber.portfolio import Portfolio  # noqa: E402

_Usage = collections.namedtuple("_Usage", "total used free")


def _free(monkeypatch, free: int):
    """Every disk-space query (the lab's and the fetch's) sees ``free`` bytes."""
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(10**15, 0, int(free)))


def _entry(**over):
    zb = _zip_bytes()
    d = _ahu_entry_dict(zb)
    d.update(over)
    return DatasetEntry.from_dict(d), zb


def _lab(tmp_path, entries, zb, **kw):
    if "workspace" not in kw:
        kw.setdefault("store", str(tmp_path / "store"))
    app = LabApp(
        data_dir=str(tmp_path / "cache"),
        entries=entries,
        opener=FakeOpener({BASE + "test.zip": zb}),
        **kw,
    )
    app.bind(PORT)
    return app


def _row(app, did="test-ahu"):
    return {d["id"]: d for d in get(app, "/lab/catalog")[1]["datasets"]}[did]


# --------------------------------------------------------------------------- port in use


def test_port_in_use_is_a_clear_error(tmp_path, capsys, monkeypatch):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        app = _app(tmp_path)
        try:
            with pytest.raises(PortInUse) as exc:
                make_lab_server(app, port=port)
            assert isinstance(exc.value, OSError) and f"--port {port + 1}" in str(exc.value)
            assert "Errno" not in str(exc.value)
        finally:
            app.close()
        monkeypatch.chdir(tmp_path)
        rc = cli.main(["lab", "--store", str(tmp_path / "st"), "--port", str(port)])
        err = capsys.readouterr().err
        assert rc == 1
        assert f"port {port} on 127.0.0.1 is already in use" in err and "--port" in err
        assert "Traceback" not in err and "Errno" not in err
    finally:
        sock.close()


# --------------------------------------------------------------------------- disk check


def test_page_and_server_disk_check_agree_with_the_fetchs_own_refusal(tmp_path, monkeypatch):
    entry, zb = _entry()
    app = _lab(tmp_path, [entry], zb)
    try:
        row = _row(app)
        body = get(app, "/lab/catalog")[1]
        assert body["disk_margin"] == DISK_MARGIN and body["same_disk"] is True
        need = row["subsets"]["default"]["needs"]
        assert need["download"] == entry.download_bytes() == len(zb)
        assert need["store"] == 1_000_000
        edge = math.ceil(len(zb) * (1 + DISK_MARGIN))  # the fetch refuses free < size * 1.05

        _free(monkeypatch, edge - 1)
        with pytest.raises(InsufficientSpace):  # the fetch's own refusal ...
            _ops.fetch_dataset(entry, data_dir=str(tmp_path / "probe"), opener=FakeOpener({}))
        status, out, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]})  # ... and the lab's
        assert status == 507 and "not enough disk space" in out["error"]

        _free(monkeypatch, edge)  # one byte more: the fetch goes ahead, and so does the lab
        status, out, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]})
        assert status == 202
        assert _wait(app, out["job"]["id"])["state"] == "done"
        assert os.path.isfile(_ops._dest(str(tmp_path / "cache"), entry, "test.zip"))
    finally:
        app.close()


def test_fetch_and_ingest_counts_the_store_and_ingest_checks_the_store(tmp_path, monkeypatch):
    entry, zb = _entry()
    app = _lab(tmp_path, [entry], zb)
    try:
        edge = math.ceil(len(zb) * (1 + DISK_MARGIN))
        _free(monkeypatch, edge)  # room for the download, not for the store
        status, out, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
        assert status == 507 and "store" in out["error"]
        status, out, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]})
        assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"
        # fetched: "Ingest (already fetched)" is checked against the store too
        status, out, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})
        assert status == 507 and "store" in out["error"]
        _free(monkeypatch, 10**12)
        status, out, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})
        assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"
    finally:
        app.close()


def test_separate_disks_are_checked_separately(tmp_path, monkeypatch):
    entry, zb = _entry()
    app = _lab(tmp_path, [entry], zb)
    try:
        monkeypatch.setattr(_checks, "same_disk", lambda a, b: False)
        cache = str(tmp_path / "cache")
        os.makedirs(cache, exist_ok=True)

        def usage(p):
            free = 10**12 if os.path.abspath(p).startswith(cache) else 1000
            return _Usage(10**15, 0, free)

        monkeypatch.setattr(shutil, "disk_usage", usage)
        plan = _checks.disk_plan(
            [entry], "default", data_dir=cache, store=app.store.root, download=True
        )
        assert not plan["ok"] and len(plan["problems"]) == 1
        assert plan["problems"][0].startswith("the store needs")
        assert [loc["label"] for loc in plan["locations"]] == ["the dataset cache", "the store"]
        assert plan["locations"][1]["need"] == math.ceil(1_000_000 * (1 + DISK_MARGIN))
    finally:
        app.close()


def test_archive_extraction_is_counted_before_and_after_the_download(tmp_path, monkeypatch):
    import zipfile

    zb = _zip_bytes()
    d = _ahu_entry_dict(zb)
    with zipfile.ZipFile(__import__("io").BytesIO(zb)) as z:
        sizes = {i.filename: i.file_size for i in z.infolist()}
    members = d["files"][0]["members"]
    d["files"][0]["extracted_size"] = sum(sizes[m] for m in members)
    entry = DatasetEntry.from_dict(d)
    cache = str(tmp_path / "cache")
    # before the download: the catalog's extracted_size, for the 2 of 3 members the subset reads
    before = _checks.pending_extract(entry, "default", cache)
    assert before == math.ceil(d["files"][0]["extracted_size"] * 2 / 3)
    assert _checks.pending_extract(entry, "full", cache) == d["files"][0]["extracted_size"]
    _ops.fetch_dataset(entry, data_dir=cache, opener=FakeOpener({BASE + "test.zip": zb}))
    # after it: exact, from the zip's own directory (what ingest checks before it extracts)
    exact = sizes[members[0]] + sizes[members[1]]
    assert _checks.pending_extract(entry, "default", cache) == exact
    # the extraction is part of a fetch-and-ingest's need, with the headroom
    _free(monkeypatch, 10**12)
    plan = _checks.disk_plan([entry], "default", data_dir=cache, store=cache, download=True)
    assert plan["locations"][0]["need"] == math.ceil((exact + 1_000_000) * (1 + DISK_MARGIN))
    app = _lab(tmp_path, [entry], zb)
    try:
        assert _row(app)["subsets"]["default"]["needs"]["extract"] == exact
        _free(monkeypatch, math.ceil((exact + 1_000_000) * (1 + DISK_MARGIN)) - 1)
        status, out, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})
        assert status == 507
    finally:
        app.close()


# --------------------------------------------------------------------------- extras


def test_missing_extra_is_shown_and_blocks_the_fetch_before_any_download(tmp_path, monkeypatch):
    real = _checks.importlib.util.find_spec
    monkeypatch.setattr(
        _checks.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "openpyxl" else real(name, *a),
    )
    entry, zb = _entry(requires_extras=["xlsx"])
    app = _lab(tmp_path, [entry], zb)
    try:
        row = _row(app)
        assert row["requires_extras"] == ["xlsx"]
        assert row["missing_extras"] == [
            {"extra": "xlsx", "module": "openpyxl", "hint": 'pip install "camber-toolkit[xlsx]"'}
        ]
        for route in ("/lab/jobs/fetch", "/lab/jobs/ingest"):
            status, out, _ = post(
                app,
                route,
                {"ids": ["test-ahu"], "ingest": True}
                if route.endswith("fetch")
                else {"ids": ["test-ahu"]},
            )
            assert status == 409, route
            assert 'pip install "camber-toolkit[xlsx]"' in out["error"]
        assert app.opener.calls == []  # nothing was downloaded
        assert not os.path.exists(os.path.join(str(tmp_path / "cache"), "test-ahu"))
    finally:
        app.close()


def test_installed_extra_is_not_flagged(tmp_path):
    entry, zb = _entry(requires_extras=["xlsx"])
    app = _lab(tmp_path, [entry], zb)
    try:
        found = _checks.importlib.util.find_spec("openpyxl") is not None
        assert (_row(app)["missing_extras"] == []) is found
    finally:
        app.close()


# --------------------------------------------------------------------------- remove


def _ingested(tmp_path, **kw):
    entry, zb = _entry()
    app = _lab(tmp_path, [entry], zb, **kw)
    status, out, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
    assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"
    return app


def test_remove_route_rejections(tmp_path):
    app = _ingested(tmp_path)
    try:
        ok = {"id": "test-ahu", "confirm": "test-ahu"}
        data = json.dumps(ok).encode()
        base = {
            "Host": f"127.0.0.1:{PORT}",
            "Origin": f"http://127.0.0.1:{PORT}",
            "Content-Type": "application/json",
            "Content-Length": str(len(data)),
        }
        no_auth = dispatch_lab(app, "POST", "/lab/jobs/remove", {}, base, data)
        assert no_auth[0] == 401
        no_csrf = dispatch_lab(
            app, "POST", "/lab/jobs/remove", {}, {**base, "Cookie": _cookie(app)}, data
        )
        assert no_csrf[0] == 403 and "CSRF" in no_csrf[1]["error"]
        assert post(app, "/lab/jobs/remove", ok, Origin="http://evil.example")[0] == 403
        assert post(app, "/lab/jobs/remove", raw=data, **{"Content-Type": "text/plain"})[0] == 415
        assert get(app, "/lab/jobs/remove")[0] == 405
        for bad, why in (
            ({"id": "nope", "confirm": "nope"}, "catalog ids only"),
            ({"id": "../etc", "confirm": "../etc"}, "catalog ids only"),
            ({"id": "test-ahu"}, "to confirm"),
            ({"id": "test-ahu", "confirm": "test"}, "to confirm"),
            ({**ok, "purge_store": "yes"}, "true or false"),
            ({**ok, "path": "/tmp"}, "unknown field"),
            ({"ids": ["test-ahu"], "confirm": "test-ahu"}, "unknown field"),
        ):
            status, out, _ = post(app, "/lab/jobs/remove", bad)
            assert status == 400 and why in out["error"], (bad, out)
        assert os.path.isdir(os.path.join(str(tmp_path / "cache"), "test-ahu"))  # untouched
    finally:
        app.close()


def test_remove_then_purge_in_a_plain_store(tmp_path):
    app = _ingested(tmp_path)
    try:
        cache = os.path.join(str(tmp_path / "cache"), "test-ahu")
        assert _row(app)["purge_blocked"] == []
        status, out, _ = post(app, "/lab/jobs/remove", {"id": "test-ahu", "confirm": "test-ahu"})
        view = _wait(app, out["job"]["id"])
        assert status == 202 and view["state"] == "done" and view["kind"] == "remove"
        res = view["result"][0]["remove"]
        assert res["freed_bytes"] > 0 and res["facilities_dropped"] == []
        assert not os.path.exists(cache)
        assert "ds-test-ahu" in app.store.facilities()  # the store keeps its data
        assert "test-ahu" not in _paths.read_manifest(str(tmp_path / "cache"))

        body = {"id": "test-ahu", "confirm": "test-ahu", "purge_store": True}
        status, out, _ = post(app, "/lab/jobs/remove", body)
        view = _wait(app, out["job"]["id"])
        assert view["state"] == "done"
        assert view["result"][0]["remove"]["facilities_dropped"] == ["ds-test-ahu"]
        assert "ds-test-ahu" not in app.store.facilities()
        assert get(app, "/lab/reports/ds-test-ahu")[0] == 404
    finally:
        app.close()


def test_workspace_purge_respects_the_lifecycle(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    app = _ingested(tmp_path, workspace=pf.root)
    purge = {"id": "test-ahu", "confirm": "test-ahu", "purge_store": True}
    try:
        pf.hold("ds-test-ahu", reason="test hold")
        assert "legal hold" in _row(app)["purge_blocked"][0]
        status, out, _ = post(app, "/lab/jobs/remove", purge)
        assert status == 409 and "legal hold" in out["error"]
        pf.release_hold("ds-test-ahu", reason="test")

        pf.transition("ds-test-ahu", "suspend", reason="test")
        status, out, _ = post(app, "/lab/jobs/remove", purge)
        assert status == 409 and "ds-test-ahu is suspended" in out["error"]
        assert "ds-test-ahu" in app.store.facilities()
        # removing the cache only is still allowed: the lifecycle owns the store, not the cache
        status, out, _ = post(app, "/lab/jobs/remove", {"id": "test-ahu", "confirm": "test-ahu"})
        assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"

        pf.transition("ds-test-ahu", "resume", reason="test")
        assert _row(app)["purge_blocked"] == []
        status, out, _ = post(app, "/lab/jobs/remove", purge)
        assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"
        assert "ds-test-ahu" not in app.store.facilities()
        actions = [(r["action"], r["facility_id"]) for r in pf.audit_log()]
        assert ("lab.remove", None) in actions and ("lab.purge", "ds-test-ahu") in actions
    finally:
        app.close()


# --------------------------------------------------------------------------- from a folder


def _manual(tmp_path, research_only=False):
    zb = _zip_bytes()
    d = _ahu_entry_dict(zb)
    d.update(manual=True, manual_instructions="Download test.zip from the portal.")
    if research_only:
        d.update(licence="CC-BY-NC-4.0", access="research_only")
    entry = DatasetEntry.from_dict(d)
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "test.zip").write_bytes(zb)
    return entry, zb, folder


def _tree(folder) -> dict:
    out = {}
    for dirpath, _dirs, files in os.walk(folder):
        for fn in files:
            p = os.path.join(dirpath, fn)
            st = os.stat(p)
            with open(p, "rb") as fh:
                out[p] = (hashlib.sha256(fh.read()).hexdigest(), st.st_mtime_ns)
    return out


def test_from_dir_ingests_a_manual_entry_and_only_reads_the_folder(tmp_path):
    entry, zb, folder = _manual(tmp_path)
    app = _lab(tmp_path, [entry], zb)
    try:
        assert _row(app)["manual_instructions"] == "Download test.zip from the portal."
        before = _tree(folder)
        body = {"id": "test-ahu", "dir": str(folder), "force": False}
        status, out, _ = post(app, "/lab/jobs/from-dir", body)
        assert status == 202 and out["job"]["kind"] == "ingest from folder"
        view = _wait(app, out["job"]["id"])
        assert view["state"] == "done", view
        res = view["result"][0]
        assert res["fetch"]["files"][0]["name"] == "test.zip"
        assert res["ingest"]["facilities"] == ["ds-test-ahu"] and res["ingest"]["rows"] > 0
        assert _tree(folder) == before  # read, never written
        assert app.opener.calls == []  # nothing downloaded
        # force re-ingests unchanged inputs
        status, out, _ = post(app, "/lab/jobs/from-dir", {**body, "force": True})
        assert _wait(app, out["job"]["id"])["result"][0]["ingest"]["skipped"] is False
    finally:
        app.close()


def test_from_dir_validates_the_path_as_untrusted_input(tmp_path):
    entry, zb, folder = _manual(tmp_path)
    other, _ = _entry(id="test-open")
    app = _lab(tmp_path, [entry, other], zb)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test.zip").write_bytes(zb)
    linked = tmp_path / "linked"
    linked.mkdir()
    os.symlink(outside / "test.zip", linked / "test.zip")
    try:
        for d, why in (
            ("downloads", "absolute path"),
            ("../downloads", "absolute path"),
            (str(tmp_path / "missing"), "not a folder"),
            (str(folder / "test.zip"), "not a folder"),
            (str(folder) + "\x00", "control character"),
            ("", "path of a local folder"),
            (["/tmp"], "path of a local folder"),
            ("/" + "a" * 5000, "longer than"),
            (str(linked), "links outside it"),
        ):
            status, out, _ = post(app, "/lab/jobs/from-dir", {"id": "test-ahu", "dir": d})
            assert status == 400 and why in out["error"], (d, out)
        status, out, _ = post(app, "/lab/jobs/from-dir", {"id": "test-open", "dir": str(folder)})
        assert status == 400 and "not a manual download" in out["error"]
        status, out, _ = post(app, "/lab/jobs/from-dir", {"id": "nope", "dir": str(folder)})
        assert status == 400 and "catalog ids only" in out["error"]
        status, out, _ = post(
            app, "/lab/jobs/from-dir", {"id": "test-ahu", "dir": str(folder), "x": 1}
        )
        assert status == 400 and "unknown field" in out["error"]
        assert (
            post(
                app,
                "/lab/jobs/from-dir",
                {"id": "test-ahu", "dir": str(folder)},
                **{TOKEN_HEADER: "wrong"},
            )[0]
            == 403
        )
        assert app.jobs.jobs() == []  # nothing was queued
        assert not os.path.exists(os.path.join(str(tmp_path / "cache"), "test-ahu"))
    finally:
        app.close()


def test_from_dir_research_only_needs_the_acknowledgement(tmp_path):
    entry, zb, folder = _manual(tmp_path, research_only=True)
    app = _lab(tmp_path, [entry], zb)
    try:
        body = {"id": "test-ahu", "dir": str(folder)}
        status, out, _ = post(app, "/lab/jobs/from-dir", body)
        assert status == 403 and "research" in out["error"]
        ack = {**body, "acknowledge": {"test-ahu": "test-ahu"}}
        status, out, _ = post(app, "/lab/jobs/from-dir", ack)
        assert status == 202 and _wait(app, out["job"]["id"])["state"] == "done"
    finally:
        app.close()


# --------------------------------------------------------------------------- force


def test_force_reingests_unchanged_inputs(tmp_path):
    app = _ingested(tmp_path)
    try:
        status, out, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})
        assert _wait(app, out["job"]["id"])["result"][0]["ingest"]["skipped"] is True
        status, out, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"], "force": True})
        view = _wait(app, out["job"]["id"])
        assert view["params"]["force"] is True
        assert view["result"][0]["ingest"]["skipped"] is False
        assert post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"], "force": 1})[0] == 400
    finally:
        app.close()


def test_page_has_the_new_controls():
    from camber.lab._ui import _BODY, _JS

    for needle in (
        'id="force"',
        'id="rm"',
        'id="rm-typed"',
        'id="rm-purge"',
        'id="fd"',
        'id="fd-path"',
    ):
        assert needle in _BODY, needle
    for needle in (
        "/lab/jobs/remove",
        "/lab/jobs/from-dir",
        "missing_extras",
        "disk_margin",
        "same_disk",
        "purge_blocked",
        "Math.ceil",
    ):
        assert needle in _JS, needle


# --------------------------------------------------------------------------- #125: the report


def test_lab_report_carries_the_evidence_charts(tmp_path):
    app = _ingested(tmp_path)
    try:
        status, html, _ = get(app, "/lab/reports/ds-test-ahu")
        assert status == 200
        assert "<h2>Finding evidence</h2>" in html and html.count("<figure>") >= 1
    finally:
        app.close()


def test_cli_audit_report_carries_the_evidence_charts(tmp_path, monkeypatch):
    app = _ingested(tmp_path)
    app.close()
    entry = app.entries()[0]
    cfg = str(tmp_path / "cfg.json")
    _ops.build_config(entry, app.store, facility_id="ds-test-ahu", out=cfg)
    out = str(tmp_path / "report.html")
    assert cli.main(["report", cfg, "--out", out]) == 0
    html = open(out, encoding="utf-8").read()
    assert "<h2>Finding evidence</h2>" in html and html.count("<figure>") >= 1


def test_evidence_charts_are_bounded():
    import pandas as pd

    from camber.report.audit import EVIDENCE_LIMIT, AuditReport
    from camber.rules.base import Finding

    assert EVIDENCE_LIMIT == 12

    class Rule:
        name = "r"

        def evidence(self, equip, frame):
            from camber.charts.evidence import Evidence

            return Evidence("multitrend", roles=["x"], mask=frame["x"] > 24)

    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    frame = pd.DataFrame({"x": range(48)}, index=idx, dtype=float)
    rep = AuditReport(building="B", level=2)
    rep.add_findings([Finding("r", f"E{i}", "fault", {}, summary="s") for i in range(3)])
    full = rep.to_html(rules={"r": Rule()}, frames=lambda e: frame)
    assert full.count("<figure>") == 3 and "highest-ranked" not in full
    capped = rep.to_html(rules={"r": Rule()}, frames=lambda e: frame, evidence_limit=2)
    assert capped.count("<figure>") == 2 and "highest-ranked" in capped
    # no frame, an empty frame or a failing lookup: no chart, no error
    assert "<figure>" not in rep.to_html(rules={"r": Rule()}, frames=lambda e: None)
    assert "<figure>" not in rep.to_html(rules={"r": Rule()}, frames=lambda e: frame.iloc[:0])
    assert "<figure>" not in rep.to_html(rules={"r": Rule()}, frames=lambda e: 1 / 0)


def test_compressor_short_cycle_has_learn_more_and_an_action():
    from camber import references as R
    from camber.aso import recommend
    from camber.report.audit import AuditReport
    from camber.rules.base import Finding

    f = Finding(
        "compressor_short_cycle",
        "RTU-1",
        "fault",
        {"starts_per_day": 57.0, "max_starts_per_day": 12.0},
        summary="RTU-1: 57.0 compressor starts/day",
    )
    rec = recommend(f)
    assert rec is not None and rec.title == "Stop compressor short-cycling"
    assert rec.cause == "Compressor short-cycling (57 starts/day)"
    assert rec.references == ["pnnl-small-retuning-ch3"]
    ref = R.reference("pnnl-small-retuning-ch3")
    assert ref.short() == "PNNL small-building re-tuning ch. 3" and ref.number == "PNNL-SA-92685"
    assert R.reference("pnnl-retuning-ch3").short() == "PNNL re-tuning ch. 3"
    rep = AuditReport(building="B", level=2)
    rep.add_findings([f])
    html = rep.to_html(recommend=True)
    assert html.count(ref.url) == 2  # the findings table and the recommended actions
    assert "Stop compressor short-cycling" in html
