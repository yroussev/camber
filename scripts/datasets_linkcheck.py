"""Weekly link check of the dataset catalog: are the publishers still serving what we pinned?

    python scripts/datasets_linkcheck.py                      # every entry; markdown report
    python scripts/datasets_linkcheck.py lbnl-sdahu bdg2      # some entries
    python scripts/datasets_linkcheck.py --json out.json      # also a machine-readable result
    python scripts/datasets_linkcheck.py --strict             # exit 1 on drift (default: exit 0)
    python scripts/datasets_linkcheck.py --no-references      # the catalog only

For every file URL of every entry it issues a HEAD request (falling back to a one-byte ranged GET
when a host refuses HEAD) and compares the served size and ETag with the catalog's pins. Where an
entry sets ``licence_check`` it also fetches that page and checks that the licence it states still
matches (``expect``, a case-insensitive substring; ``json_path`` walks a JSON API response first).
Manual-download entries (``manual: true``) are checked only for their landing page. Synthetic
entries (``kind: "synthetic"``, 0.103) are skipped: CAMBER generates their data, there is no
publisher URL to check.

It also checks the linked references of :mod:`camber.references` (0.96, #78: the PNNL Building
Re-tuning guides, chapters and tool guides the reports link to): each URL must still answer. A
404 / 410 is reported as drift (the publisher moved the document: re-find it and update the
registry and its ``verified_on``). ``--no-references`` skips them; naming dataset ids skips them
unless ``--references`` is given.

**Non-blocking by design.** A publisher host being slow or down for a day is not a CAMBER defect,
so the check never fails CI unless ``--strict`` is given; the ``datasets-linkcheck`` workflow
runs it weekly with ``continue-on-error`` and writes the report to the job summary
(``$GITHUB_STEP_SUMMARY``). The real safety net is elsewhere: ``camber datasets fetch`` refuses any
file whose size or sha256 differs from the pin, so drift can never reach a learner silently -- this
check only tells the maintainer early, so the entry can be re-verified (``datasets_refresh.py``).

Dev tooling: not part of the package. Stdlib only (plus camber's own HTTPS-only opener).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from camber.datasets._catalog import load_catalog_data  # noqa: E402
from camber.datasets._fetch import USER_AGENT, https_opener, require_https  # noqa: E402
from camber.references import REFERENCES, reference_urls  # noqa: E402

OK, DRIFT, ERROR = "ok", "drift", "error"


def probe(url: str, *, opener=None, timeout: float = 30.0) -> dict:
    """``{"size", "etag", "status", "method"}`` for ``url`` (HTTPS only; errors raise)."""
    require_https(url)
    opener = opener or https_opener(timeout)
    headers = {"User-Agent": USER_AGENT}
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            n = resp.headers.get("Content-Length")
            return {
                "size": int(n) if n else None,
                "etag": resp.headers.get("ETag"),
                "status": getattr(resp, "status", 200),
                "method": "HEAD",
            }
    except urllib.error.HTTPError as e:
        if e.code not in (403, 405, 501):
            raise
    # some hosts refuse HEAD: a one-byte ranged GET gives the total size in Content-Range
    req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
    with opener.open(req, timeout=timeout) as resp:
        size = None
        rng = resp.headers.get("Content-Range") or ""
        if "/" in rng and rng.rsplit("/", 1)[1].strip().isdigit():
            size = int(rng.rsplit("/", 1)[1])
        elif getattr(resp, "status", 200) == 200 and resp.headers.get("Content-Length"):
            size = int(resp.headers["Content-Length"])
        return {
            "size": size,
            "etag": resp.headers.get("ETag"),
            "status": getattr(resp, "status", 200),
            "method": "GET range",
        }


def _walk(obj, path: str):
    for key in [k for k in path.split(".") if k]:
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    return obj


def check_licence(lc: dict, *, opener=None, timeout: float = 30.0) -> dict:
    """Fetch a ``licence_check`` page and compare what it states with ``expect``."""
    url = require_https(lc["url"])
    opener = opener or https_opener(timeout)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(2_000_000).decode("utf-8", errors="replace")
    stated = body
    if lc.get("json_path"):
        stated = str(_walk(json.loads(body), lc["json_path"]))
    expect = lc.get("expect")
    if not expect:
        return {"status": OK, "detail": "reachable (no expectation set)"}
    if expect.lower() in stated.lower():
        return {"status": OK, "detail": f"states {expect!r}"}
    shown = stated if lc.get("json_path") else "(page text)"
    return {"status": DRIFT, "detail": f"expected {expect!r}, found {shown[:120]!r}"}


def check_entry(d: dict, *, opener=None, timeout: float = 30.0) -> list:
    """One result row per checked URL of a catalog entry dict."""
    rows = []
    targets = [] if d.get("manual") else list(d.get("files") or [])
    if d.get("manual"):
        targets = [{"name": "(landing page)", "url": d["landing_url"], "landing": True}]
    for f in targets:
        row = {"dataset": d["id"], "file": f["name"], "url": f["url"]}
        try:
            got = probe(f["url"], opener=opener, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - report and keep going (non-blocking)
            row.update(status=ERROR, detail=f"{type(e).__name__}: {e}")
            rows.append(row)
            continue
        problems = []
        if not f.get("landing"):
            if f.get("size") and got["size"] is not None and got["size"] != f["size"]:
                problems.append(f"size {f['size']} -> {got['size']}")
            if f.get("etag") and got["etag"] and got["etag"] != f["etag"]:
                problems.append(f"ETag {f['etag']} -> {got['etag']}")
        row.update(
            status=DRIFT if problems else OK,
            detail="; ".join(problems) or f"{got['size']} bytes via {got['method']}",
        )
        rows.append(row)
    lc = d.get("licence_check")
    if lc:
        row = {"dataset": d["id"], "file": "(licence)", "url": lc.get("url")}
        try:
            row.update(check_licence(lc, opener=opener, timeout=timeout))
        except Exception as e:  # noqa: BLE001
            row.update(status=ERROR, detail=f"{type(e).__name__}: {e}")
        rows.append(row)
    return rows


def run(data: dict, ids=None, *, opener=None, timeout: float = 30.0) -> list:
    """Check every (or the named) entries; a flat list of result rows."""
    rows = []
    for d in data["datasets"]:
        if ids and d["id"] not in ids:
            continue
        if d.get("kind") == "synthetic":  # 0.103 (#133): generated locally, nothing to check
            continue
        rows.extend(check_entry(d, opener=opener, timeout=timeout))
    return rows


def check_references(*, opener=None, timeout: float = 30.0) -> list:
    """One row per linked reference URL (:func:`camber.references.reference_urls`)."""
    by_url = {r.url: r for r in REFERENCES.values()}
    rows = []
    for url in reference_urls():
        ref = by_url[url]
        row = {"dataset": "(references)", "file": ref.id, "url": url}
        try:
            got = probe(url, opener=opener, timeout=timeout)
            row.update(status=OK, detail=f"HTTP {got['status']} via {got['method']}")
        except urllib.error.HTTPError as e:
            gone = e.code in (404, 410)
            row.update(
                status=DRIFT if gone else ERROR,
                detail=f"HTTP {e.code}" + (" (moved? update camber/references.py)" if gone else ""),
            )
        except Exception as e:  # noqa: BLE001 - report and keep going (non-blocking)
            row.update(status=ERROR, detail=f"{type(e).__name__}: {e}")
        rows.append(row)
    return rows


def markdown(rows: list) -> str:
    """The report: a summary line, then every drift / error row, then the ok count."""
    n = {s: sum(1 for r in rows if r["status"] == s) for s in (OK, DRIFT, ERROR)}
    lines = [
        "## Dataset catalog link check",
        "",
        f"{len(rows)} URL(s): **{n[DRIFT]} drift**, {n[ERROR]} error(s), {n[OK]} ok.",
        "",
    ]
    bad = [r for r in rows if r["status"] != OK]
    if bad:
        lines += ["| status | dataset | file | detail |", "|---|---|---|---|"]
        for r in bad:
            detail = str(r["detail"]).replace("|", "\\|")
            lines.append(f"| {r['status']} | `{r['dataset']}` | `{r['file']}` | {detail} |")
        lines += [
            "",
            "Drift: re-verify the entry (`python scripts/datasets_refresh.py <id>`), re-check its",
            "licence on the host's own page, and re-pin only after reviewing the new file. Errors",
            "are usually a host outage; re-run before acting.",
        ]
    else:
        lines.append("Every file is served at its pinned size / ETag.")
    return "\n".join(lines) + "\n"


def main(argv=None, *, opener=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*", help="dataset ids (default: every entry)")
    ap.add_argument("--json", help="also write the result rows to this JSON file")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything drifted")
    ap.add_argument("--catalog", help="catalog JSON (default: the packaged one)")
    refs = ap.add_mutually_exclusive_group()
    refs.add_argument(
        "--references",
        dest="refs",
        action="store_true",
        default=None,
        help="also check the linked references (the default unless dataset ids are named)",
    )
    refs.add_argument("--no-references", dest="refs", action="store_false")
    args = ap.parse_args(argv)
    if args.catalog:
        with open(args.catalog, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = load_catalog_data()
    rows = run(data, set(args.ids) or None, opener=opener, timeout=args.timeout)
    if args.refs if args.refs is not None else not args.ids:
        rows += check_references(opener=opener, timeout=args.timeout)
    report = markdown(rows)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    for r in rows:
        if r["status"] != OK and os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning::{r['dataset']}/{r['file']}: {r['status']}: {r['detail']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
    drifted = any(r["status"] == DRIFT for r in rows)
    return 1 if (args.strict and drifted) else 0


if __name__ == "__main__":
    raise SystemExit(main())
