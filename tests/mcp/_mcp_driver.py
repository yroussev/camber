"""Run the real ``camber`` command line with :mod:`_fake_registry` as ``camber.capabilities``.

``python tests/mcp/_mcp_driver.py mcp --store S --allow ID`` is ``camber mcp`` with the fake
registry; with ``CAMBER_TEST_NO_REGISTRY=1`` the registry module is missing instead.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

if os.environ.get("CAMBER_TEST_NO_REGISTRY") == "1":
    sys.modules["camber.capabilities"] = None  # type: ignore[assignment]
else:
    import _fake_registry

    _fake_registry.install()

from camber.cli import main

raise SystemExit(main(sys.argv[1:]))
