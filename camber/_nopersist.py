"""The no-persist scope: code that would write state checks :func:`persisting` first (0.110, #276).

A run made for a reader that must not change anything -- the capability registry
(:mod:`camber.capabilities`), and so the MCP and A2A servers built on it -- runs inside
:func:`no_persist`. Inside it :func:`camber.config.run_config` folds no fault history, writes no
config-named report file and records nothing in a workspace manifest, a store does not write back
its ``_catalog.json`` cache, and an M&V billing entry fetches no weather (no network, no weather
cache, no audit line). Findings and reports are computed exactly as before.

The flag is a :mod:`contextvars` variable, so it follows the code that set it, not the process: a
worker thread sees it only when started with a copy of the caller's context
(``contextvars.copy_context().run``), which the registry does. Private module -- not public API.
"""

from __future__ import annotations

import contextlib
import contextvars

_NO_PERSIST: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "camber_no_persist", default=False
)


@contextlib.contextmanager
def no_persist():
    """A scope in which nothing that checks :func:`persisting` writes state."""
    token = _NO_PERSIST.set(True)
    try:
        yield
    finally:
        _NO_PERSIST.reset(token)


def persisting() -> bool:
    """``False`` inside :func:`no_persist`, else ``True``."""
    return not _NO_PERSIST.get()
