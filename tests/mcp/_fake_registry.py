"""A stand-in for ``camber.capabilities`` with the shapes of the 0.110 registry contract.

The MCP server is tested against this, not the real registry: the tools here are tiny and
deterministic, and each exercises one path of the adapter (a success, a truncation banner, every
domain error, an internal error, a non-object result, a tool that writes to stdout). The module
can stand in for ``camber.capabilities`` itself (:func:`install`), which is how the subprocess
test runs the real ``camber mcp`` command line against it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Caps:
    max_rows: int = 5000
    max_bytes: int = 2_000_000
    max_seconds: float = 120.0


@dataclass(frozen=True)
class CallContext:
    store: str
    allowed_facilities: frozenset
    config: str | None = None
    min_trust: float = 0.5
    caps: Caps = field(default_factory=Caps)
    caller: str = "local"


@dataclass(frozen=True)
class CapabilityResult:
    data: Any
    text: str
    facts: list
    banners: list = field(default_factory=list)
    truncated: bool = False


class CapabilityError(Exception):
    def __init__(self, code: str, message: str, *, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class Capability:
    name: str
    title: str
    description: str
    input_schema: dict
    output_schema: dict
    handler: Callable
    long_running: bool = False


@dataclass(frozen=True)
class PromptArg:
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class Prompt:
    name: str
    title: str
    description: str
    arguments: tuple
    render: Callable


#: every call the registry served, as (name, arguments, ctx): the tests read the context back
CALLS: list = []


class Registry:
    def __init__(self):
        self._caps: dict = {}
        self._prompts: dict = {}

    def register(self, cap):
        if cap.name in self._caps:
            raise ValueError(cap.name)
        self._caps[cap.name] = cap

    def register_prompt(self, prompt):
        self._prompts[prompt.name] = prompt

    def capabilities(self):
        return [self._caps[k] for k in sorted(self._caps)]

    def prompts(self):
        return [self._prompts[k] for k in sorted(self._prompts)]

    def get(self, name):
        if name not in self._caps:
            raise CapabilityError("not_found", f"no capability {name!r}")
        return self._caps[name]

    def call(self, name, arguments, ctx):
        cap = self.get(name)
        CALLS.append((name, dict(arguments), ctx))
        schema = cap.input_schema
        for req in schema.get("required", []):
            if req not in arguments:
                raise CapabilityError("invalid_params", f"missing required argument {req!r}")
        for key, value in arguments.items():
            spec = schema.get("properties", {}).get(key)
            if spec is None:
                raise CapabilityError("invalid_params", f"unknown argument {key!r}")
            want = {"string": str, "integer": int}[spec["type"]]
            if not isinstance(value, want) or isinstance(value, bool):
                raise CapabilityError("invalid_params", f"{key} must be of type {spec['type']}")
        try:
            return cap.handler(ctx, arguments)
        except CapabilityError:
            raise
        except Exception:
            raise CapabilityError("internal", "the capability failed") from None


def _facility_arg(ctx, args):
    fid = args["facility"]
    if fid == "research-only":
        raise CapabilityError(
            "refused_licence",
            "research-only data (CC BY-NC-ND) is never served to an AI client",
            data={"facility": fid, "licence": "CC-BY-NC-ND-4.0"},
        )
    if fid not in ctx.allowed_facilities:
        raise CapabilityError("not_allowed", f"facility {fid!r} is not in this server's allowlist")
    return fid


def _facilities(ctx, args):
    ids = sorted(ctx.allowed_facilities)
    return CapabilityResult(
        data={"facilities": ids},
        text=f"{len(ids)} facilities are visible [F1@ctx].",
        facts=[{"id": "F1@ctx", "kind": "count", "value": len(ids)}],
    )


def _rows(ctx, args):
    fid = _facility_arg(ctx, args)
    n = args.get("n", 2)
    if n > 1000:
        raise CapabilityError("too_large", f"{n} rows exceed the cap of 1000")
    if fid == "empty":
        raise CapabilityError("not_found", "no rows for facility 'empty'")
    rows = [{"i": i, "value": i * 1.5} for i in range(min(n, 3))]
    banners = [f"truncated to 3 of {n} rows"] if n > 3 else []
    return CapabilityResult(
        data={"facility": fid, "rows": rows, "nan": float("nan")},
        text=f"{len(rows)} rows for {fid} [F1@ctx].",
        facts=[{"id": "F1@ctx", "kind": "rows", "value": len(rows)}],
        banners=banners,
        truncated=n > 3,
    )


def _boom(ctx, args):
    raise RuntimeError("/somewhere/private/path exploded")


def _ids(ctx, args):
    return CapabilityResult(data=["a", "b"], text="two ids", facts=[])


def _noisy(ctx, args):
    print("stray print from a capability")
    sys.stdout.flush()
    os.write(1, b"stray bytes on fd 1\n")
    return CapabilityResult(data={"ok": True}, text="noisy but fine", facts=[])


_OBJ = {"type": "object"}


def default_registry() -> Registry:
    reg = Registry()
    reg.register(
        Capability(
            name="facilities",
            title="Facilities",
            description="List the facilities this server may read.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"facilities": {"type": "array", "items": {"type": "string"}}},
                "required": ["facilities"],
            },
            handler=_facilities,
        )
    )
    reg.register(
        Capability(
            name="rows",
            title="Rows",
            description="Up to three rows of one facility.",
            input_schema={
                "type": "object",
                "properties": {
                    "facility": {"type": "string"},
                    "n": {"type": "integer", "minimum": 0},
                },
                "required": ["facility"],
                "additionalProperties": False,
            },
            output_schema=_OBJ,
            handler=_rows,
        )
    )
    reg.register(
        Capability(
            name="boom",
            title="Boom",
            description="Always fails inside.",
            input_schema={"type": "object"},
            output_schema=_OBJ,
            handler=_boom,
        )
    )
    reg.register(
        Capability(
            name="ids",
            title="Ids",
            description="A result that is a JSON array.",
            input_schema={"type": "object"},
            output_schema={"type": "array", "items": {"type": "string"}},
            handler=_ids,
        )
    )
    reg.register(
        Capability(
            name="noisy",
            title="Noisy",
            description="Writes to stdout while it runs.",
            input_schema={"type": "object"},
            output_schema=_OBJ,
            handler=_noisy,
        )
    )

    def _render(args):
        focus = args.get("focus", "everything")
        return [
            {"role": "user", "text": f"Triage facility {args['facility']}, focusing on {focus}."},
            {"role": "assistant", "text": "I will start with the facilities tool."},
        ]

    reg.register_prompt(
        Prompt(
            name="triage",
            title="Triage a facility",
            description="Walk through one facility's open findings.",
            arguments=(
                PromptArg("facility", "facility id", required=True),
                PromptArg("focus", "what to look at first"),
            ),
            render=_render,
        )
    )
    return reg


def install() -> None:
    """Make this module ``camber.capabilities`` for the rest of the process."""
    import camber

    module = sys.modules[__name__]
    sys.modules["camber.capabilities"] = module
    camber.capabilities = module  # type: ignore[attr-defined]
