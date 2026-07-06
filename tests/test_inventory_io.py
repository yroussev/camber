"""Hardening tests for previously-untested utility modules (camber.inventory, camber.io)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import inventory as inv  # noqa: E402
from camber import io as cio  # noqa: E402
from camber.inventory import PointFile, parse_name, to_rows  # noqa: E402


# --------------------------------------------------------------------------- parse_name

def test_parse_name_known_prefix_id_measure():
    assert parse_name("AHU_1_SupplyAirTemp.csv") == ("AHU", "1", "SupplyAirTemp")
    assert parse_name("VAV_117_HWValve.csv") == ("VAV", "117", "HWValve")


def test_parse_name_longest_prefix_wins():
    # "CHW_SYS" must win over "CHW" / "CW" for a chilled-water-system file
    assert parse_name("CHW_SYS_1_Flow.csv")[0] == "CHW_SYS"
    # a plain CHW file still resolves to CHW, not CW
    assert parse_name("CHW_2_Temp.csv")[0] == "CHW"


def test_parse_name_prefix_only_and_no_measure():
    assert parse_name("AHU.csv") == ("AHU", "", "")
    assert parse_name("AHU_Status.csv") == ("AHU", "", "Status")   # single token after prefix


def test_parse_name_generic_fallback():
    # no known prefix -> TYPE_ID_MEASURE generic split
    assert parse_name("Widget_9_Power.csv") == ("Widget", "9", "Power")
    assert parse_name("Foo_Bar.csv") == ("Foo", "", "Bar")
    assert parse_name("Solo.csv") == ("Solo", "", "")


def test_parse_name_handles_missing_extension():
    assert parse_name("AHU_3_Damper") == ("AHU", "3", "Damper")


def test_to_rows_serializes_pointfiles():
    pts = [PointFile(path="p1.csv", fname="AHU_1_SAT.csv", equip_type="AHU", equip_id="1",
                     measure="SAT", unit="degF", n_rows=10)]
    rows = to_rows(pts)
    assert rows[0]["equip_type"] == "AHU" and rows[0]["measure"] == "SAT"


def test_inventory_over_a_folder(tmp_path):
    (tmp_path / "AHU_1_SAT.csv").write_text("ts,val\n2024-01-01,55\n2024-01-01T01:00,56\n")
    (tmp_path / "VAV_9_Flow.csv").write_text("ts,val\n2024-01-01,100\n")
    pts = inv.inventory([str(tmp_path)], count_rows=True)
    by_type = {p.equip_type for p in pts}
    assert by_type == {"AHU", "VAV"} and len(pts) == 2
    ahu = [p for p in pts if p.equip_type == "AHU"][0]
    assert ahu.measure == "SAT" and ahu.n_rows == 2


# --------------------------------------------------------------------------- io

def test_load_csv_default_and_named_timestamp(tmp_path):
    p = tmp_path / "trend.csv"
    p.write_text("Datetime,SAT\n2024-06-01 00:00,55\n2024-06-01 01:00,57\n")
    df = cio.load_csv(str(p))
    assert isinstance(df.index, pd.DatetimeIndex) and list(df["SAT"]) == [55, 57]
    df2 = cio.load_csv(str(p), timestamp_col="Datetime")
    assert df2.index.equals(df.index)


def test_load_csv_resample_mean(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("ts,v\n2024-06-01 00:00,10\n2024-06-01 00:30,20\n2024-06-01 01:00,30\n")
    df = cio.load_csv(str(p), resample="1h")
    assert df["v"].iloc[0] == 15.0                     # mean of 10,20 in the first hour


def test_add_oat_band_cooling_season():
    df = pd.DataFrame({"oat": [50.0, 65.0, 70.0, 80.0]})
    band = cio.add_oat_band(df, "oat", cooling_cutoff_f=65.0)
    assert list(band) == [False, False, True, True]    # strictly above the cutoff
