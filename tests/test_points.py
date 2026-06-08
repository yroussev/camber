"""Tests for camber.points -- the <prefix><id>_<measure> column-name format.

Fixtures are our own, chosen to exercise each rule of the format: single- and
two-digit equipment ids, a multi-segment prefix, zone points, building-level
points with no id, the measure-suffix helper, and the generic-name matcher
(building-level vs. exact equipment match).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.points import (  # noqa: E402
    count_equipment,
    equip_id_len,
    find_column,
    matches,
    measure_suffix,
    parse_point,
)


def test_two_digit_id_length():
    # trailing two-digit run before the measure -> id length 2
    assert equip_id_len("RTU08_DamperPct") == 2
    assert equip_id_len("RTU8_DamperPct") == 1


def test_multi_segment_prefix_with_two_digit_id():
    # prefix may contain underscores and letters; only the trailing digits are the id
    p = parse_point("Exhaust_Fan_F23_Status")
    assert p.equip_id == 23
    assert p.measure == "Status"
    assert p.prefix == "Exhaust_Fan_F"
    assert p.equip == "Exhaust_Fan_F23"


def test_single_digit_id():
    p = parse_point("AHU1_HeC")
    assert p.prefix == "AHU"
    assert p.equip_id == 1
    assert p.measure == "HeC"
    assert p.equip == "AHU1"


def test_two_digit_id():
    p = parse_point("AHU12_CC")
    assert p.prefix == "AHU"
    assert p.equip_id == 12
    assert p.measure == "CC"


def test_zone_point():
    p = parse_point("Z5_Temp")
    assert p.prefix == "Z"
    assert p.equip_id == 5
    assert p.measure == "Temp"


def test_no_id_is_none():
    # building-level point has no trailing integer id
    p = parse_point("Bldg_TempOa")
    assert p.equip_id is None
    assert p.measure == "TempOa"


def test_measure_suffix_includes_underscore():
    assert measure_suffix("AHU_HeC") == "_HeC"
    assert measure_suffix("Z_Tl3_Rht%") == "_Rht%"


def test_matches_specific_equipment():
    assert matches("AHU1_HeC", "AHU_HeC", 1)
    assert matches("AHU2_HeC", "AHU_HeC", 2)
    assert not matches("AHU2_HeC", "AHU_HeC", 1)
    assert not matches("AHU1_CC", "AHU_HeC", 1)


def test_matches_building_level():
    # Bldg* wildcard path: any "Bldg...<suffix>" matches regardless of id
    assert matches("Bldg_HeC", "AHU_HeC", 1)
    assert matches("BldgMain_HeC", "AHU_HeC", 7)
    assert not matches("Bldg_CC", "AHU_HeC", 1)


def test_find_column():
    headers = ["Timestamp", "AHU1_HeC", "AHU1_CC", "AHU2_HeC", "Bldg_TempOa"]
    assert find_column(headers, "AHU_HeC", 1) == "AHU1_HeC"
    assert find_column(headers, "AHU_HeC", 2) == "AHU2_HeC"
    assert find_column(headers, "AHU_CC", 1) == "AHU1_CC"
    assert find_column(headers, "AHU_CC", 2) is None


def test_count_equipment():
    headers = ["AHU1_HeC", "AHU1_CC", "AHU2_HeC", "AHU3_CC", "Z1_Temp", "Z14_Temp"]
    assert count_equipment(headers, "AHU") == 3
    assert count_equipment(headers, "Z") == 14
    assert count_equipment(headers, "ChW") == 0
