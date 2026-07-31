"""Deprecation machinery for CAMBER's public API.

CAMBER commits to Semantic Versioning from 1.0 (see ``docs/API-STABILITY.md``): a public
name is never removed or broken in a patch/minor release without first going through a
deprecation period. This module is the mechanism that makes that promise operational —
a deprecated name keeps working but emits a ``DeprecationWarning`` that names the version
it goes away in and what to use instead.

This module is itself **private** (leading-underscore): it is plumbing, not public API.

Typical use::

    from camber._deprecation import deprecated

    @deprecated(since="1.2", remove_in="2.0", use="camber.newmod.new_fn")
    def old_fn(...):
        ...

or, for a call site that isn't a whole function (e.g. a deprecated keyword or a class
constructor path)::

    from camber._deprecation import warn_deprecated

    warn_deprecated("old_fn(mode=...)", since="1.2", remove_in="2.0", use="the `policy=` argument")
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import TypeVar

__all__ = ["deprecated", "warn_deprecated"]

_T = TypeVar("_T")


def _message(name: str, *, since: str, remove_in: str, use: str | None) -> str:
    msg = f"{name} is deprecated since CAMBER {since} and will be removed in CAMBER {remove_in}."
    if use:
        msg += f" Use {use} instead."
    return msg


def warn_deprecated(
    name: str,
    *,
    since: str,
    remove_in: str,
    use: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Emit a standard ``DeprecationWarning`` for a deprecated public name or code path.

    Parameters mirror :func:`deprecated`. ``stacklevel`` is passed through to
    :func:`warnings.warn` so the warning points at the *caller's* line, not this helper;
    the default of ``2`` is right when calling this directly from the deprecated function.
    """
    warnings.warn(
        _message(name, since=since, remove_in=remove_in, use=use),
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def deprecated(
    *,
    since: str,
    remove_in: str,
    use: str | None = None,
) -> Callable[[_T], _T]:
    """Mark a function or class as deprecated.

    Wrapping preserves the original signature, name, and docstring (a deprecation note is
    appended to ``__doc__``), and attaches a machine-readable ``__deprecated__`` dict
    ``{"since", "remove_in", "use"}`` for tooling. Calling the wrapped object emits a
    :class:`DeprecationWarning` pointing at the caller.

    Parameters
    ----------
    since:
        The CAMBER version in which the name became deprecated (e.g. ``"1.2"``).
    remove_in:
        The CAMBER version in which the name will be removed (e.g. ``"2.0"``). Per the
        policy this is at least one minor release away.
    use:
        Optional replacement to point users at (a dotted path or a short phrase).
    """

    def decorate(obj: _T) -> _T:
        meta = {"since": since, "remove_in": remove_in, "use": use}
        note = f"\n\n.. deprecated:: {since}\n    Will be removed in CAMBER {remove_in}."
        if use:
            note += f" Use ``{use}`` instead."

        if isinstance(obj, type):
            # Deprecate a class by warning on construction; leave the type otherwise intact
            # so isinstance()/subclassing still work during the deprecation window.
            orig_init = obj.__init__

            @functools.wraps(orig_init)
            def __init__(self, *args, **kwargs):  # noqa: N807 (dunder wrapper)
                warn_deprecated(
                    obj.__qualname__, since=since, remove_in=remove_in, use=use, stacklevel=2
                )
                orig_init(self, *args, **kwargs)

            obj.__init__ = __init__
            obj.__deprecated__ = meta
            obj.__doc__ = (obj.__doc__ or "") + note
            return obj

        @functools.wraps(obj)  # type: ignore[arg-type]
        def wrapper(*args, **kwargs):
            warn_deprecated(
                getattr(obj, "__qualname__", str(obj)),
                since=since,
                remove_in=remove_in,
                use=use,
                stacklevel=2,
            )
            return obj(*args, **kwargs)  # type: ignore[operator]

        wrapper.__deprecated__ = meta  # type: ignore[attr-defined]
        wrapper.__doc__ = (obj.__doc__ or "") + note
        return wrapper  # type: ignore[return-value]

    return decorate
