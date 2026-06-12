"""Synthetic portfolio generator + read/write benchmark for the Parquet store.

Validates that the store scales to many buildings over years: it builds a synthetic portfolio
and times the hot paths — catalog enumeration (`points`), a single-equipment read, a
time-ranged read across the history, and a rollup — so a regression in column projection or
year-partition pruning shows up as a number instead of a silent slowdown. Runnable:

    python -m camber.store.bench --sites 50 --equips 20 --days 365 --freq 1h

Uses only numpy/pandas + the store itself (no benchmarking dependency).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from ..model.roles import Role
from .parquet_store import ParquetStore


def synth_portfolio(store: ParquetStore, *, sites: int = 10, equips: int = 10, roles: int = 4,
                    days: int = 30, freq: str = "1h", start: str = "2024-01-01",
                    seed: int = 0) -> int:
    """Write a synthetic portfolio (sites × equips, each with ``roles`` role-series). Returns
    total rows written. ``days`` may span years to exercise year partitioning."""
    rng = np.random.default_rng(seed)
    role_list = list(Role)[:roles]
    n = max(1, int(pd.Timedelta(days=days) / pd.Timedelta(freq)))
    idx = pd.date_range(start, periods=n, freq=freq)
    rows = 0
    for s in range(sites):
        site = f"site_{s:03d}"
        for e in range(equips):
            frame = pd.DataFrame(
                {r: 50.0 + rng.normal(0, 5, len(idx)) for r in role_list}, index=idx)
            rows += store.write_role_frame(frame, site=site, equip=f"AHU_{e:02d}",
                                           equip_class="ahu")
    return rows


def benchmark(root: str, *, sites: int = 10, equips: int = 10, roles: int = 4,
              days: int = 30, freq: str = "1h", start: str = "2024-01-01") -> dict:
    """Build a synthetic portfolio under ``root`` and time the store's hot paths.

    Returns a dict of row/point counts and per-operation wall-clock seconds.
    """
    store = ParquetStore(root)
    out: dict = {"sites": sites, "equips": equips, "roles": roles, "days": days, "freq": freq}

    t0 = time.perf_counter()
    out["rows"] = synth_portfolio(store, sites=sites, equips=equips, roles=roles,
                                  days=days, freq=freq, start=start)
    out["write_s"] = round(time.perf_counter() - t0, 4)

    t0 = time.perf_counter()
    pts = store.points()
    out["points_s"] = round(time.perf_counter() - t0, 4)
    out["n_points"] = len(pts)

    t0 = time.perf_counter()
    store.read_role_frame(site="site_000", equip="AHU_00")
    out["read_one_s"] = round(time.perf_counter() - t0, 4)

    mid = pd.Timestamp(start) + pd.Timedelta(days=days) / 2
    t0 = time.perf_counter()
    ranged = store.read_long(start=mid, end=mid + pd.Timedelta(days=1))
    out["ranged_read_s"] = round(time.perf_counter() - t0, 4)
    out["ranged_rows"] = len(ranged)

    t0 = time.perf_counter()
    store.rollup("D", site="site_000")
    out["rollup_s"] = round(time.perf_counter() - t0, 4)
    return out


def _main(argv=None):  # pragma: no cover
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description="Benchmark the CAMBER Parquet store at scale")
    ap.add_argument("--root", default=None, help="store root (default: a temp dir)")
    ap.add_argument("--sites", type=int, default=10)
    ap.add_argument("--equips", type=int, default=10)
    ap.add_argument("--roles", type=int, default=4)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--freq", default="1h")
    ap.add_argument("--start", default="2024-01-01")
    args = ap.parse_args(argv)

    root = args.root or tempfile.mkdtemp(prefix="camber-bench-")
    res = benchmark(root, sites=args.sites, equips=args.equips, roles=args.roles,
                    days=args.days, freq=args.freq, start=args.start)
    width = max(len(k) for k in res)
    for k, v in res.items():
        print(f"  {k:<{width}} : {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
