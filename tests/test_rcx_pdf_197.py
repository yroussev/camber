"""0.107 (#197): the RCx report as PDF, rendered from its own HTML through WeasyPrint.

The extra-missing paths, the metadata dates and the CLI wiring run everywhere (a stand-in
``weasyprint`` module records what the report hands it). The real renders need WeasyPrint, its
Pango libraries and pypdf, and skip cleanly without them.
"""

import builtins
import json
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.report import rcx  # noqa: E402
from camber.report.rcx import MissingPdfExtra, RcxOptions, build_rcx_report  # noqa: E402

HINT = 'pip install "camber-toolkit[pdf]"'


def _cfg(tmp_path):
    fx.make_store(tmp_path)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(fx.config()))
    return path


def _no_weasyprint(monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # import raises ImportError


class _FakeWP(types.ModuleType):
    """A stand-in WeasyPrint that records the HTML and fetcher it is given."""

    def __init__(self):
        super().__init__("weasyprint")
        self.__version__ = "99.0"
        self.calls = []
        fake = self

        class URLFetcher:
            def __init__(self, **kw):
                self.kw = kw

        class HTML:
            def __init__(self, string=None, url_fetcher=None, media_type=None):
                fake.calls.append({"html": string, "fetcher": url_fetcher, "media": media_type})

            def write_pdf(self):
                return b"%PDF-1.7 fake"

        self.URLFetcher = URLFetcher
        self.HTML = HTML


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rcxpdf")
    fx.make_store(tmp)
    run = run_config(fx.config(), base_dir=str(tmp))
    return run


# ------------------------------------------------------------------ without the extra


def test_cli_without_the_extra_names_it_and_exits_2(tmp_path, capsys, monkeypatch):
    _no_weasyprint(monkeypatch)
    path = _cfg(tmp_path)
    out = tmp_path / "r.pdf"
    rc = main(["report", str(path), "--out", str(out), "--layout", "rcx"])
    assert rc == 2
    cap = capsys.readouterr()
    assert HINT in cap.err and "Traceback" not in cap.err + cap.out
    assert not out.exists()
    assert "wrote" not in cap.out  # it fails before the run, not after it


def test_cli_uppercase_extension_is_pdf_too(tmp_path, capsys, monkeypatch):
    _no_weasyprint(monkeypatch)
    path = _cfg(tmp_path)
    assert main(["report", str(path), "--out", str(tmp_path / "R.PDF"), "--layout", "rcx"]) == 2
    assert HINT in capsys.readouterr().err


def test_missing_pango_is_named(monkeypatch):
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingPdfExtra, match="Pango") as ei:
        rcx._weasyprint()
    assert isinstance(ei.value, ImportError)


def test_too_old_weasyprint_is_named(monkeypatch):
    old = types.ModuleType("weasyprint")
    old.__version__ = "60.2"
    monkeypatch.setitem(sys.modules, "weasyprint", old)
    with pytest.raises(MissingPdfExtra, match=r"60.2 .*68\.1 or newer"):
        rcx._weasyprint()
    old.__version__ = "68.0"  # has URLFetcher, but drops data: images under allowed_protocols
    with pytest.raises(MissingPdfExtra, match="68.0"):
        rcx._weasyprint()


def test_version_tuple():
    assert rcx._version_tuple("70.0") == (70, 0) and rcx._version_tuple("68") == (68, 0)
    assert rcx._version_tuple("68.1rc1") == (68, 1) and rcx._version_tuple("dev") == (0, 0)


def test_to_pdf_without_the_extra_raises_the_hint(report, monkeypatch):
    _no_weasyprint(monkeypatch)
    rep = build_rcx_report(report, options=RcxOptions(sections=("cover",)))
    with pytest.raises(MissingPdfExtra, match=r"camber-toolkit\[pdf\]"):
        rep.to_pdf()


def test_audit_layout_refuses_pdf(tmp_path, capsys):
    path = _cfg(tmp_path)
    out = tmp_path / "a.pdf"
    assert main(["report", str(path), "--out", str(out), "--layout", "audit"]) == 2
    assert "--layout rcx" in capsys.readouterr().err and not out.exists()


# ------------------------------------------------------------------ what WeasyPrint is handed


def test_one_layout_source_and_fixed_dates(report, tmp_path, monkeypatch):
    fake = _FakeWP()
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    rep = build_rcx_report(report, options=RcxOptions(paper="a4"))
    out = tmp_path / "r.pdf"
    assert rep.to_pdf(out) == b"%PDF-1.7 fake" and out.read_bytes() == b"%PDF-1.7 fake"
    call = fake.calls[-1]
    # the same document as to_html, plus only the metadata tags in <head>
    end = pd.Timestamp(rep.period[1])
    end = (end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    head = rcx._pdf_head(rep)
    assert f"content='{end}'" in head and "dcterms.created" in head and "dcterms.modified" in head
    assert call["html"].replace(head, "") == rep.to_html()
    assert "size:A4" in call["html"] and call["media"] == "print"
    assert call["fetcher"].kw == {"allowed_protocols": ("data",)}  # nothing from disk or network


def test_source_date_epoch_wins(report, monkeypatch):
    rep = build_rcx_report(report, options=RcxOptions(sections=("cover",)))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    assert "content='2023-11-14T22:13:20Z'" in rcx._pdf_head(rep)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "soon")
    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        rcx._pdf_head(rep)


def test_no_data_means_no_dates(report, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    rep = build_rcx_report(report, options=RcxOptions(sections=("cover",)))
    rep.period = (None, None)
    assert "dcterms" not in rcx._pdf_head(rep)
    rep.period = (pd.Timestamp("2026-03-01 12:00", tz="America/New_York"),) * 2
    assert "content='2026-03-01T17:00:00Z'" in rcx._pdf_head(rep)


def test_cli_writes_pdf_and_rejects_a_bad_epoch(tmp_path, capsys, monkeypatch):
    fake = _FakeWP()
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    path = _cfg(tmp_path)
    out = tmp_path / "r.pdf"
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert main(["report", str(path), "--out", str(out), "--layout", "rcx", "--paper", "a4"]) == 0
    assert out.read_bytes().startswith(b"%PDF") and "size:A4" in fake.calls[-1]["html"]
    assert f"wrote {out}" in capsys.readouterr().out
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "-x")
    assert main(["report", str(path), "--out", str(tmp_path / "b.pdf"), "--layout", "rcx"]) == 2
    err = capsys.readouterr().err
    assert "SOURCE_DATE_EPOCH" in err and "Traceback" not in err


# ------------------------------------------------------------------ real renders (skip without)


def _real():
    try:
        rcx._weasyprint()
    except MissingPdfExtra as e:
        pytest.skip(f"no usable WeasyPrint: {e}")
    return pytest.importorskip("pypdf")


@pytest.fixture(scope="module")
def pdfs(report, tmp_path_factory):
    pypdf = _real()
    tmp = tmp_path_factory.mktemp("rcxpdf-real")
    mp = pytest.MonkeyPatch()
    mp.delenv("SOURCE_DATE_EPOCH", raising=False)
    try:
        out = {}
        for paper in ("letter", "a4"):
            rep = build_rcx_report(report, options=RcxOptions(paper=paper))
            data = rep.to_pdf(tmp / f"{paper}.pdf")
            out[paper] = (rep, data, pypdf.PdfReader(str(tmp / f"{paper}.pdf")))
    finally:
        mp.undo()
    return out


def test_real_pdf_page_size_count_and_footer(pdfs):
    for paper, (w, h) in (("letter", (612, 792)), ("a4", (595.3, 841.9))):
        rep, _, reader = pdfs[paper]
        box = reader.pages[0].mediabox
        assert (round(float(box.width), 1), round(float(box.height), 1)) == (w, h)
        n = len(reader.pages)
        # every section except the cover and the continued appendices starts a page
        starts = sum(1 for s in rep.sections if s["id"] not in rcx._CONTINUED) - 1
        assert n >= starts + 1
        assert f"Page 1 of {n}" in reader.pages[0].extract_text()
        assert f"Page {n} of {n}" in reader.pages[-1].extract_text()


def test_real_pdf_first_pages_text(pdfs):
    rep, _, reader = pdfs["letter"]
    p1 = reader.pages[0].extract_text()
    assert rep.title in p1 and "Cover and provenance" in p1 and "Demo facility" in p1
    assert "CAMBER RCx report" in p1  # the bottom-left margin box
    assert "Data source & licence" in p1  # the cover's data-source block
    banner = "NON-COMMERCIAL / RESEARCH USE ONLY"
    p2 = reader.pages[1].extract_text()
    assert p2.startswith("Executive summary")  # the summary starts its own page
    assert "Contents" not in p1  # the table of contents is hidden in print
    for page in reader.pages:  # the research-only banner repeats on every page
        assert banner in page.extract_text()
    assert "Data source & licence" not in p2


def test_real_pdf_embeds_figures_and_fixed_metadata(pdfs):
    rep, data, reader = pdfs["letter"]
    assert sum(len(p.images) for p in reader.pages) > 0  # the inline PNG charts
    meta = reader.metadata
    end = pd.Timestamp(rcx._pdf_date(rep)).strftime("D:%Y%m%d%H%M%SZ")
    assert meta["/CreationDate"] == end and meta["/ModDate"] == end
    assert meta["/Title"] == rep.title and meta["/Creator"].startswith("CAMBER ")
    assert rep.to_pdf() == data  # reproducible: the same report renders the same bytes
