"""CAMBER — Commissioning, Analytics & M&V for Building Energy Re-tuning.

CAMBER's public API lives in its subpackages and modules, imported by path — e.g.
``from camber.io import load_csv``, ``from camber.rules import builtin_registry``,
``from camber.mandv import calibrate``, ``from camber.model import Role``. The top-level
``camber`` namespace deliberately exposes only ``__version__``; it is not a re-export
surface, so nothing here is promised beyond the version string.

What counts as public, and the stability guarantees around it, are defined in
``docs/API-STABILITY.md``. In short: any name not starting with ``_``, in a module not
starting with ``_``, is public and covered by CAMBER's SemVer promise from 1.0 onward;
each module's ``__all__`` is its curated public surface.
"""

__version__ = "0.29.0"

__all__ = ["__version__"]
