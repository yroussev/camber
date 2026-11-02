"""Pages of a result's lists, and the record of what was left out (0.111, #299).

A capability cuts a list only through :func:`page`: it takes the page the caller asked for
(``offset`` and ``limit`` arguments, or the capability's model-sized default), and when the page
is not the whole list it records a notice in the call it runs under. :meth:`Registry.call`
collects those notices, adds them to the result's banners and sets ``truncated``, so no
capability can cut a list and leave the flag false.

Private module -- not public API.
"""

from __future__ import annotations

import contextvars
import json

#: the notices of the call this context runs (``None`` outside :meth:`Registry.call`)
_TRIMMED: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "camber_capability_trimmed", default=None
)

#: the largest ``limit`` any capability takes (the registry's ``max_rows`` still applies)
MAX_LIMIT = 1000


def limit_arg(default: int, what: str) -> dict:
    """The input schema of a ``limit`` argument whose default is ``default``."""
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "description": f"page size of {what} (default {default})",
    }


#: the size a default page aims for (0.112, #333): characters of the MCP ``structuredContent``
#: (the data and, under ``camber``, the facts and notices), below the tool-output limit of a
#: host that gives the model only that JSON (about 25,000 characters with room to spare; JSON
#: of hex fact ids and short keys runs at about two characters a token)
RESULT_CHARS = 22_000

#: the characters the call token adds to each fact id where it appears (``-1a2b3c``)
_TOKEN = 7


def result_chars(data, facts, banners=()) -> int:
    """About how many characters ``data`` with ``facts`` and ``banners`` take as MCP
    ``structuredContent`` (the data, then each fact's id and text and each notice, which the
    registry also gives a fact); an estimate made before the registry scopes the fact ids."""
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))
    size += sum(
        len(str(f.get("id", ""))) + 2 * _TOKEN + len(str(f.get("text", ""))) + 8 for f in facts
    )
    return size + sum(2 * len(str(b)) + 40 for b in banners)


def fits(data, facts, banners=(), *, extra: int = 0) -> bool:
    """Whether a result of ``data`` and ``facts`` with ``banners`` (and ``extra`` characters
    still to come, such as a page's notice) fits :data:`RESULT_CHARS`."""
    return result_chars(data, facts, banners) + extra <= RESULT_CHARS


#: the input schema of the ``offset`` argument
OFFSET = {
    "type": "integer",
    "minimum": 0,
    "maximum": 1_000_000,
    "description": "rows to skip, for the next page",
}


def collect() -> tuple[list, contextvars.Token]:
    """Start recording the notices of one call (the registry's handler thread does this)."""
    notices: list = []
    return notices, _TRIMMED.set(notices)


def trimmed(notice: str) -> None:
    """Record that something was left out of this call's result, with the notice to show."""
    notices = _TRIMMED.get()
    if notices is not None and notice not in notices:
        notices.append(notice)


def page(
    rows,
    arguments: dict,
    *,
    default: int,
    what: str,
    order: str = "",
    more: str = "",
    how: str = "",
):
    """The page of ``rows`` the caller asked for: ``arguments["offset"]`` rows skipped, then at
    most ``arguments["limit"]`` (``default`` when absent). A page that is not the whole list is
    recorded with a notice naming ``what`` (``'findings'``), the rows shown, the total and how
    to get the rest (``how`` replaces the offset / limit advice for a list paged by other
    arguments; ``more``, when given, is a further hint such as a filter)."""
    rows = list(rows)
    limit = arguments.get("limit") or default
    offset = arguments.get("offset") or 0
    out = rows[offset : offset + limit]
    total = len(rows)
    if len(out) == total:
        return out
    if not out and total <= limit:  # a short sibling of the list being paged (#314)
        trimmed(
            f"'{what}' is empty on this page: all its {total} row(s) are on the first page "
            "(offset=0); one offset pages every list of this result"
        )
        return out
    if not out:
        trimmed(
            f"'{what}' is empty on this page: offset {offset} is past its {total} row(s) "
            "(call again with a smaller offset)"
        )
        return out
    ordered = f" ({order})" if order else ""
    nxt = offset + len(out)
    if nxt >= total:
        how = "call again with offset=0 for the first rows"
    elif not how:
        # 0.112 (#333): the next page, not a larger one: a host may refuse a larger result
        how = f"call again with offset={nxt} for the next page"
    hint = f"; {more}" if more else ""
    rows_shown = f"rows {offset + 1}-{nxt}" if nxt > offset + 1 else f"row {nxt}"
    trimmed(f"'{what}' shows {rows_shown} of {total}{ordered}: {how}{hint}")
    return out
