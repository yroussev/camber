"""Deterministic integer-hash noise, the same on every machine (0.105, #144).

Shared by the synthetic catalog entries (:mod:`camber.datasets._synthetic`) and the generated
G36 fleets (:mod:`camber.fleetlab`). It lives in its own module so a generator outside
``camber.datasets`` can use it without importing the datasets package.
"""

from __future__ import annotations

import numpy as np


def noise(n: int, seed: int, stream: int) -> np.ndarray:
    """``n`` uniform values in [0, 1) from a 64-bit integer hash (splitmix64) of
    ``(seed, stream, i)`` -- integer arithmetic only, so every platform and numpy version gives
    the same values."""
    with np.errstate(over="ignore"):
        x = np.arange(n, dtype=np.uint64) + np.uint64(seed * 1_000_003 + stream * 7_919)
        x = x * np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


__all__ = ["noise"]
