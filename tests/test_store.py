"""Tests for the Parquet time-series store (store.parquet_store)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore, role_frame_to_long  # noqa: E402


def _frame(start="2024-01-01", periods=48, freq="1h"):
    idx = pd.date_range(start, periods=periods, freq=freq)
    return pd.DataFrame({
        Role.HEAT_VALVE: range(periods),
        Role.COOL_VALVE: [periods - i for i in range(periods)],
    }, index=idx)


# --- melt ------------------------------------------------------------------ #

def test_role_frame_to_long_shape_and_dropna():
    f = _frame(periods=3)
    f.iloc[1, 0] = float("nan")          # one heat-valve gap
    long = role_frame_to_long(f, site="S", equip="AHU_1", equip_class="AHU")
    assert set(long.columns) == {"ts", "equip", "equip_class", "role", "value"}
    # 3*2 cells minus the 1 NaN = 5 observations
    assert len(long) == 5
    assert set(long["role"]) == {"heat_valve", "cool_valve"}
    assert (long["equip"] == "AHU_1").all()


def test_role_frame_to_long_empty():
    long = role_frame_to_long(pd.DataFrame(), site="S", equip="E")
    assert long.empty


# --- roundtrip ------------------------------------------------------------- #

def test_write_then_read_role_frame_roundtrips(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    f = _frame(periods=24)
    n = st.write_role_frame(f, site="DemoSite", equip="AHU_1", equip_class="AHU")
    assert n == 48                        # 24 rows * 2 roles
    back = st.read_role_frame(site="DemoSite", equip="AHU_1")
    assert list(back.columns) == [Role.COOL_VALVE, Role.HEAT_VALVE] or \
           set(back.columns) == {Role.HEAT_VALVE, Role.COOL_VALVE}
    assert len(back) == 24
    # values preserved
    assert back[Role.HEAT_VALVE].iloc[0] == 0
    assert back[Role.HEAT_VALVE].iloc[-1] == 23


def test_hive_partitions_on_disk(tmp_path):
    root = str(tmp_path / "tsdb")
    st = ParquetStore(root)
    st.write_role_frame(_frame(start="2024-12-30", periods=96), site="DemoSite",
                        equip="AHU_1", equip_class="AHU")   # spans 2024 -> 2025
    sites = [d for d in os.listdir(root) if d.startswith("site=")]
    assert sites == ["site=DemoSite"]
    years = sorted(os.listdir(os.path.join(root, "site=DemoSite")))
    assert years == ["year=2024", "year=2025"]


# --- filtered reads -------------------------------------------------------- #

def test_read_filters_by_role_and_time(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(periods=48), site="S", equip="AHU_1", equip_class="AHU")
    long = st.read_long(site="S", roles=[Role.HEAT_VALVE],
                        start="2024-01-01 06:00", end="2024-01-01 09:00")
    assert set(long["role"]) == {"heat_valve"}
    # 06:00..09:00 inclusive hourly = 4 rows
    assert len(long) == 4


def test_read_filters_by_equip(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(periods=10), site="S", equip="AHU_1", equip_class="AHU")
    st.write_role_frame(_frame(periods=10), site="S", equip="AHU_2", equip_class="AHU")
    long = st.read_long(site="S", equips=["AHU_2"])
    assert set(long["equip"]) == {"AHU_2"}


def test_append_accumulates_without_clobber(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(start="2024-01-01", periods=5), site="S",
                        equip="AHU_1", equip_class="AHU")
    st.write_role_frame(_frame(start="2024-02-01", periods=5), site="S",
                        equip="AHU_1", equip_class="AHU")
    long = st.read_long(site="S", roles=[Role.HEAT_VALVE])
    assert len(long) == 10                # both writes survive


def test_resample_on_read(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(periods=48, freq="1h"), site="S", equip="AHU_1",
                        equip_class="AHU")
    daily = st.read_role_frame(site="S", equip="AHU_1", resample="1D")
    assert len(daily) == 2                # 48 hours -> 2 days


# --- catalog --------------------------------------------------------------- #

def test_points_and_sites_catalog(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(periods=4), site="A", equip="AHU_1", equip_class="AHU")
    st.write_role_frame(_frame(periods=4), site="B", equip="AHU_9", equip_class="AHU")
    assert sorted(st.sites()) == ["A", "B"]
    keys = st.points(site="A")
    assert {(k.equip, k.role) for k in keys} == {
        ("AHU_1", "heat_valve"), ("AHU_1", "cool_valve")}


def test_read_empty_store_is_empty(tmp_path):
    st = ParquetStore(str(tmp_path / "nope"))
    assert st.read_long(site="S").empty
    assert st.read_role_frame(site="S", equip="X").empty
    assert st.sites() == []
    assert st.points() == []


# --- rollup / retention ----------------------------------------------------- #

def test_rollup_to_daily_means(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    st.write_role_frame(_frame(periods=48, freq="1h"), site="S", equip="AHU_1",
                        equip_class="AHU")
    rolled = st.rollup("D", agg="mean")
    # 48 hourly rows over 2 days x 2 roles -> 4 rolled rows
    assert len(rolled) == 4
    assert set(rolled["role"]) == {"heat_valve", "cool_valve"}
    # day-1 heat_valve hourly values 0..23 -> mean 11.5
    d1 = rolled[(rolled["role"] == "heat_valve")].sort_values("ts").iloc[0]
    assert abs(d1["value"] - 11.5) < 1e-9


def test_write_rollup_to_dest_store(tmp_path):
    src = ParquetStore(str(tmp_path / "src"))
    src.write_role_frame(_frame(periods=48), site="S", equip="AHU_1", equip_class="AHU")
    dest = ParquetStore(str(tmp_path / "dest"))
    n = src.write_rollup("D", dest, agg="mean")
    assert n == 4
    back = dest.read_role_frame(site="S", equip="AHU_1")
    assert len(back) == 2          # two daily rows


def test_prune_removes_old_year_partitions(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    # spans 2024 -> 2025
    st.write_role_frame(_frame(start="2024-12-30", periods=96), site="S",
                        equip="AHU_1", equip_class="AHU")
    removed = st.prune(before_year=2025)
    assert removed == 1            # the year=2024 partition
    long = st.read_long(site="S")
    assert (pd.to_datetime(long["ts"]).dt.year == 2025).all()
