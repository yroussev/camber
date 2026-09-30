#!/usr/bin/env python3
"""Reference links for the re-tuning workbook (docs/workbook/), from ``camber.references``.

The workbook links to the PNNL re-tuning material through the reference registry's ids, as
**reference-style Markdown links** whose label is the id -- plain Markdown that renders the same
on GitHub, on the docs site and in the lab's offline copy, with no mkdocs plugin::

    Read the [economizer guide][pnnl-guide-economizer] before you start.

    [pnnl-guide-economizer]: https://www.pnnl.gov/.../pnnl_sa_86706.pdf "Building Re-Tuning ..."

Usage::

    python scripts/workbook_refs.py pnnl-guide-economizer pnnl-retuning-ch6   # print definitions
    python scripts/workbook_refs.py --list                                    # every id
    python scripts/workbook_refs.py --check                                   # verify the pages

``--check`` (also run by ``tests/workbook/test_workbook_docs.py``) fails when a workbook page:

- defines a reference-style link whose label looks like a registry id but is not one, or whose
  URL is not the registry's URL for that id;
- uses ``[text][id]`` (or ``[id][]``) without defining ``id`` on the page;
- links a PNNL URL any other way (an inline link, or a definition under a non-id label), so every
  PNNL link in the workbook goes through the registry and its weekly link check.
"""

from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.references import REFERENCES  # noqa: E402

WORKBOOK = os.path.join(ROOT, "docs", "workbook")
DEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$")
USE_RE = re.compile(r"\[([^\]\n]+)\]\[([^\]\n]*)\]")
PNNL_RE = re.compile(r"https?://(?:www\.)?pnnl\.gov\S*", re.IGNORECASE)
# a label that is meant to be a registry id: the registry's own id shapes
ID_SHAPE = re.compile(r"^(pnnl-[a-z0-9-]+|ecam)$")


def definition(rid: str) -> str:
    """The Markdown link definition for reference ``rid`` (``KeyError`` if unknown)."""
    r = REFERENCES[rid]
    title = r.label().replace('"', "'")
    return f'[{r.id}]: {r.url} "{title}"'


def check_text(text: str, where: str) -> list:
    """Every reference-link problem in one page's Markdown (``[]``: none)."""
    errs, defined = [], {}
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = DEF_RE.match(line)
        if m:
            label, url = m.group(1).lower(), m.group(2)
            defined[label] = url
            if ID_SHAPE.match(label):
                if label not in REFERENCES:
                    errs.append(f"{where}:{n}: [{label}] is not a camber.references id")
                elif url != REFERENCES[label].url:
                    errs.append(
                        f"{where}:{n}: [{label}] links {url}, but the registry has "
                        f"{REFERENCES[label].url}"
                    )
            elif PNNL_RE.search(url):
                errs.append(f"{where}:{n}: PNNL link under [{label}]: use its registry id")
            continue
        for u in PNNL_RE.findall(line):
            errs.append(f"{where}:{n}: inline PNNL link {u}: use a [text][<id>] reference link")
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        for m in USE_RE.finditer(line):
            label = (m.group(2) or m.group(1)).lower()
            if label not in defined:
                errs.append(f"{where}:{n}: [{label}] is used but not defined on the page")
    return errs


def used_ids(text: str) -> set:
    """The registry ids a page defines links for."""
    out = set()
    for line in text.splitlines():
        m = DEF_RE.match(line)
        if m and m.group(1).lower() in REFERENCES:
            out.add(m.group(1).lower())
    return out


def check_pages(root: str = WORKBOOK) -> list:
    """Problems across every workbook page (``_template.md`` excluded)."""
    errs = []
    for path in sorted(glob.glob(os.path.join(root, "*.md"))):
        if os.path.basename(path).startswith("_"):  # the page template
            continue
        with open(path, encoding="utf-8") as fh:
            errs += check_text(fh.read(), os.path.relpath(path, ROOT))
    return errs


def main(argv: list) -> int:
    if not argv or argv == ["-h"] or argv == ["--help"]:
        print(__doc__)
        return 2
    if argv == ["--list"]:
        for r in REFERENCES.values():
            print(f"{r.id:40s} {r.short()}")
        return 0
    if argv == ["--check"]:
        errs = check_pages()
        for e in errs:
            print(e, file=sys.stderr)
        print("workbook reference links: " + ("OK" if not errs else f"{len(errs)} problem(s)"))
        return 1 if errs else 0
    bad = [a for a in argv if a not in REFERENCES]
    if bad:
        print(f"unknown reference id(s): {', '.join(bad)} (see --list)", file=sys.stderr)
        return 1
    for a in argv:
        print(definition(a))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
