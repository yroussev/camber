"""Scale tuning for the Parquet store: year-partition pruning, projection, fast pivot.

These assert the *mechanism* (fragments pruned, columns projected) and that results are
unchanged — not wall-clock — so they're deterministic in CI. `tests/test_store.py` covers the
base read/write semantics.
"""

import os
import sys

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.store.bench import benchmark, synth_portfolio  # noqa: E402
from camber.store.parquet_store import ParquetStore  # noqa: E402

_ROLE = list(Role)[0]


def _multi_year_store(root):
    """One site, two equips, three years of daily data."""
    store = ParquetStore(root)
    idx = pd.date_range("2022-01-01", "2024-12-31", freq="D")
    rng = np.random.default_rng(0)
    for e in ("AHU_00", "AHU_01"):
        frame = pd.DataFrame({_ROLE: 50 + rng.normal(0, 5, len(idx))}, index=idx)
        store.write_role_frame(frame, site="S1", equip=e, equip_class="ahu")
    return store


def test_year_partition_pruning_skips_fragments(tmp_path):
    store = _multi_year_store(str(tmp_path / "store"))
    dataset = ds.dataset(store.root, format="parquet", partitioning="hive")
    all_frags = list(dataset.get_fragments())
    assert len(all_frags) >= 3                              # at least one per year

    # a one-month query in 2024 must prune the 2022 + 2023 year partitions
    filt = store._build_filter(start="2024-06-01", end="2024-06-30")
    pruned = list(dataset.get_fragments(filter=filt))
    assert 0 < len(pruned) < len(all_frags)
    assert all("year=2024" in f.path for f in pruned)       # only the 2024 partition opened


def test_ranged_read_correct_after_pruning(tmp_path):
    store = _multi_year_store(str(tmp_path / "store"))
    full = store.read_long()                                # everything
    ranged = store.read_long(start="2024-06-01", end="2024-06-30")
    assert not ranged.empty
    ts = pd.to_datetime(ranged["ts"])
    assert ts.min() >= pd.Timestamp("2024-06-01") and ts.max() <= pd.Timestamp("2024-06-30")
    # pruned read matches a brute-force filter over the full read (same rows)
    m = (pd.to_datetime(full["ts"]) >= "2024-06-01") & (pd.to_datetime(full["ts"]) <= "2024-06-30")
    assert len(ranged) == int(m.sum())


def test_points_projects_catalog_columns_only(tmp_path):
    store = _multi_year_store(str(tmp_path / "store"))
    # the projected read used by points() must not pull ts/value
    proj = store.read_long(columns=["site", "equip", "role"])
    assert set(proj.columns) == {"site", "equip", "role"}
    pts = store.points()
    assert {(p.site, p.equip, p.role) for p in pts} == {("S1", "AHU_00", _ROLE.value),
                                                        ("S1", "AHU_01", _ROLE.value)}


def test_read_role_frame_fast_and_dup_paths_agree(tmp_path):
    store = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range("2024-01-01", periods=48, freq="1h")
    frame = pd.DataFrame({_ROLE: np.arange(48.0)}, index=idx)
    store.write_role_frame(frame, site="S1", equip="AHU_00")
    # unique (ts, role) -> fast pivot path
    wide = store.read_role_frame(site="S1", equip="AHU_00")
    assert list(wide[_ROLE]) == list(np.arange(48.0))

    # write the same timestamps again -> duplicates -> pivot_table(mean) path, still correct
    store.write_role_frame(frame + 2.0, site="S1", equip="AHU_00")
    wide2 = store.read_role_frame(site="S1", equip="AHU_00")
    assert np.allclose(wide2[_ROLE].to_numpy(), np.arange(48.0) + 1.0)   # mean of x and x+2


def test_benchmark_smoke(tmp_path):
    res = benchmark(str(tmp_path / "bench"), sites=2, equips=2, roles=3, days=2, freq="1h")
    assert res["rows"] > 0
    assert res["n_points"] == 2 * 2 * 3                    # sites * equips * roles
    assert res["ranged_rows"] > 0
    for k in ("write_s", "points_s", "read_one_s", "ranged_read_s", "rollup_s"):
        assert isinstance(res[k], float) and res[k] >= 0.0


def test_synth_portfolio_row_count(tmp_path):
    store = ParquetStore(str(tmp_path / "s"))
    rows = synth_portfolio(store, sites=2, equips=2, roles=2, days=1, freq="1h")
    assert rows == 2 * 2 * 2 * 24                          # sites*equips*roles*hours
