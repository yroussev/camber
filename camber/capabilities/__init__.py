"""camber.capabilities -- one read-only core the MCP and A2A servers expose (0.110, #275).

**Provisional** (see docs/API-STABILITY.md). A :class:`Capability` is a named, read-only
operation with JSON Schema 2020-12 input and output schemas and a handler that returns a
:class:`CapabilityResult`: structured ``data``, a short ``text`` that cites fact ids
(``[F1@facilities]``), the ``facts`` those ids name, and ``banners`` always shown to the user. A
:class:`Registry` holds them; every transport (the MCP server, later the A2A server) is a thin
adapter over :meth:`Registry.call`, so policy is enforced in one place:

- **input validation** against ``input_schema`` (``invalid_params``);
- **facility allowlist**: a ``facility_id`` / ``facility_ids`` argument outside
  ``ctx.allowed_facilities`` is ``not_allowed``, and a listing shows only allowed facilities;
- **research-only (NC/ND) data** is ``refused_licence`` -- no override, in every adapter -- and
  so is a facility whose licence cannot be established (a missing or damaged facility registry,
  #290), with a banner on every result while the registry is damaged;
- **no-persist**: the handler runs inside :func:`camber._nopersist.no_persist`, so a config run
  writes no fault history, report file, manifest entry or cache (#276);
- **trust floor**: ``ctx.min_trust`` (0.5 by default) reaches trust-aware code through
  :func:`run_config_for`;
- **pages and caps** (0.111, #299): a capability returns a model-sized page of each list by
  default (``limit`` / ``offset`` arguments ask for others) and cuts a list only through
  :func:`camber.capabilities._page.page`, which records a notice; every recorded notice is a
  banner and sets ``truncated=True``. A list longer than ``caps.max_rows`` is cut the same way, a
  result whose JSON (``data``, ``text``, ``facts`` and ``banners`` together) is larger than
  ``caps.max_bytes`` is ``too_large``, and a call that runs longer than ``caps.max_seconds`` is
  ``timeout``;
- **fact ids unique per call**: every fact id's context gets a token (:func:`call_token`, a hash
  of the name, the arguments, the allowlist, the run config, the trust floor and the store
  generation, #295), ``F1@points`` -> ``F1@points-1a2b3c``, in ``facts``, ``text`` and ``data``;
- **citations**: ``text`` is checked against ``facts`` with :func:`camber.agent.verify.check`
  (strict: a sentence stating a figure no cited fact carries is removed, and so is an unknown
  ``[id]``, each with a banner that says which);
- **text from the data** (0.111, #291): ``text``, every fact's ``text``, every banner and every
  error message are one line with no control, bidi or invisible character, only the handler's
  own fact ids are citations in ``text`` (any other ``[F1@x]`` reads ``(F1@x)``), a value quoted
  with :func:`camber.capabilities._sanitize.name` is one word to the citation check, and no
  string in ``data`` or ``facts`` is longer than 2,000 characters (see
  :mod:`camber.capabilities._sanitize`);
- **store errors**: a read of stored data that fails (a damaged or unreadable part file, or one a
  concurrent ingest replaced) or any other ``OSError`` is ``unavailable``, with ``data``
  ``{"retryable": bool, "facility_id": ...}`` (the facility only when the caller may see it); so
  is a call that a writer replacing or removing files of a facility it reads overlapped (a forced
  re-ingest, a prune: the facility's write fence, :mod:`camber.store._fence`, moved), retryable,
  whatever the handler answered (#306);
- **no leaks**: any other exception becomes ``CapabilityError("internal")`` whose message holds
  no absolute local path.

The timeout runs the handler in a worker thread (a daemon thread with a copy of the caller's
context). Python cannot kill a thread: on a timeout the call returns ``timeout`` at once, while
the handler runs on in the background until it returns, and its result is discarded. Handlers
are read-only and no-persist, so a late finish changes nothing; a handler that loops for ever
holds one thread until the process exits. ``camber mcp`` runs each call in a worker process that
calls this method and is killed on a timeout, a cancel or a disconnect
(:class:`camber.mcp.WorkerPool`, #292).

**Adding a capability is a one-file job.** Add a module ``camber/capabilities/_cap_<name>.py``
that defines ``CAPABILITIES = (Capability(...),)`` (and optionally ``PROMPTS = (Prompt(...),)``);
:func:`default_registry` imports every ``_cap_*`` module in this package, in name order. A
handler takes ``(ctx, arguments)``: ``arguments`` is already valid against its input schema and
every facility it names is allowed and licence-clean. It names facilities only through
``facility_id`` / ``facility_ids`` arguments, keeps a listing to ``ctx.allowed_facilities`` (and
leaves out research-only ones, see :func:`research_only`), calls :func:`run_config_for` for any
config run, puts every figure its ``text`` states into a fact it cites, and writes each name
from the data with :func:`camber.capabilities._sanitize.name`. See docs/CAPABILITY-REGISTRY.md.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib
import json
import os
import pkgutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ._page import collect
from ._policy import known_roots, redact, redact_value, research_only, run_config_for
from ._sanitize import (
    cap_strings,
    finish,
    hide_spans,
    lead,
    one_line,
    plain,
    show_spans,
    shown,
    unmark,
)

__all__ = [
    "ERROR_CODES",
    "Caps",
    "CallContext",
    "CapabilityResult",
    "CapabilityError",
    "Capability",
    "PromptArg",
    "Prompt",
    "Registry",
    "default_registry",
    "research_only",
    "run_config_for",
]

#: every :attr:`CapabilityError.code`
ERROR_CODES = (
    "invalid_params",
    "unknown_capability",
    "not_found",
    "not_allowed",
    "refused_licence",
    "too_large",
    "timeout",
    "unavailable",
    "internal",
)


@dataclass(frozen=True)
class Caps:
    """Limits on one call."""

    max_rows: int = 5000  # rows in any tabular part of a result
    max_bytes: int = 2_000_000  # serialized JSON size of the whole result (and an MCP reply)
    max_seconds: float = 120.0  # wall-clock budget of one call


@dataclass(frozen=True)
class CallContext:
    """Who is calling and what they may read. ``allowed_facilities`` is required: empty means no
    facility is visible."""

    store: str  # ParquetStore root the call reads
    allowed_facilities: frozenset[str]
    config: str | tuple[str, ...] | None = None  # run/report config path(s), one per facility
    min_trust: float = 0.5
    caps: Caps = Caps()
    caller: str = "local"  # free-text label for audit/logs (e.g. "mcp:claude-code")
    # 0.111 (#308): ((path, loaded config), ...) as checked when the server started; the calls
    # read a config named here from this snapshot, never from the file again
    config_snapshot: tuple = field(default=(), compare=False, repr=False)

    def __post_init__(self):
        allowed = self.allowed_facilities
        if allowed is None or isinstance(allowed, str):
            raise ValueError("allowed_facilities must be a set of facility ids (empty: none)")
        object.__setattr__(self, "allowed_facilities", frozenset(str(f) for f in allowed))
        if not 0.0 <= float(self.min_trust) <= 1.0:
            raise ValueError(f"min_trust must be between 0 and 1, got {self.min_trust!r}")


@dataclass(frozen=True)
class CapabilityResult:
    """What a capability returns: ``data`` matches its output schema; ``text`` cites ``facts``."""

    data: Any  # JSON-serializable structured result (matches output_schema)
    text: str  # short human/LLM-readable summary; cites fact ids like [F1@ctx]
    facts: list[dict]  # fact index entries: {"id": "F1@ctx", "kind": ..., "value": ..., ...}
    banners: list[str] = field(default_factory=list)  # licence / trust / truncation notices
    truncated: bool = False


class CapabilityError(Exception):
    """A refused or failed call. ``code`` is one of :data:`ERROR_CODES`."""

    def __init__(self, code: str, message: str, *, data: dict | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown capability error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class _SchemaError(CapabilityError):
    """An ``invalid_params`` refusal of the arguments by the input schema: its JSON pointers
    (``/facility_id: ...``) are not paths, so it is not redacted."""


@dataclass(frozen=True)
class Capability:
    """One read-only operation."""

    name: str  # snake_case, stable, e.g. "facilities", "rule_run"
    title: str
    description: str  # written for an LLM tool picker: what, when, what it returns
    input_schema: dict  # JSON Schema 2020-12 object schema
    output_schema: dict  # JSON Schema 2020-12 object schema of CapabilityResult.data
    handler: Callable[[CallContext, dict], CapabilityResult]
    long_running: bool = False  # a candidate for MCP Tasks (0.111) / A2A tasks (0.112)


@dataclass(frozen=True)
class PromptArg:
    """One argument of a :class:`Prompt`."""

    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class Prompt:
    """A prompt template an adapter offers (MCP prompts)."""

    name: str
    title: str
    description: str
    arguments: tuple[PromptArg, ...]
    render: Callable[[dict], list[dict]]  # -> [{"role": "user"|"assistant", "text": str}, ...]


class _Facts:
    """The ``facts`` of a result as the grounding whitelist :func:`camber.agent.verify.check`
    reads (``ids()``, ``by_id()``, each fact with ``text`` and ``data``)."""

    @dataclass(frozen=True)
    class _Fact:
        id: str
        text: str
        data: dict

    def __init__(self, facts: list):
        self._by_id: dict = {}
        for f in facts:
            if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not f["id"]:
                raise ValueError("every fact must be an object with a non-empty string 'id'")
            if f["id"] in self._by_id:
                raise ValueError(f"duplicate fact id {f['id']!r}")
            data = {k: v for k, v in f.items() if k not in ("id", "text")}
            self._by_id[f["id"]] = self._Fact(f["id"], str(f.get("text", "")), data)

    def ids(self) -> list:
        return list(self._by_id)

    def by_id(self, fid: str):
        return self._by_id.get(fid)


def _truncate(data, max_rows: int) -> tuple[Any, list]:
    """Cut each top-level list in ``data`` (an object) to ``max_rows``; returns the data and a
    banner per cut list."""
    banners: list = []

    def cut(rows, label):
        if len(rows) <= max_rows:
            return rows
        banners.append(
            f"{label} truncated: showing the first {max_rows} of {len(rows)} rows "
            "(narrow the query to see the rest)"
        )
        return list(rows[:max_rows])

    if isinstance(data, dict):
        out = {k: cut(v, f"'{k}'") if isinstance(v, (list, tuple)) else v for k, v in data.items()}
        return out, banners
    return data, banners


class Registry:
    """The capabilities and prompts one adapter serves, and the policy around every call."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self._prompts: dict[str, Prompt] = {}

    def register(self, cap: Capability) -> None:
        """Add ``cap``; a duplicate name or a schema outside the supported subset is a
        ``ValueError``."""
        from ._schema import check_schema

        if cap.name in self._caps:
            raise ValueError(f"capability {cap.name!r} is already registered")
        if not cap.name or not cap.name.replace("_", "a").isalnum() or cap.name != cap.name.lower():
            raise ValueError(f"capability name {cap.name!r} must be snake_case")
        check_schema(cap.input_schema, f"{cap.name}.input_schema")
        check_schema(cap.output_schema, f"{cap.name}.output_schema")
        for which in ("input_schema", "output_schema"):
            # MCP (2025-11-25) takes only an object outputSchema / structuredContent
            if getattr(cap, which).get("type") != "object":
                raise ValueError(f"{cap.name}.{which} must be an object schema")
        self._caps[cap.name] = cap

    def register_prompt(self, prompt: Prompt) -> None:
        """Add ``prompt``; a duplicate name is a ``ValueError``."""
        if prompt.name in self._prompts:
            raise ValueError(f"prompt {prompt.name!r} is already registered")
        self._prompts[prompt.name] = prompt

    def capabilities(self) -> list[Capability]:
        """Every capability, sorted by name."""
        return [self._caps[n] for n in sorted(self._caps)]

    def prompts(self) -> list[Prompt]:
        """Every prompt, sorted by name."""
        return [self._prompts[n] for n in sorted(self._prompts)]

    def complete(
        self, prompt: str, argument: str, value: str, context: dict | None, ctx: CallContext
    ) -> tuple[list[str], int]:
        """``(values, total)`` completing a prompt argument (MCP ``completion/complete``, 0.111,
        #304): at most :data:`MAX_COMPLETIONS` values starting with ``value``, then those
        containing it, case-insensitive.

        ``facility_id`` completes from ``ctx.allowed_facilities`` (less the facilities a call
        would refuse); ``equip`` and ``equips`` (its last comma-separated id) from the stored
        equipment of ``context["facility_id"]`` when a call could read that facility, else to
        nothing. A name that is not one clean line of at most 200 characters is never offered
        (the prompt could not take it whole), nor one the sanitiser would change (HTML or markdown
        markup, a URL, a fact-id bracket) or one that starts with ``NOTICE`` (#307). Any other
        argument completes to nothing; an unknown prompt or argument is
        ``CapabilityError("invalid_params")``.
        """
        p = self._prompts.get(prompt) if isinstance(prompt, str) else None
        if p is None:
            raise CapabilityError("invalid_params", f"no prompt named {prompt!r}")
        if argument not in {a.name for a in p.arguments}:
            raise CapabilityError(
                "invalid_params", f"prompt {prompt!r} has no argument {argument!r}"
            )
        value = str(value or "")
        head = ""
        if argument == "equips":
            head, _, value = value.rpartition(",")
            head = ", ".join(e.strip() for e in head.split(",") if e.strip())
            head, value = (f"{head}, " if head else ""), value.strip()
        if argument == "facility_id":
            pool = [f for f in ctx.allowed_facilities if _readable(ctx, f)]
        elif argument in ("equip", "equips"):
            fid = str((context or {}).get("facility_id") or "")
            pool = _equipment(ctx, fid) if fid and _readable(ctx, fid) else []
        else:
            pool = []
        # #307: a name the sanitiser would change (markup, a link, a fact-id bracket) or one that
        # reads as a NOTICE is not offered: shown sanitised, it would name nothing
        pool = [
            s for s in pool if s == shown(s) and s == lead(s) and 0 < len(s) <= 200 and "," not in s
        ]
        low = value.lower()
        hits = sorted(
            (s for s in pool if low in s.lower()),
            key=lambda s: (not s.lower().startswith(low), s.lower(), s),
        )
        return [head + s for s in hits[:MAX_COMPLETIONS]], len(hits)

    def get(self, name: str) -> Capability:
        """The capability ``name``; ``CapabilityError("unknown_capability")`` when there is none
        (``not_found`` is a capability's own "nothing found")."""
        cap = self._caps.get(name) if isinstance(name, str) else None
        if cap is None:
            raise CapabilityError("unknown_capability", f"no capability named {name!r}")
        return cap

    def call(self, name: str, arguments: dict, ctx: CallContext) -> CapabilityResult:
        """Validate ``arguments``, run the handler under the policy, check the result.

        Never raises anything but :class:`CapabilityError`; see the module docstring.
        """
        # the known roots (the call's store and config, home, temp ...) go by exact prefix
        # before the generic pattern, which a path with a space or a comma would get past
        secrets = known_roots(ctx)
        try:
            fence = _fence_snapshot(arguments, ctx)
            try:
                res = self._call(name, arguments, ctx)
            except _SchemaError:
                raise
            except Exception:
                _fence_check(ctx, fence)  # an error a concurrent replace caused is unavailable
                raise
            _fence_check(ctx, fence)
            # the quoted spans keep their marks until here, so a name from the data gets the
            # known roots only (#307), then the marks go
            return replace(
                res,
                text=unmark(redact(res.text, *secrets)),
                banners=[unmark(redact(b, *secrets)) for b in res.banners],
            )
        except _SchemaError as e:  # built from the schema and the caller's own arguments
            raise CapabilityError(e.code, e.message, data=e.data) from None
        except CapabilityError as e:
            data = _redact_data(e.data, secrets)
            # one line: data cannot add a NOTICE; the spans' marks kept for redact (#307)
            msg = unmark(redact(one_line(e.message, keep_marks=True), *secrets))
            raise CapabilityError(e.code, msg, data=data) from None
        except Exception as e:  # noqa: BLE001 - nothing else may escape to a transport
            store_err = _unavailable(e, ctx)
            if store_err is not None:
                raise store_err from None
            msg = redact(one_line(f"{type(e).__name__}: {e}"), *secrets)
            raise CapabilityError("internal", f"capability {name!r} failed: {msg}") from None

    def _call(self, name, arguments, ctx: CallContext) -> CapabilityResult:
        from ._policy import check_arguments, outside_allowlist, registry_notice
        from ._schema import as_integers, validate

        cap = self.get(name)
        if not isinstance(ctx, CallContext):
            raise TypeError("ctx must be a CallContext")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise CapabilityError("invalid_params", "arguments must be an object")
        errors = validate(arguments, cap.input_schema)
        if errors:
            raise _SchemaError(
                "invalid_params", "; ".join(errors[:5]), data={"errors": errors[:50]}
            )
        arguments = as_integers(arguments, cap.input_schema)  # #308: 5.0 runs as 5
        check_arguments(ctx, arguments)

        res, cuts = self._run(cap, ctx, dict(arguments))
        if not isinstance(res, CapabilityResult):
            raise TypeError(f"handler returned {type(res).__name__}, not a CapabilityResult")
        data, banners = _truncate(res.data, ctx.caps.max_rows)
        banners = [*cuts, *(b for b in banners if b not in cuts)]
        token = call_token(cap.name, arguments, ctx)
        facts_in, text_in, data = _sanitized(res, data)
        facts, text_in, data = _scoped(facts_in, text_in, data, token)
        errors = validate(data, cap.output_schema)
        if errors:
            raise ValueError("result does not match the output schema: " + "; ".join(errors[:3]))
        stray = outside_allowlist(data, ctx.allowed_facilities)
        if stray is not None:
            raise ValueError(f"result names facility {stray!r} outside the allowlist")
        json.dumps(data, allow_nan=False)  # NaN / inf in data is the handler's bug: internal
        hidden, spans = hide_spans(text_in)  # a quoted data value is one word to the filter
        text, cite_banners = _grounded(hidden, facts)
        text = lead(show_spans(text, spans))  # the marks stay for call()'s redact (#307)
        notice = registry_notice(ctx)  # 0.111 (#290): a damaged registry is said on every result
        own = [finish(b) for b in [*(res.banners or []), *banners]]
        out = replace(
            res,
            data=data,
            text=text,
            facts=facts,
            banners=[*([notice] if notice else []), *own, *cite_banners],
            truncated=bool(res.truncated or banners),
        )
        _too_large(_result_bytes(out), ctx.caps.max_bytes)
        return out

    @staticmethod
    def _run(cap: Capability, ctx: CallContext, arguments: dict) -> tuple:
        """``(result, notices)``: the handler in a worker thread, no-persist, within
        ``ctx.caps.max_seconds``, and the notices of what its pages left out."""
        from .._nopersist import no_persist

        box: dict = {}

        def work():
            box["cuts"], _token = collect()
            try:
                with no_persist():
                    box["result"] = cap.handler(ctx, arguments)
            except BaseException as e:  # noqa: BLE001 - re-raised in the calling thread
                box["error"] = e

        context = contextvars.copy_context()
        t = threading.Thread(
            target=context.run, args=(work,), name=f"capability-{cap.name}", daemon=True
        )
        t.start()
        t.join(ctx.caps.max_seconds)
        if t.is_alive():
            raise CapabilityError(
                "timeout",
                f"{cap.name!r} ran longer than {ctx.caps.max_seconds:g} s and was abandoned",
                data={"max_seconds": ctx.caps.max_seconds},
            )
        err = box.get("error")
        if err is not None:
            if not isinstance(err, Exception):  # SystemExit, KeyboardInterrupt from a handler
                raise RuntimeError(f"the handler raised {type(err).__name__}")
            raise err
        return box["result"], list(box["cuts"])


#: the most values one completion returns (the MCP limit)
MAX_COMPLETIONS = 100


def _readable(ctx: CallContext, facility_id: str) -> bool:
    """Whether a call naming ``facility_id`` gets past the allowlist and the licence check: a
    completion never offers what a call would refuse."""
    from ._policy import check_facility

    try:
        check_facility(ctx, facility_id)
    except Exception:  # noqa: BLE001 - refused, or a store problem: nothing to offer
        return False
    return True


def _equipment(ctx: CallContext, facility_id: str) -> list:
    """The stored equipment ids of one facility, read without writing anything."""
    from .._nopersist import no_persist
    from ._policy import open_store

    try:
        with no_persist():
            eqs = open_store(ctx).equipment(facility_id=facility_id).get(facility_id) or {}
    except Exception:  # noqa: BLE001 - a store problem: nothing to offer
        return []
    return [str(e) for e in eqs]


def _result_bytes(res: CapabilityResult) -> int:
    """The size of ``res`` as compact JSON: its data, text, facts and banners together."""
    whole = {"data": res.data, "text": res.text, "facts": res.facts, "banners": res.banners}
    text = json.dumps(whole, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(text.encode("utf-8", "backslashreplace"))


def _too_large(size: int, max_bytes: int) -> None:
    """``CapabilityError("too_large")`` when ``size`` bytes is over ``max_bytes``."""
    if size > max_bytes:
        raise CapabilityError(
            "too_large",
            f"the result is {size:,} bytes, over this caller's limit of {max_bytes:,}; ask for "
            "a smaller page (limit) or narrow the query",
            data={"bytes": size, "max_bytes": max_bytes},
        )


def _fence_snapshot(arguments, ctx) -> dict:
    """The write fences of the facilities a call reads (those its arguments name, else every
    allowed one), taken before it runs (#306): a writer replacing one of them now is a retryable
    ``StoreReadError`` (after a short wait), so a call never starts on a half-swapped facility."""
    from ..store import _fence
    from ._policy import named_facilities

    store = getattr(ctx, "store", None)
    if not isinstance(store, str) or not store:
        return {}
    allowed: frozenset = getattr(ctx, "allowed_facilities", frozenset())
    args = arguments if isinstance(arguments, dict) else {}
    named = [f for f in named_facilities(args) if f in allowed]
    return _fence.snapshot(store, set(named or allowed))


def _fence_check(ctx, fence: dict) -> None:
    """Raise a retryable ``StoreReadError`` when a writer replaced or removed files of a facility
    in ``fence`` while the call ran: its answer may mix old, new and missing data (#306)."""
    from ..store import _fence

    moved = _fence.changed(ctx.store, fence) if fence else None
    if moved is not None:
        raise _fence.changed_error(moved)


def _unavailable(exc: BaseException, ctx) -> CapabilityError | None:
    """``CapabilityError("unavailable")`` for a store read that failed (#301): a
    :class:`~camber.store.parquet_store.StoreReadError` or any other ``OSError``; ``None`` for
    anything else. The message names no path and no facility the caller may not see."""
    from ..store.parquet_store import StoreReadError

    if isinstance(exc, StoreReadError):
        fid, retryable, reason = exc.facility_id, exc.retryable, exc.reason
    elif isinstance(exc, OSError):
        fid, retryable = None, isinstance(exc, FileNotFoundError)
        reason = f"the store could not be read ({type(exc).__name__})"
    else:
        return None
    named = fid if fid in getattr(ctx, "allowed_facilities", frozenset()) else None
    data: dict = {"retryable": retryable}
    if named is not None:
        data["facility_id"] = named
    where = f"the stored data of facility {named!r}" if named else "the stored data"
    hint = (
        "retry the call"
        if retryable
        else "a retry will not help until the file is repaired or the facility is re-ingested"
    )
    return CapabilityError("unavailable", f"{where} cannot be read: {reason}; {hint}", data=data)


def call_token(name: str, arguments: dict, ctx: CallContext) -> str:
    """Six hex digits of SHA-256 over the capability name, the canonical JSON of the validated
    arguments, the sorted allowlist and the server's state (:func:`_server_state`: the effective
    config, the trust floor and the store generation): the same call on the same server and
    unchanged data always gives the same token, across restarts, and two different calls, or one
    call on two servers that would answer it differently, (almost surely) different ones (#295).

    The one place a fact id's token is made, so it can become a server-keyed MAC (#294)."""
    payload = json.dumps(
        [name, arguments, sorted(ctx.allowed_facilities), _server_state(arguments, ctx)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:6]


def _server_state(arguments: dict, ctx: CallContext) -> list:
    """What decides a call's values besides its arguments and the allowlist (#295): a digest of
    the loaded run config (its canonical JSON, not its path; the file's bytes when it does not
    load), the trust floor, and a cheap store generation: the size and modification time of the
    facility registry and the point catalog, and of every file of each touched facility
    partition, part files included, so an in-place rewrite counts (#312; the facilities the
    arguments name, else every allowed one, plus a store-backed config's own facility). Stat
    calls only: nothing is read but the config, and no path enters the token."""
    from .. import __version__
    from ..store.facilities import _FACILITIES
    from ..store.parquet_store import _CATALOG, _CATALOG_GEN, _FACILITY, _partition_signature
    from ._policy import config_paths, named_facilities, read_config

    configs, config_stores = [], []
    for path in config_paths(ctx):
        try:
            loaded = read_config(ctx, path)
            text = json.dumps(loaded, sort_keys=True, separators=(",", ":"), default=str)
            configs.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
            source = loaded.get("source") or {}
            if source.get("kind") == "store" and source.get("facility_id"):
                root = str(source.get("store") or "")
                if not os.path.isabs(root):
                    root = os.path.join(os.path.dirname(os.path.abspath(path)), root)
                config_stores.append((root, str(source["facility_id"])))
        except Exception:  # noqa: BLE001 - a config that does not load: its bytes, or absent
            try:
                with open(path, "rb") as fh:
                    configs.append("raw:" + hashlib.sha256(fh.read()).hexdigest())
            except OSError:
                configs.append("missing")

    def signature(root: str, fid: str) -> list:
        fdir = os.path.join(os.path.abspath(root), f"{_FACILITY}={fid}")
        return [fid, list(_partition_signature(fdir))]

    def stat(root: str, name: str):
        try:
            st = os.stat(os.path.join(root, name))
            return [st.st_mtime_ns, st.st_size]
        except (OSError, TypeError):  # absent, or no store
            return None

    named = [f for f in named_facilities(arguments) if f in ctx.allowed_facilities]
    store = [
        [stat(ctx.store, n) for n in (_FACILITIES, _CATALOG, _CATALOG_GEN)],
        *(signature(ctx.store, f) for f in sorted(set(named or ctx.allowed_facilities))),
    ]
    for root, fid in config_stores:
        store.append([*signature(root, fid), [stat(root, n) for n in (_CATALOG, _CATALOG_GEN)]])
    # the package version: with no config a catalog facility runs its packaged template (#296)
    return [__version__, configs, float(ctx.min_trust), store]


def _scoped(facts: list, text: str, data, token: str) -> tuple:
    """Fact ids made unique to one call: ``F1@rule_run`` becomes ``F1@rule_run-<token>`` (an id
    with no context gets ``@<token>``), in ``facts``, in the ``[id]`` citations of ``text`` and
    wherever ``data`` holds an id as a string value (a row's ``fact``, a fact index's ``id``)."""
    from ..agent.verify import _CITE

    mapping: dict = {}
    out: list = []
    for f in facts:
        fid = f.get("id") if isinstance(f, dict) else None
        if not isinstance(fid, str) or not fid:
            out.append(f)  # refused by the citation check as malformed
            continue
        base, _sep, where = fid.partition("@")
        new = f"{base}@{where}-{token}" if where else f"{base}@{token}"
        mapping[fid] = new
        out.append({**f, "id": new})

    def sub(m):
        return f"[{mapping.get(m.group(1), m.group(1))}]"

    def walk(x, depth=0):
        if depth > 20:
            return x
        if isinstance(x, str):
            return mapping.get(x, x)
        if isinstance(x, dict):
            return {k: walk(v, depth + 1) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [walk(v, depth + 1) for v in x]
        return x

    return out, _CITE.sub(sub, text), walk(data) if mapping else data


def _sanitized(res: CapabilityResult, data) -> tuple:
    """``(facts, text, data)`` of ``res`` with the data in them made safe (0.111, #291): the text
    one line with only the handler's own fact ids as citations (see
    :func:`~camber.capabilities._sanitize.finish`), each fact's text one line with no citation,
    and no string in ``data`` or ``facts`` longer than :data:`~._sanitize.MAX_VALUE`."""
    facts = cap_strings(list(res.facts or []))
    own = {f["id"] for f in facts if isinstance(f, dict) and isinstance(f.get("id"), str)}
    facts = [
        {**f, "text": unmark(plain(finish(f["text"])))}
        if isinstance(f, dict) and "text" in f
        else f
        for f in facts
    ]
    return facts, plain(finish(str(res.text or ""), known_ids=own)), cap_strings(data)


def _redact_data(data, secrets: list, depth: int = 0):
    """``data`` (an error's ``data``) with every string in it path-redacted like the message; a
    value from the data comes marked as a span (see ``_common._safe``) and keeps its slashes."""
    if depth > 20:
        return None
    if isinstance(data, str):
        return unmark(redact_value(data, *secrets))
    if isinstance(data, dict):
        return {
            unmark(redact_value(k, *secrets)) if isinstance(k, str) else k: _redact_data(
                v, secrets, depth + 1
            )
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_redact_data(v, secrets, depth + 1) for v in data]
    return data


def _grounded(text: str, facts: list) -> tuple[str, list]:
    """``text`` checked against ``facts`` (strict); a banner when anything was removed."""
    from ..agent.verify import check

    g = check(str(text or ""), _Facts(list(facts or [])), strict=True, source="template")
    dropped = sum(1 for f in g.flagged if f["reason"] in ("uncited-number", "uncited-sentence"))
    unknown = sum(1 for f in g.flagged if f["reason"] == "unknown-citation")
    banners = []
    if dropped:
        banners.append(
            f"{dropped} sentence(s) of the summary were removed: they state a figure that no "
            "fact they cite carries"
        )
    if unknown:
        banners.append(
            f"{unknown} citation(s) of a fact id this result does not have were removed from "
            "the summary (the sentences around them were kept)"
        )
    return g.text, banners


def default_registry() -> Registry:
    """A registry with the built-in capabilities and prompts (every ``_cap_*`` module)."""
    reg = Registry()
    for mod in sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("_cap_")):
        module = importlib.import_module(f"{__name__}.{mod}")
        for cap in getattr(module, "CAPABILITIES", ()):
            reg.register(cap)
        for prompt in getattr(module, "PROMPTS", ()):
            reg.register_prompt(prompt)
    return reg
