"""Text from the data, made safe to show in a result (0.111, #291).

Facility, equipment and point names, units and metadata come from the data, so a result's
``text``, its facts' ``text`` and its banners must never let them pass for the tool's own words.
:func:`name` turns a data value into a **quoted span** for a handler to put in its text
(``equipment "AHU-2"``): one line (newlines and tabs become spaces), with control characters
(C0 / C1, ESC sequences, BEL, NUL), bidi overrides and isolates, zero-width and other invisible
format characters taken out, a fact-id bracket inside it (``[F1@rule_run]``) turned into
parentheses so it never reads as a citation, and a long value cut (an ellipsis and a hash of the
whole value, so two cut values stay apart).

:meth:`camber.capabilities.Registry.call` then makes every result one-line text by
construction, whatever a handler did: it strips the same characters from ``text``, every fact's
``text`` and every banner, turns any citation in data into parentheses, treats each quoted span as
opaque to the citation filter (so a sentence break or a number inside a name is never "removed"),
and cuts any string in ``data`` or ``facts`` longer than :data:`MAX_VALUE`. A line that starts
with ``NOTICE:`` therefore only ever comes from a banner.

Private module -- not public API.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: private-use marks around a quoted span until the registry has checked the text
OPEN, CLOSE = "", ""
#: the longest data value :func:`name` shows (longer: cut with an ellipsis and a hash)
MAX_NAME = 100
#: the longest string anywhere in a result's ``data`` or ``facts``
MAX_VALUE = 2000

#: terminal escape sequences (CSI, OSC, two-character escapes), dropped whole
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
#: characters that break a line (or a terminal's idea of one): shown as a space
_BREAKS = frozenset("\t\n\r\v\f\x1c\x1d\x1e\x1f\x85  ")
#: a citation-shaped token (the shape :mod:`camber.agent.verify` reads)
_CITE = re.compile(r"\[([A-Za-z]+\d+(?:@[A-Za-z0-9_\-]+)?)\]")
#: a quoted span, with its marks
_SPAN = re.compile(f"{OPEN}[^{OPEN}{CLOSE}]*{CLOSE}")


def _drop(ch: str) -> bool:
    """A control (C0, DEL, C1), format (bidi, zero-width, tag ...), surrogate or private-use
    character."""
    return unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co")


def one_line(value, *, keep_marks: bool = False) -> str:
    """``value`` as one line of plain text: escape sequences and invisible characters out, every
    run of whitespace (newlines and tabs included) one space. ``keep_marks`` keeps the marks of
    the quoted spans :func:`name` made."""
    s = _ANSI.sub("", str(value))
    out = []
    for ch in s:
        if ch in _BREAKS:
            out.append(" ")
        elif (keep_marks and ch in (OPEN, CLOSE)) or not _drop(ch):
            out.append(ch)
    return " ".join("".join(out).split())


def defang(text: str) -> str:
    """``text`` with each citation-shaped ``[F1@ctx]`` written ``(F1@ctx)``."""
    return _CITE.sub(r"(\1)", text)


def clip(value: str, limit: int, *, key=None) -> str:
    """``value`` when it is at most ``limit`` characters, else its start, an ellipsis and six hex
    digits of SHA-256 over ``key`` (default: ``value``), ``limit`` characters in all."""
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(str(value if key is None else key).encode("utf-8", "replace"))
    return f"{value[: max(limit - 8, 1)]}…~{digest.hexdigest()[:6]}"


def name(value, *, limit: int = MAX_NAME) -> str:
    """A data value (a name, an id, a unit) as a quoted span for a result's text."""
    s = defang(one_line(value))
    s = clip(s, limit, key=value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{OPEN}"{s}"{CLOSE}'


def hide_spans(text: str) -> tuple[str, list]:
    """``text`` with each quoted span replaced by a placeholder of letters only (no space, digit
    or sentence end, so the citation filter reads it as one word), and the spans."""
    spans: list = []

    def sub(m):
        spans.append(m.group(0))
        n, tag = len(spans) - 1, ""
        while True:
            n, r = divmod(n, 26)
            tag = chr(97 + r) + tag
            if not n:
                break
        return f"{OPEN}{tag}{CLOSE}"

    return _SPAN.sub(sub, text), spans


def show_spans(text: str, spans: list) -> str:
    """The inverse of :func:`hide_spans`."""

    def sub(m):
        tag = m.group(0)[1:-1]
        n = 0
        for c in tag:
            n = n * 26 + ord(c) - 97
        return spans[n] if tag.isalpha() and tag.islower() and n < len(spans) else ""

    return re.sub(f"{OPEN}[a-z]+{CLOSE}", sub, text)


def unmark(text: str) -> str:
    """``text`` without the marks of its quoted spans (the quotes stay)."""
    return text.replace(OPEN, "").replace(CLOSE, "")


def finish(text, *, known_ids=None) -> str:
    """One handler-written string (a summary, a fact's text or a banner) as one line, each
    citation whose id is not in ``known_ids`` written in parentheses, span marks kept."""
    s = one_line(text, keep_marks=True)
    known = frozenset(known_ids or ())
    return _CITE.sub(lambda m: m.group(0) if m.group(1) in known else f"({m.group(1)})", s)


def lead(text: str) -> str:
    """``text`` with a ``Summary:`` lead when it would start with ``NOTICE``, so that only a
    banner ever starts a line with it."""
    return f"Summary: {text}" if re.match(r"(?i)[\W_]*notice\b", text) else text


def cap_strings(x, depth: int = 0):
    """``x`` (JSON data) with every string longer than :data:`MAX_VALUE` cut (see :func:`clip`)
    and no span marks left."""
    if depth > 40:
        return x
    if isinstance(x, str):
        if OPEN in x or CLOSE in x:
            x = unmark(x)
        return clip(x, MAX_VALUE)
    if isinstance(x, dict):
        return {k: cap_strings(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [cap_strings(v, depth + 1) for v in x]
    if isinstance(x, tuple):
        return [cap_strings(v, depth + 1) for v in x]
    return x
