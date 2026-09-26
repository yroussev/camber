"""Pin the dataset catalog's download sizes, sha256s and ETags (maintainer tool).

    python scripts/datasets_refresh.py lbnl-sdahu                 # HEAD every file; report drift
    python scripts/datasets_refresh.py lbnl-sdahu --pin           # download unpinned files, pin
    python scripts/datasets_refresh.py lbnl-sdahu --pin --repin   # re-download + re-pin all
    python scripts/datasets_refresh.py bdg2 --pin --local metadata.csv=/data/bdg2/metadata.csv
    python scripts/datasets_refresh.py --all --write              # also rewrite catalog.json

Without ``--pin`` it only issues HEAD requests and compares each file's Content-Length and ETag with
the catalog (a publisher reissue shows up as a size/ETag change). ``--pin`` computes the sha256 of
every unpinned file (``--repin``: every file) -- from a ``--local NAME=PATH`` copy when given
(already-downloaded data, no network), else by streaming it through the catalog's own verified
HTTPS downloader into a temporary directory. ``--write`` saves the pinned values and today's
``verified_on`` back into ``camber/datasets/catalog.json`` (then run the tests: the validator must
still pass). Nothing is ever written to the catalog without ``--write``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from camber.datasets._catalog import validate_catalog  # noqa: E402
from camber.datasets._fetch import (  # noqa: E402
    USER_AGENT,
    download,
    https_opener,
    require_https,
    sha256_file,
)

CATALOG = os.path.join(os.path.dirname(__file__), "..", "camber", "datasets", "catalog.json")


def head(url: str, *, opener=None, timeout: float = 30.0) -> dict:
    """``{"size": int|None, "etag": str|None}`` from a HEAD request (HTTPS only)."""
    require_https(url)
    opener = opener or https_opener(timeout)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        n = resp.headers.get("Content-Length")
        return {"size": int(n) if n else None, "etag": resp.headers.get("ETag")}


def pin_file(f: dict, *, local: str | None = None, opener=None) -> dict:
    """Size + sha256 of one catalog file, from ``local`` or a verified temporary download."""
    if local:
        return {"size": os.path.getsize(local), "sha256": sha256_file(local)}
    with tempfile.TemporaryDirectory(prefix="camber-refresh-") as tmp:
        out = download(f["url"], os.path.join(tmp, "file"), opener=opener)
        return {"size": out.bytes, "sha256": out.sha256, "etag": out.etag}


def refresh(data: dict, ids, *, pin=False, repin=False, local=None, opener=None, log=print):
    """Update ``data`` in place; returns the number of changed fields."""
    local = local or {}
    today = _dt.date.today().isoformat()
    changed = 0
    for d in data["datasets"]:
        if ids and d["id"] not in ids:
            continue
        for f in d["files"]:
            tag = f"{d['id']}/{f['name']}"
            try:
                h = head(f["url"], opener=opener)
            except Exception as e:  # noqa: BLE001 - report and keep going (non-blocking)
                log(f"  {tag}: HEAD failed: {e}")
                h = {"size": None, "etag": None}
            if h["size"] is not None and f.get("size") not in (None, h["size"]):
                log(f"  {tag}: SIZE CHANGED upstream {f.get('size')} -> {h['size']}")
            if h["etag"] and f.get("etag") and h["etag"] != f["etag"]:
                log(f"  {tag}: ETag changed {f['etag']} -> {h['etag']}")
            if pin and (repin or not f.get("sha256")):
                got = pin_file(f, local=local.get(f["name"]), opener=opener)
                for k, v in got.items():
                    if v is not None and f.get(k) != v:
                        f[k] = v
                        changed += 1
                f["pinned"] = True
                if h["etag"] and not f.get("etag"):
                    f["etag"] = h["etag"]
                log(f"  {tag}: pinned {f['size']} bytes sha256 {f['sha256']}")
            else:
                log(f"  {tag}: ok ({h['size']} bytes, etag {h['etag']})")
        if pin:
            d["verified_on"] = today
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*", help="dataset ids (default: none; use --all)")
    ap.add_argument("--all", action="store_true", help="every catalog entry")
    ap.add_argument("--pin", action="store_true", help="compute sha256 for unpinned files")
    ap.add_argument("--repin", action="store_true", help="with --pin: re-pin every file")
    ap.add_argument(
        "--local", action="append", default=[], metavar="NAME=PATH", help="use a local copy"
    )
    ap.add_argument("--write", action="store_true", help="write the result to catalog.json")
    ap.add_argument("--catalog", default=CATALOG)
    args = ap.parse_args(argv)
    if not args.all and not args.ids:
        ap.error("name dataset ids or pass --all")
    with open(args.catalog, encoding="utf-8") as fh:
        data = json.load(fh)
    local = dict(x.split("=", 1) for x in args.local)
    n = refresh(
        data, None if args.all else set(args.ids), pin=args.pin, repin=args.repin, local=local
    )
    errs = validate_catalog(data)
    if errs:
        print("catalog would be INVALID:\n  " + "\n  ".join(errs))
        return 1
    if args.write:
        with open(args.catalog, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
            fh.write("\n")
        print(f"wrote {args.catalog} ({n} field(s) changed)")
    else:
        print(f"{n} field(s) would change (dry run; pass --write to save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
