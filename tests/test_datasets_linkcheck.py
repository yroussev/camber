"""scripts/datasets_linkcheck.py (offline, fake opener) and the opt-in network smoke test."""

import dataclasses
import importlib.util
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    path = os.path.join(_ROOT, "scripts", "datasets_linkcheck.py")
    spec = importlib.util.spec_from_file_location("datasets_linkcheck", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    def __init__(self, body=b"", headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    """url -> (headers, body) for HEAD / GET; ``refuse_head`` answers HEAD with 405."""

    def __init__(self, table, refuse_head=()):
        self.table, self.refuse_head, self.calls = table, set(refuse_head), []

    def open(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        self.calls.append((method, url))
        if url not in self.table:
            raise urllib.error.URLError("unreachable")
        if method == "HEAD" and url in self.refuse_head:
            raise urllib.error.HTTPError(url, 405, "no HEAD", {}, None)
        headers, body = self.table[url]
        return _Resp(body, headers, 206 if req.get_header("Range") else 200)


def _cat(**lc):
    d = {
        "id": "t",
        "landing_url": "https://example.org/t",
        "files": [
            {"name": "a.zip", "url": "https://example.org/a.zip", "size": 10, "etag": '"e1"'},
            {"name": "b.csv", "url": "https://example.org/b.csv", "size": 5},
        ],
    }
    if lc:
        d["licence_check"] = lc
    return {"schema": 1, "datasets": [d]}


def test_ok_drift_error_and_head_fallback():
    mod = _mod()
    opener = _Opener(
        {
            "https://example.org/a.zip": ({"Content-Length": "10", "ETag": '"e2"'}, b""),
            "https://example.org/b.csv": ({"Content-Range": "bytes 0-0/5"}, b"x"),
        },
        refuse_head={"https://example.org/b.csv"},
    )
    rows = mod.run(_cat(), opener=opener)
    by = {r["file"]: r for r in rows}
    assert by["a.zip"]["status"] == "drift" and "ETag" in by["a.zip"]["detail"]
    assert by["b.csv"]["status"] == "ok" and "GET range" in by["b.csv"]["detail"]
    rows = mod.run(_cat(), opener=_Opener({}))
    assert {r["status"] for r in rows} == {"error"}
    md = mod.markdown(rows)
    assert "2 error(s)" in md and "| error |" in md


def test_size_drift_and_licence_check_json_path():
    mod = _mod()
    api = json.dumps({"license": {"name": "CC BY 4.0"}}).encode()
    table = {
        "https://example.org/a.zip": ({"Content-Length": "11"}, b""),
        "https://example.org/b.csv": ({"Content-Length": "5"}, b""),
        "https://api.example.org/t": ({}, api),
    }
    ok = _cat(url="https://api.example.org/t", json_path="license.name", expect="CC BY 4.0")
    rows = mod.run(ok, opener=_Opener(table))
    by = {r["file"]: r for r in rows}
    assert by["a.zip"]["status"] == "drift" and "size 10 -> 11" in by["a.zip"]["detail"]
    assert by["(licence)"]["status"] == "ok"
    bad = _cat(url="https://api.example.org/t", json_path="license.name", expect="CC0")
    lic = [r for r in mod.run(bad, opener=_Opener(table)) if r["file"] == "(licence)"][0]
    assert lic["status"] == "drift" and "CC BY 4.0" in lic["detail"]


def test_manual_entries_check_only_the_landing_page():
    mod = _mod()
    cat = _cat()
    cat["datasets"][0]["manual"] = True
    opener = _Opener({"https://example.org/t": ({"Content-Length": "100"}, b"")})
    rows = mod.run(cat, opener=opener)
    assert [r["file"] for r in rows] == ["(landing page)"] and rows[0]["status"] == "ok"


def test_main_is_non_blocking_unless_strict(tmp_path, monkeypatch, capsys):
    mod = _mod()
    cat = tmp_path / "c.json"
    cat.write_text(json.dumps(_cat()))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    drift = _Opener(
        {
            "https://example.org/a.zip": ({"Content-Length": "99"}, b""),
            "https://example.org/b.csv": ({"Content-Length": "5"}, b""),
        }
    )
    out = tmp_path / "rows.json"
    assert mod.main(["--catalog", str(cat), "--json", str(out)], opener=drift) == 0
    assert "**1 drift**" in summary.read_text()
    assert "::warning::t/a.zip: drift" in capsys.readouterr().out
    assert json.loads(out.read_text())[0]["status"] == "drift"
    assert mod.main(["--catalog", str(cat), "--strict"], opener=drift) == 1


def test_shipped_catalog_is_checkable_offline():
    mod = _mod()
    rows = mod.run(mod.load_catalog_data(), opener=_Opener({}))
    assert rows and all(r["status"] == "error" for r in rows)  # every URL probed, none reached


# --------------------------------------------------------------------------- network (opt-in)


@pytest.mark.network
def test_network_smoke_fetch_one_small_open_file(tmp_path, monkeypatch):  # pragma: no cover
    """``pytest -m network``: the real HTTPS path fetches + verifies the smallest open file."""
    from camber import datasets
    from camber.datasets._ops import fetch_dataset

    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    size, entry, f = min(
        (f["size"], e, f)
        for e in datasets.catalog(licence="commercial")
        if not e.manual
        for f in e.files
        if f.get("sha256") and f.get("size")
    )
    assert size < 5_000_000
    one = dataclasses.replace(
        entry,
        files=(f,),
        subsets={"default": {"files": [f["name"]], "runs": [], "store_bytes_estimate": 1}},
    )
    res = fetch_dataset(one)
    assert res.files[0]["sha256"] == f["sha256"] and res.files[0]["bytes"] == size
    again = fetch_dataset(one)  # verified from the cache, no second download
    assert again.files[0]["skipped"]
    # and the link check agrees with the pin
    rows = _mod().check_entry({"id": entry.id, "landing_url": entry.landing_url, "files": [f]})
    assert rows[0]["status"] in ("ok", "drift"), rows
