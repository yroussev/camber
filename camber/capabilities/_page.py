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
        how = (
            f"call again with offset={nxt} for the next page, or a larger limit (up to {MAX_LIMIT})"
        )
    hint = f"; {more}" if more else ""
    rows_shown = f"rows {offset + 1}-{nxt}" if nxt > offset + 1 else f"row {nxt}"
    trimmed(f"'{what}' shows {rows_shown} of {total}{ordered}: {how}{hint}")
    return out
