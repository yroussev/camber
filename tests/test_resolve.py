"""Tests for the role vocabulary, mapping provider, and resolve layer."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import (  # noqa: E402
    discover, discover_terminals, resolve, occupied, TERMINAL_CLASSES)


# ---- mapping ----

def _mapping():
    return MappingProvider.from_dict({
        "aliases": {
            "HWValve": "heat_valve",
            "HHW_Valve": "heat_valve",
            "CHW_Valve": "cool_valve",
            "SupplyAir": "supply_air_temp",
            "SpaceTemp": "space_temp",
            "ActCoolSP": "cool_sp",
            "WarmUp": "warmup",
            "CoolDown": "cooldown",
        },
        "patterns": [[r"OSA$", "oat"]],
    })


def test_alias_case_insensitive():
    m = _mapping()
    assert m.role_of("HWValve") is Role.HEAT_VALVE
    assert m.role_of("hwvalve") is Role.HEAT_VALVE


def test_two_tokens_same_role():
    m = _mapping()
    assert m.role_of("HHW_Valve") is Role.HEAT_VALVE
    assert m.role_of("CHW_Valve") is Role.COOL_VALVE


def test_pattern_fallback():
    m = _mapping()
    assert m.role_of("AHU_1_OSA".split("_")[-1]) is Role.OAT  # "OSA" -> OAT
    assert m.role_of("OSA") is Role.OAT


def test_unmapped_is_none():
    assert _mapping().role_of("FilterDPT") is None


def test_roles_present_first_wins():
    m = _mapping()
    got = m.roles_present(["SpaceTemp", "HWValve", "CHW_Valve", "FilterDPT"])
    assert got[Role.SPACE_TEMP] == "SpaceTemp"
    assert got[Role.HEAT_VALVE] == "HWValve"
    assert got[Role.COOL_VALVE] == "CHW_Valve"
    assert Role.OAT not in got


# ---- discover + resolve against a tiny fixture folder ----

_HDR = "Timestamp,Value (%)\n"
_ROWS = "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,{v}\n"
                for h, v in [(8, 40.0), (9, 45.0), (10, 50.0)])


def _write_point(folder, equip, measure, header=_HDR, rows=_ROWS):
    with open(os.path.join(folder, f"{equip}_{measure}.csv"), "w", encoding="utf-8") as f:
        f.write(header + rows)


def test_discover_and_resolve(tmp_path):
    folder = str(tmp_path)
    for m in ("SpaceTemp", "HWValve", "CHW_Valve", "FilterDPT"):
        _write_point(folder, "VAV_101", m)
    _write_point(folder, "VAV_102", "SpaceTemp")

    refs = discover(folder, "VAV")
    assert [r.equip for r in refs] == ["VAV_101", "VAV_102"]

    frame = resolve(refs[0], _mapping(),
                    [Role.HEAT_VALVE, Role.COOL_VALVE, Role.SPACE_TEMP, Role.OAT],
                    resample="1h")
    # mapped roles present become columns; unmapped (FilterDPT) and absent (OAT) omitted
    assert Role.HEAT_VALVE in frame.columns
    assert Role.COOL_VALVE in frame.columns
    assert Role.SPACE_TEMP in frame.columns
    assert Role.OAT not in frame.columns
    assert len(frame) >= 1


def test_discover_terminals_unions_vav_cav_fcav(tmp_path):
    folder = str(tmp_path)
    # a mixed building: VAV + CAV + FCAV terminals (plus a non-terminal AHU)
    for eq in ("VAV_101", "VAV_102", "CAV_201", "FCAV_301", "FCAV_302"):
        _write_point(folder, eq, "SpaceTemp")
    _write_point(folder, "AHU_1", "SpaceTemp")   # not a terminal class

    # VAV-only discovery misses CAV/FCAV (the 67-of-73 bug)
    vav_only = [r.equip for r in discover(folder, "VAV")]
    assert vav_only == ["VAV_101", "VAV_102"]

    terms = discover_terminals(folder)
    assert [r.equip for r in terms] == [
        "CAV_201", "FCAV_301", "FCAV_302", "VAV_101", "VAV_102"]
    # AHU is not swept in as a terminal
    assert all(not r.equip.startswith("AHU") for r in terms)
    # each ref keeps its own class
    by_equip = {r.equip: r.equip_class for r in terms}
    assert by_equip["CAV_201"] == "CAV" and by_equip["FCAV_301"] == "FCAV"


def test_discover_terminals_no_double_count(tmp_path):
    folder = str(tmp_path)
    _write_point(folder, "FCAV_301", "SpaceTemp")   # must not also match CAV/VAV
    terms = discover_terminals(folder)
    assert [r.equip for r in terms] == ["FCAV_301"]
    assert "FCAV" in TERMINAL_CLASSES


def test_discover_across_multiple_folders(tmp_path):
    # one export split across batches: VAV_101 in folderA, VAV_102 in folderB
    fa = tmp_path / "a"
    fb = tmp_path / "b"
    fa.mkdir()
    fb.mkdir()
    _write_point(str(fa), "VAV_101", "SpaceTemp")
    _write_point(str(fb), "VAV_102", "SpaceTemp")
    refs = discover([str(fa), str(fb)], "VAV")
    assert [r.equip for r in refs] == ["VAV_101", "VAV_102"]
    # each ref searches every source folder (its marker folder first)
    assert set(refs[0].all_folders()) == {str(fa), str(fb)}


def test_resolve_finds_points_split_across_folders(tmp_path):
    # the marker (SpaceTemp) is in folderA but the heat valve lives in folderB:
    # resolve must search both folders for one equipment's role-frame
    fa = tmp_path / "a"
    fb = tmp_path / "b"
    fa.mkdir()
    fb.mkdir()
    _write_point(str(fa), "VAV_101", "SpaceTemp")
    _write_point(str(fb), "VAV_101", "HWValve")
    ref = discover([str(fa), str(fb)], "VAV")[0]
    frame = resolve(ref, _mapping(), [Role.SPACE_TEMP, Role.HEAT_VALVE], resample="1h")
    assert Role.SPACE_TEMP in frame.columns      # from folder a
    assert Role.HEAT_VALVE in frame.columns      # from folder b


def test_resolve_empty_when_no_roles(tmp_path):
    folder = str(tmp_path)
    _write_point(folder, "VAV_101", "FilterDPT")  # unmapped only
    ref = discover(folder, "VAV", marker_measure="FilterDPT")[0]
    frame = resolve(ref, _mapping(), [Role.HEAT_VALVE])
    assert frame.empty


def test_occupied_weekday_window(tmp_path):
    import pandas as pd
    # build a role-named frame spanning a weekday across the occupied boundary
    idx = pd.date_range("2025-07-07 05:00", periods=8, freq="1h")  # Monday
    frame = pd.DataFrame({Role.HEAT_VALVE: range(8)}, index=idx)
    occ = occupied(frame)
    assert not occ.loc[pd.Timestamp("2025-07-07 06:00")]
    assert occ.loc[pd.Timestamp("2025-07-07 10:00")]
