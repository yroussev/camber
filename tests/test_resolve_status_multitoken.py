"""Tests for resolve() follow-ups: status-role loading + multi-token equipment."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import EquipRef, resolve  # noqa: E402


def _mapping():
    return MappingProvider.from_dict({
        "aliases": {
            "B1_Sts": "boiler_status",
            "HHWS_Temp": "hw_supply_temp",
            "HHW_DiffPress": "hw_diff_press",
            "OSATemp": "oat",
        },
    })


def _write_status(folder, fname, rows):
    with open(os.path.join(folder, fname), "w", encoding="utf-8") as f:
        f.write("Timestamp,Value\n")
        f.write(rows)


def _write_numeric(folder, fname, rows):
    with open(os.path.join(folder, fname), "w", encoding="utf-8") as f:
        f.write("Timestamp,Value (F)\n")
        f.write(rows)


def test_status_role_loaded_via_load_status(tmp_path):
    folder = str(tmp_path)
    # text status point: numeric loader would NaN this; resolve must use load_status
    _write_status(folder, "HotWaterPlant_B1_Sts.csv",
                  "07-Jul-25 08:00:00 AM PDT,Off\n"
                  "07-Jul-25 10:00:00 AM PDT,Running\n"
                  "07-Jul-25 02:00:00 PM PDT,Off\n")
    # equip = HotWaterPlant; the file's token is "B1_Sts"
    ref = EquipRef(equip="HotWaterPlant", equip_class="HotWaterPlant", folder=folder)
    frame = resolve(ref, _mapping(), [Role.BOILER_STATUS], resample="1h")
    assert Role.BOILER_STATUS in frame.columns
    assert frame[Role.BOILER_STATUS].notna().any()
    assert frame[Role.BOILER_STATUS].loc[pd.Timestamp("2025-07-07 11:00")] == 1.0
    assert frame[Role.BOILER_STATUS].loc[pd.Timestamp("2025-07-07 09:00")] == 0.0


def test_multitoken_equipment_spans_siblings(tmp_path):
    folder = str(tmp_path)
    # plant points under HotWaterPlant; building OAT under a DIFFERENT token (CHW_SYS)
    _write_status(folder, "HotWaterPlant_B1_Sts.csv",
                  "07-Jul-25 08:00:00 AM PDT,Running\n")
    _write_numeric(folder, "HotWaterPlant_HHWS_Temp.csv",
                   "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,150.0\n" for h in (8, 9, 10)))
    _write_numeric(folder, "CHW_SYS_OSATemp.csv",
                   "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,95.0\n" for h in (8, 9, 10)))

    ref = EquipRef(equip="HotWaterPlant", equip_class="HotWaterPlant", folder=folder,
                   extra_equips=("CHW_SYS",))
    frame = resolve(ref, _mapping(),
                    [Role.BOILER_STATUS, Role.HW_SUPPLY_TEMP, Role.OAT],
                    resample="1h")
    # OAT lives under a different token but is pulled in via extra_equips
    assert Role.BOILER_STATUS in frame.columns
    assert Role.HW_SUPPLY_TEMP in frame.columns
    assert Role.OAT in frame.columns
    assert frame[Role.OAT].dropna().iloc[0] == 95.0


def test_primary_token_wins_on_conflict(tmp_path):
    folder = str(tmp_path)
    rows_a = "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,100.0\n" for h in (8, 9))
    rows_b = "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,200.0\n" for h in (8, 9))
    _write_numeric(folder, "Plant_A_HHWS_Temp.csv", rows_a)
    _write_numeric(folder, "Plant_B_HHWS_Temp.csv", rows_b)
    ref = EquipRef(equip="Plant_A", equip_class="Plant", folder=folder,
                   extra_equips=("Plant_B",))
    frame = resolve(ref, _mapping(), [Role.HW_SUPPLY_TEMP], resample="1h")
    # primary (Plant_A=100) wins, sibling (Plant_B=200) ignored for the same role
    assert frame[Role.HW_SUPPLY_TEMP].dropna().iloc[0] == 100.0
