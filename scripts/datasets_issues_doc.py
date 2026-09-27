#!/usr/bin/env python3
"""Regenerate the "Data issues and how CAMBER handles them" section of docs/DATASETS.md.

The section is rendered from ``camber/datasets/catalog.json`` (``camber.datasets._issues``), so the
documentation always says exactly what the ingester does. Run it after editing a catalog entry's
``data_issues``; ``--check`` exits 1 (without writing) when the document is out of date, which is
what ``tests/test_datasets_catalog.py`` asserts too.

    python scripts/datasets_issues_doc.py          # rewrite docs/DATASETS.md in place
    python scripts/datasets_issues_doc.py --check  # verify only
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.datasets._catalog import load_entries  # noqa: E402
from camber.datasets._issues import render_markdown, splice_markdown  # noqa: E402

DOC = os.path.join(ROOT, "docs", "DATASETS.md")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 if out of date")
    args = ap.parse_args(argv)
    with open(DOC, encoding="utf-8") as fh:
        text = fh.read()
    new = splice_markdown(text, render_markdown(load_entries()))
    if new == text:
        print("docs/DATASETS.md data issues are up to date.")
        return 0
    if args.check:
        print(
            "docs/DATASETS.md is out of date: run scripts/datasets_issues_doc.py", file=sys.stderr
        )
        return 1
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("rewrote the data-issues section of docs/DATASETS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
