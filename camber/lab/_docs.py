"""Workbook exercise links in the lab: resolve a catalog ``exercise`` and serve it offline.

A catalog entry's ``suggested_analyses.exercise`` (0.97, #79) is either an https URL or a
**docs-relative workbook page**, ``workbook/<id>.md`` (optionally ``#<anchor>``) -- the same path
the page has under ``docs/``, so one value works on GitHub, on the published site and here.

The lab resolves a relative link in this order:

1. **A local docs tree** (``camber lab --docs DIR``; by default the ``docs/`` directory beside the
   package in a source checkout or editable install, when it has a ``workbook/`` folder): the link
   becomes ``/lab/docs/workbook/<id>.md#<anchor>`` and the lab serves the page itself, so the
   exercise opens with no network. The page is shown as its Markdown source with the headings
   anchored and the links made clickable -- a reading copy, not the rendered site.
2. **Otherwise the published docs site** (:data:`DOCS_SITE`), where
   ``workbook/<id>.md#a`` is ``<site>workbook/<id>/#a``.

Only ``workbook/*.md`` files inside the docs tree are served (the name must match the catalog's
exercise pattern and the resolved path must stay inside ``<docs>/workbook``), read-only, under a
CSP that allows no script at all.
"""

from __future__ import annotations

import html
import os
import re

from ..datasets._catalog import EXERCISE_PAGE_RE, is_exercise_link

#: the published docs site a relative exercise link resolves to (mkdocs directory URLs)
DOCS_SITE = "https://yroussev.github.io/camber/"

#: where the lab serves a local workbook page
DOCS_ROUTE = "/lab/docs/"
#: a served workbook page runs no script and loads nothing but its inline style
DOCS_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
_PAGE_RE = re.compile(r"^workbook/[a-z0-9][a-z0-9_-]*\.md$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_REF_DEF = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)")
_INLINE_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_REF_LINK = re.compile(r"\[([^\]\n]+)\]\[([^\]\n]*)\]")

__all__ = [
    "DOCS_SITE",
    "DOCS_ROUTE",
    "DOCS_CSP",
    "default_docs_dir",
    "exercise_url",
    "exercise_href",
    "page_html",
    "slug",
]


def exercise_url(value: str) -> str:
    """The published URL of an exercise link: an https link unchanged; a docs-relative page
    ``workbook/<id>.md#a`` as the docs site serves it (``<DOCS_SITE>workbook/<id>/#a``)."""
    if not is_exercise_link(value):
        raise ValueError(f"not an exercise link: {value!r}")
    if value.startswith("https://"):
        return value
    page, _, anchor = value.partition("#")
    return DOCS_SITE + page[: -len(".md")] + "/" + (f"#{anchor}" if anchor else "")


def default_docs_dir() -> str | None:
    """The ``docs/`` directory beside the installed package, when it holds a ``workbook/``."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cand = os.path.join(here, "docs")
    return cand if os.path.isdir(os.path.join(cand, "workbook")) else None


def _local_file(docs_dir: str | None, page: str) -> str | None:
    """The real path of workbook ``page`` under ``docs_dir`` (``None`` when absent or unsafe)."""
    if not docs_dir or not _PAGE_RE.match(page or ""):
        return None
    root = os.path.realpath(os.path.join(docs_dir, "workbook"))
    path = os.path.realpath(os.path.join(docs_dir, page))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        return None
    return path


def exercise_href(value, docs_dir: str | None) -> str | None:
    """Where the lab's *exercise* link points for a catalog ``exercise`` value.

    ``None`` for no (or an invalid) value; an https URL unchanged; a relative workbook page as the
    lab's own ``/lab/docs/...`` route when ``docs_dir`` has the page, else the published URL.
    """
    if not is_exercise_link(value):
        return None
    if value.startswith("https://"):
        return value
    page, _, anchor = value.partition("#")
    if _local_file(docs_dir, page) is not None:
        return DOCS_ROUTE + page + (f"#{anchor}" if anchor else "")
    return exercise_url(value)


def slug(text: str) -> str:
    """A heading's anchor, as the docs site (Python-Markdown's ``toc``) and GitHub make it for
    plain ASCII headings: inline code marks and punctuation dropped, lower case, spaces to ``-``."""
    t = re.sub(r"[`*_]", "", text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[^\w\s-]", "", t).strip().lower()
    return re.sub(r"[-\s]+", "-", t)


def _href(target: str) -> str | None:
    """A safe href for a link target in a served page (``None``: leave it as text)."""
    if target.startswith("https://"):
        return target
    page, _, anchor = target.partition("#")
    if not page and anchor:
        return f"#{anchor}"
    name = os.path.basename(page)
    if page == name and _PAGE_RE.match(f"workbook/{name}"):  # a sibling workbook page
        return DOCS_ROUTE + "workbook/" + name + (f"#{anchor}" if anchor else "")
    if EXERCISE_PAGE_RE.match(target):
        return DOCS_ROUTE + target
    if re.fullmatch(r"\.\./[A-Za-z0-9_-]+\.md", page):  # another docs page: the published one
        return DOCS_SITE + page[3:-3] + "/" + (f"#{anchor}" if anchor else "")
    return None


def _inline(line: str, refs: dict) -> str:
    """One escaped line with ``[text](target)`` and ``[text][id]`` links made clickable."""
    out, pos = [], 0
    pattern = re.compile(f"{_INLINE_LINK.pattern}|{_REF_LINK.pattern}")
    for m in pattern.finditer(line):
        out.append(html.escape(line[pos : m.start()]))
        if m.group(1) is not None:
            text, target = m.group(1), m.group(2)
        else:
            text, target = m.group(3), (m.group(4) or m.group(3))
            target = refs.get(target.lower(), "")
        href = _href(target) if target else None
        if href is None:
            out.append(html.escape(m.group(0)))
        else:
            ext = href.startswith("https://")
            rel = " target='_blank' rel='noopener noreferrer'" if ext else ""
            out.append(f"<a href='{html.escape(href, quote=True)}'{rel}>{html.escape(text)}</a>")
        pos = m.end()
    out.append(html.escape(line[pos:]))
    return "".join(out)


def page_html(docs_dir: str | None, page: str) -> str | None:
    """The lab's offline reading copy of workbook ``page`` (``None`` when it is not served)."""
    path = _local_file(docs_dir, page)
    if path is None:
        return None
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    refs: dict = {}
    for ln in lines:
        m = _REF_DEF.match(ln)
        if m:
            refs[m.group(1).lower()] = m.group(2)
    body: list = []
    block: list = []
    fence = False

    def flush():
        if block:
            body.append("<pre>" + "\n".join(block) + "</pre>")
            block.clear()

    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
            block.append(html.escape(ln))
            continue
        m = None if fence else _HEADING.match(ln)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2)
            body.append(
                f"<h{level} id='{html.escape(slug(text), quote=True)}'>"
                f"{_inline(text, refs)}</h{level}>"
            )
        elif fence:
            block.append(html.escape(ln))
        else:
            block.append(_inline(ln, refs))
    flush()
    published = exercise_url(page)
    title = html.escape(page)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>"
        "body{margin:0 auto;max-width:900px;padding:16px;font:15px/1.5 system-ui,sans-serif;"
        "background:#fff;color:#1d1d1b}pre{white-space:pre-wrap;font:14px/1.5 ui-monospace,"
        "Menlo,monospace}a{color:#2f5fb3}.note{color:#6b6b66;font-size:13px}"
        "@media (prefers-color-scheme:dark){body{background:#161615;color:#ececea}"
        "a{color:#7fa6ea}.note{color:#a3a39c}}"
        "</style></head><body>"
        "<p class='note'>Offline reading copy of the workbook page (its Markdown source), served "
        "by <code>camber lab</code> from the local docs. The rendered page is "
        f"<a href='{html.escape(published, quote=True)}' target='_blank' "
        f"rel='noopener noreferrer'>on the docs site</a>. <a href='/lab'>Back to the lab</a>.</p>"
        + "".join(body)
        + "</body></html>"
    )
