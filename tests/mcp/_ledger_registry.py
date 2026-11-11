"""A real ``camber.capabilities.Registry`` for the fact-ledger tests (#294): ``fact`` issues the
facts its arguments describe, ``pid`` names the process that ran it, ``sleep`` waits, and the real
``check_citations``. Module level, so a worker process builds the same registry from
``"_ledger_registry:default_registry"`` (this directory is on the worker's import path).
"""

from __future__ import annotations

import os
import time

from camber.capabilities import Capability, CapabilityResult, Registry
from camber.capabilities._cap_check_citations import CAPABILITIES as CHECK


def _fact(ctx, args):
    """``n`` facts ``F<i>@fact``, each stating ``value`` (and ``pad`` characters of text)."""
    value, pad = args.get("value", 7), "x" * int(args.get("pad", 0))
    facts = [
        {
            "id": f"F{i}@fact",
            "kind": "test",
            "value": value,
            "text": f"item {i} has value {value} {pad}".rstrip(),
        }
        for i in range(1, int(args.get("n", 1)) + 1)
    ]
    return CapabilityResult(data={"n": len(facts)}, text=f"value {value} [F1@fact].", facts=facts)


def _pid(ctx, args):
    return CapabilityResult(data={"pid": os.getpid()}, text="ran", facts=[])


def _sleep(ctx, args):
    time.sleep(float(args.get("seconds", 30)))
    return CapabilityResult(data={}, text="slept", facts=[])


def default_registry() -> Registry:
    reg = Registry()
    reg.register(
        Capability(
            name="fact",
            title="Fact",
            description="Issue test facts.",
            input_schema={
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "value": {"type": "integer"},
                    "pad": {"type": "integer", "minimum": 0, "maximum": 1900},
                },
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            handler=_fact,
        )
    )
    reg.register(
        Capability(
            name="pid",
            title="Pid",
            description="The process id that ran the call.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
            handler=_pid,
        )
    )
    reg.register(
        Capability(
            name="sleep",
            title="Sleep",
            description="Sleep for some seconds.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number", "minimum": 0}},
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            handler=_sleep,
        )
    )
    for cap in CHECK:
        reg.register(cap)
    return reg
