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
- **research-only (NC/ND) data** is ``refused_licence`` -- no override, in every adapter;
- **no-persist**: the handler runs inside :func:`camber._nopersist.no_persist`, so a config run
  writes no fault history, report file, manifest entry or cache (#276);
- **trust floor**: ``ctx.min_trust`` (0.5 by default) reaches trust-aware code through
  :func:`run_config_for`;
- **caps**: a list longer than ``caps.max_rows`` is cut (``truncated=True`` and a banner), data
  whose JSON is larger than ``caps.max_bytes`` is ``too_large``, and a call that runs longer than
  ``caps.max_seconds`` is ``timeout``;
- **citations**: ``text`` is checked against ``facts`` with :func:`camber.agent.verify.check`
  (strict: an unknown ``[id]`` or a figure no cited fact carries is removed, with a banner);
- **no leaks**: any other exception becomes ``CapabilityError("internal")`` whose message holds
  no absolute local path.

The timeout runs the handler in a worker thread (a daemon thread with a copy of the caller's
context). Python cannot kill a thread: on a timeout the call returns ``timeout`` at once, while
the handler runs on in the background until it returns, and its result is discarded. Handlers
are read-only and no-persist, so a late finish changes nothing; a handler that loops for ever
holds one thread until the process exits.

**Adding a capability is a one-file job.** Add a module ``camber/capabilities/_cap_<name>.py``
that defines ``CAPABILITIES = (Capability(...),)`` (and optionally ``PROMPTS = (Prompt(...),)``);
:func:`default_registry` imports every ``_cap_*`` module in this package, in name order. A
handler takes ``(ctx, arguments)``: ``arguments`` is already valid against its input schema and
every facility it names is allowed and licence-clean. It names facilities only through
``facility_id`` / ``facility_ids`` arguments, keeps a listing to ``ctx.allowed_facilities`` (and
leaves out research-only ones, see :func:`research_only`), calls :func:`run_config_for` for any
config run, and puts every figure its ``text`` states into a fact it cites. See
docs/CAPABILITY-REGISTRY.md.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import os
import pkgutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ._policy import redact, research_only, run_config_for

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
    "not_found",
    "not_allowed",
    "refused_licence",
    "too_large",
    "timeout",
    "internal",
)


@dataclass(frozen=True)
class Caps:
    """Limits on one call."""

    max_rows: int = 5000  # rows in any tabular part of a result
    max_bytes: int = 2_000_000  # serialized JSON size of CapabilityResult.data
    max_seconds: float = 120.0  # wall-clock budget of one call


@dataclass(frozen=True)
class CallContext:
    """Who is calling and what they may read. ``allowed_facilities`` is required: empty means no
    facility is visible."""

    store: str  # ParquetStore root the call reads
    allowed_facilities: frozenset[str]
    config: str | None = None  # optional run/report config path
    min_trust: float = 0.5
    caps: Caps = Caps()
    caller: str = "local"  # free-text label for audit/logs (e.g. "mcp:claude-code")

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


@dataclass(frozen=True)
class Capability:
    """One read-only operation."""

    name: str  # snake_case, stable, e.g. "facilities", "rule_run"
    title: str
    description: str  # written for an LLM tool picker: what, when, what it returns
    input_schema: dict  # JSON Schema 2020-12 object schema
    output_schema: dict  # JSON Schema 2020-12 schema of CapabilityResult.data
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
    """Cut ``data`` (a list) or each top-level list in ``data`` (an object) to ``max_rows``;
    returns the data and a banner per cut list."""
    banners: list = []

    def cut(rows, label):
        if len(rows) <= max_rows:
            return rows
        banners.append(
            f"{label} truncated: showing the first {max_rows} of {len(rows)} rows "
            "(narrow the query to see the rest)"
        )
        return list(rows[:max_rows])

    if isinstance(data, (list, tuple)):
        return cut(data, "result"), banners
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
        if cap.input_schema.get("type") != "object":
            raise ValueError(f"{cap.name}.input_schema must be an object schema")
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

    def get(self, name: str) -> Capability:
        """The capability ``name``; ``CapabilityError("not_found")`` when there is none."""
        cap = self._caps.get(name) if isinstance(name, str) else None
        if cap is None:
            raise CapabilityError("not_found", f"no capability named {name!r}")
        return cap

    def call(self, name: str, arguments: dict, ctx: CallContext) -> CapabilityResult:
        """Validate ``arguments``, run the handler under the policy, check the result.

        Never raises anything but :class:`CapabilityError`; see the module docstring.
        """
        # the call's own paths, absolute, are redacted even where the generic pattern would miss
        secrets = [
            os.path.abspath(p)
            for p in (getattr(ctx, "store", None), getattr(ctx, "config", None))
            if p
        ]
        try:
            return self._call(name, arguments, ctx)
        except CapabilityError as e:
            raise CapabilityError(e.code, redact(e.message, *secrets), data=e.data) from None
        except Exception as e:  # noqa: BLE001 - nothing else may escape to a transport
            msg = redact(f"{type(e).__name__}: {e}", *secrets)
            raise CapabilityError("internal", f"capability {name!r} failed: {msg}") from None

    def _call(self, name, arguments, ctx: CallContext) -> CapabilityResult:
        from ._policy import check_arguments, outside_allowlist
        from ._schema import validate

        cap = self.get(name)
        if not isinstance(ctx, CallContext):
            raise TypeError("ctx must be a CallContext")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise CapabilityError("invalid_params", "arguments must be an object")
        errors = validate(arguments, cap.input_schema)
        if errors:
            raise CapabilityError(
                "invalid_params", "; ".join(errors[:5]), data={"errors": errors[:50]}
            )
        check_arguments(ctx, arguments)

        res = self._run(cap, ctx, dict(arguments))
        if not isinstance(res, CapabilityResult):
            raise TypeError(f"handler returned {type(res).__name__}, not a CapabilityResult")
        data, banners = _truncate(res.data, ctx.caps.max_rows)
        errors = validate(data, cap.output_schema)
        if errors:
            raise ValueError("result does not match the output schema: " + "; ".join(errors[:3]))
        stray = outside_allowlist(data, ctx.allowed_facilities)
        if stray is not None:
            raise ValueError(f"result names facility {stray!r} outside the allowlist")
        size = len(json.dumps(data, allow_nan=False, sort_keys=True).encode("utf-8"))
        if size > ctx.caps.max_bytes:
            raise CapabilityError(
                "too_large",
                f"the result is {size:,} bytes, over this caller's limit of "
                f"{ctx.caps.max_bytes:,}; narrow the query",
                data={"bytes": size, "max_bytes": ctx.caps.max_bytes},
            )
        text, cite_banners = _grounded(res.text, res.facts)
        return replace(
            res,
            data=data,
            text=text,
            facts=list(res.facts),
            banners=[*res.banners, *banners, *cite_banners],
            truncated=bool(res.truncated or banners),
        )

    @staticmethod
    def _run(cap: Capability, ctx: CallContext, arguments: dict) -> CapabilityResult:
        """The handler in a worker thread, no-persist, within ``ctx.caps.max_seconds``."""
        from .._nopersist import no_persist

        box: dict = {}

        def work():
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
        return box["result"]


def _grounded(text: str, facts: list) -> tuple[str, list]:
    """``text`` checked against ``facts`` (strict); a banner when anything was removed."""
    from ..agent.verify import check

    g = check(str(text or ""), _Facts(list(facts or [])), strict=True, source="template")
    if not g.flagged:
        return g.text, []
    return g.text, [
        f"{len(g.flagged)} statement(s) of the summary were removed: they cite no fact that "
        "supports them"
    ]


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
