"""A real ``camber.capabilities.Registry`` with slow capabilities, for the worker-pool tests (#292).

Module-level handlers, so a worker process can build the same registry from
``"_slow_registry:default_registry"`` (this directory is on the worker's import path).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

from camber.capabilities import Capability, CapabilityResult, Registry

_OUT = {"type": "object"}


def _secs() -> dict:
    return {
        "type": "object",
        "properties": {"seconds": {"type": "number", "minimum": 0}},
        "additionalProperties": False,
    }


def _sleep(ctx, args):
    time.sleep(float(args.get("seconds", 30)))
    return CapabilityResult(data={"slept": True}, text="slept", facts=[])


def _spin(ctx, args):
    """A CPU loop that holds the GIL in short slices and never checks a flag."""
    end = time.monotonic() + float(args.get("seconds", 30))
    n = 0
    while time.monotonic() < end:
        n += sum(range(1000))
    return CapabilityResult(data={"spun": True}, text="spun", facts=[])


def _freeze(ctx, args):
    """Stop the whole worker process (SIGSTOP): no thread of it can answer, not even the
    registry's own time cap."""
    os.kill(os.getpid(), signal.SIGSTOP)
    return CapabilityResult(data={}, text="thawed", facts=[])


def _spawn(ctx, args):
    """Start a child process in a process group of its own (as the font scan of matplotlib does
    on macOS), write its pid to ``path``, then wait: it must not outlive the worker (#309)."""
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    with open(args["path"], "w") as f:
        f.write(str(child.pid))
    time.sleep(60)
    return CapabilityResult(data={}, text="waited", facts=[])


def _spawn_group(ctx, args):
    """Start a child process in a process group of its own but in the worker's session (as
    macOS's ``system_profiler -nospawn`` is), write its pid to ``path``, then wait (#316)."""
    child = subprocess.Popen(["sleep", "60"], preexec_fn=os.setpgrp)
    with open(args["path"], "w") as f:
        f.write(str(child.pid))
    time.sleep(60)
    return CapabilityResult(data={}, text="waited", facts=[])


def _grow(ctx, args):
    """Touch ``mb`` megabytes, then let them go: the worker's peak memory grows (#309)."""
    block = b"x" * (int(args["mb"]) << 20)
    del block
    return CapabilityResult(data={"pid": os.getpid()}, text="grew", facts=[])


def _pid(ctx, args):
    return CapabilityResult(data={"pid": os.getpid()}, text="the worker", facts=[])


def default_registry() -> Registry:
    reg = Registry()
    for name, handler, doc in (
        ("sleep", _sleep, "Sleep for some seconds."),
        ("spin", _spin, "Burn CPU for some seconds."),
        ("pid", _pid, "The process id that ran the call."),
        ("freeze", _freeze, "Stop the worker process."),
    ):
        reg.register(
            Capability(
                name=name,
                title=name.title(),
                description=doc,
                input_schema=_secs(),
                output_schema=_OUT,
                handler=handler,
            )
        )
    for name, handler, schema in (
        ("spawn", _spawn, {"path": {"type": "string"}}),
        ("spawn_group", _spawn_group, {"path": {"type": "string"}}),
        ("grow", _grow, {"mb": {"type": "integer", "minimum": 0}}),
    ):
        reg.register(
            Capability(
                name=name,
                title=name.title(),
                description=(handler.__doc__ or name).split("\n")[0],
                input_schema={
                    "type": "object",
                    "properties": schema,
                    "required": list(schema),
                    "additionalProperties": False,
                },
                output_schema=_OUT,
                handler=handler,
            )
        )
    return reg


def dying_registry() -> Registry:
    """:func:`default_registry`, but a worker that finds a token file in the directory
    ``$CAMBER_TEST_DIE_DIR`` takes it and is killed while starting, as the system stopping
    processes for memory would (#316): one token, one worker dead before it was ready."""
    folder = os.environ.get("CAMBER_TEST_DIE_DIR")
    for name in sorted(os.listdir(folder)) if folder else ():
        try:
            os.remove(os.path.join(folder, name))
        except OSError:  # another worker took it
            continue
        os.kill(os.getpid(), signal.SIGKILL)
    return default_registry()
