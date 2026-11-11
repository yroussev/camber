"""The container image's ``HEALTHCHECK`` (#326): ``python -m camber._healthcheck``.

It probes the read API's ``GET /health`` only when the container runs the API (the image's
default command, or ``camber serve``). Any other command started from the image (``camber mcp``,
a shell, a one-off run) serves no HTTP, so there is nothing to probe: the check passes while
the container runs, instead of turning it ``unhealthy``.
"""

from __future__ import annotations

import os
import sys


def _command(pid: int = 1) -> list | None:
    """The command line of process ``pid`` (the container's own command), or ``None`` when it
    cannot be read here (no ``/proc``)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]


def serves_http(cmd: list | None) -> bool:
    """Whether ``cmd`` runs the read API: ``python -m camber.api.server`` or ``camber serve``.
    An unknown command counts as the API, the check this image always had."""
    if cmd is None:
        return True
    return "camber.api.server" in cmd or "serve" in cmd[1:]


def main(argv=None) -> int:
    """0 when healthy; 1 when the API does not answer ``/health`` with 200."""
    if not serves_http(_command()):
        return 0
    import urllib.request

    port = os.environ.get("CAMBER_API_PORT", "8080")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            return 0 if resp.status == 200 else 1
    except Exception:  # noqa: BLE001 - any failure to answer is unhealthy
        return 1


if __name__ == "__main__":
    sys.exit(main())
