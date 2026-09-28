"""Energy conversion factor sets (camber.energy_factors): the ENERGY STAR "Thermal Energy
Conversions" transcription, the schema and consistency validator, the "M" convention, to_kbtu,
the opt-in factor-set path in config billing M&V, and the generated reference docs.

Synthetic bills only.
"""

import copy
import json
import os
import sys
import warnings

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_factors as ef  # noqa: E402
from camber import energy_units as eu  # noqa: E402

ES = "energy_star_thermal_2015"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JSON = os.path.join(_ROOT, "camber", "energy_factors", f"{ES}.json")

# Hand-checked against the PDF (Figure 3, pages 3-6): unit key -> (US multiplier, CA multiplier),
# as printed, per meter type; and each meter type's printed heat content (US, CA).
E = {"kBtu": ("1", "1"), "MMBtu": ("1,000", "1,000"), "GJ": ("947.817", "947.817")}
FIG3 = {
    "electricity": (None, None, {
        **E, "kWh": ("3.412", "3.412"), "MWh": ("3,412", "3,412")}),
    "natural_gas": ("1,026 Btu/cf", "1,031.43 Btu/cf", {
        **E, "ft3": ("1.026", "1.031"), "CCF": ("102.6", "103.143"), "kcf": ("1,026", "1,031"),
        "MMcf": ("1,026,000", "1,031,430"), "therm": ("100", "100"),
        "m3": ("36.303", "36.425")}),
    "fuel_oil_1": ("0.139 MBtu/gallon", "0.139210 MBtu/gallon", {
        **E, "gal_US": ("139", "139.210"), "gal_UK": ("166.927", "167.184"),
        "L": ("36.720", "36.775")}),
    "fuel_oil_2": ("0.138 MBtu/gallon", "0.139210 MBtu/gallon", {
        **E, "gal_US": ("138", "139.210"), "gal_UK": ("165.726", "167.184"),
        "L": ("36.456", "36.775")}),
    "fuel_oil_4": ("0.146 MBtu/gallon", "0.139210 MBtu/gallon", {
        **E, "gal_US": ("146", "139.210"), "gal_UK": ("175.333", "167.184"),
        "L": ("38.569", "36.775")}),
    "fuel_oil_5_6": ("0.150 MBtu/gallon", "0.152485 MBtu/gallon", {
        **E, "gal_US": ("150", "152.485"), "gal_UK": ("180.137", "183.127"),
        "L": ("39.626", "40.282")}),
    "diesel": ("0.138 MBtu/gallon", "0.137416 MBtu/gallon", {
        **E, "gal_US": ("138", "137.416"), "gal_UK": ("165.726", "165.029"),
        "L": ("36.456", "36.301")}),
    "kerosene": ("0.135 MBtu/gallon", "0.135191 MBtu/gallon", {
        **E, "gal_US": ("135", "135.191"), "gal_UK": ("162.123", "162.358"),
        "L": ("35.663", "35.714")}),
    "propane": ("0.092 MBtu/gallon", "0.09089 MBtu/gallon", {
        **E, "ft3": ("2.516", "2.516"), "CCF": ("251.6", "251.6"), "kcf": ("2,516", "2,516"),
        "gal_US": ("92", "90.809"), "gal_UK": ("110.484", "109.057"),
        "L": ("24.304", "23.989")}),
    "district_steam": ("1,194 Btu/Lb", "1,194 Btu/Lb", {
        **E, "lb": ("1.194", "1.194"), "klb": ("1,194", "1,194"),
        "MMlb": ("1,194,000", "1,194,000"), "therm": ("100.0", "100.000"),
        "kg": ("2.632", "2.632")}),
    "district_hot_water": (None, None, {**E, "therm": ("100", "100")}),
    "district_chilled_water": (None, None, {**E, "ton-hour": ("12.0", "12.0")}),
    "coal_anthracite": ("25.09 MBtu/ton", "23.818 MBtu/ton", {
        **E, "short_ton": ("25,090", "23,818"), "lb": ("12.545", "11.909"),
        "klb": ("12,545", "11,909"), "MMlb": ("12,545,000", "11,909,055"),
        "tonne": ("27,658.355", "26,255")}),
    "coal_bituminous": ("24.93 MBtu/ton", "21.496 MBtu/ton", {
        **E, "short_ton": ("24,930", "21,496"), "lb": ("12.465", "10.748"),
        "klb": ("12,465", "10,748"), "MMlb": ("12,465,000", "10,748,245"),
        "tonne": ("27,482", "23,695")}),
    "coke": ("24.80 MBtu/ton", "24.79 MBtu/ton", {
        **E, "short_ton": ("24,800", "24,790"), "lb": ("12.4", "12.395"),
        "klb": ("12,400", "12,395"), "MMlb": ("12,400,000", "12,394,876"),
        "tonne": ("27,339", "27,326")}),
    "wood": ("17.48 MBtu/Ton", "15.48 MBtu/Ton", {
        **E, "short_ton": ("17,480", "15,477"), "tonne": ("15,857", "17,061")}),
    "other": (None, None, {"kBtu": ("1.0", "1.0")}),
}  # fmt: skip
# the rows the source prints inconsistently with its own heat content (kept as printed)
DISCREPANT = {
    ("natural_gas", "US", "m3"),
    ("propane", "CA", "gal_US"),
    ("propane", "CA", "gal_UK"),
    ("propane", "CA", "L"),
    ("wood", "US", "tonne"),
}


@pytest.fixture(scope="module")
def fs():
    return ef.get_factor_set(ES)


@pytest.fixture(scope="module")
def doc():
    with open(_JSON, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- registry + schema


def test_registry_lists_and_loads_the_energy_star_set(fs):
    assert ES in ef.factor_sets()
    assert fs.kind == "energy_conversion" and fs.output_unit == "kBtu"
    s = fs.source
    assert s["url"] == (
        "https://portfoliomanager.energystar.gov/pdf/reference/Thermal%20Conversions.pdf"
    )
    assert s["sha256"] == "52fb681b4c3c626a7132a7546ba86b236921a304c4e990c309598e47f1c9ece1"
    assert (s["edition"], s["retrieved"]) == ("2015-08", "2026-09-28")
    assert "U.S. Government" in s["terms"]
    assert "40 CFR" in s["footnotes"]["1a"] and "Tables C-1 and C-2" in s["footnotes"]["1a"]
    assert "Statistics Canada" in s["footnotes"]["2a"]
    assert "International District Energy Association" in s["footnotes"]["1c"]
    assert '"M" to represent million' in s["footnotes"]["5"]
    assert set(fs.regions) == {"US", "CA"} and fs.conventions["M"] == "million"
    assert "52fb681b4c3c" in fs.citation()
    with pytest.raises(ValueError, match="unknown factor set"):
        ef.get_factor_set("nope")


def test_schema_is_valid_and_every_problem_is_reported(doc):
    assert ef.validate_factor_set(doc) == []
    bad = copy.deepcopy(doc)
    bad["schema"] = "x"
    bad["source"]["sha256"] = "abc"
    bad["source"]["url"] = "http://example.org"
    bad["entries"][0]["unit_key"] = "furlong"
    bad["entries"][1]["multiplier"] = 999
    bad["entries"].append(dict(doc["entries"][2]))
    p = "\n".join(ef.validate_factor_set(bad))
    for frag in ("schema", "sha256", "https", "unknown unit key", "does not equal", "duplicate"):
        assert frag in p
    assert ef.validate_factor_set([]) == ["a factor set must be a JSON object"]
    with pytest.raises(ValueError, match="is invalid"):
        ef.load_factor_set(bad)


def test_schema_refusals_cover_each_block(doc):
    def probs(mut):
        d = copy.deepcopy(doc)
        mut(d)
        return "\n".join(ef.validate_factor_set(d))

    assert "retrieved" in probs(lambda d: d["source"].update(retrieved="Sept 2026"))
    assert "edition" in probs(lambda d: d["source"].update(edition="August 2015"))
    assert "conventions.M" in probs(lambda d: d["conventions"].update(M="lots"))
    assert "output_unit" in probs(lambda d: d.update(output_unit="GJ"))
    assert "unknown region" in probs(lambda d: d["entries"][0].update(region="MX"))
    assert "unknown meter type" in probs(lambda d: d["entries"][0].update(meter_type="plasma"))
    assert "unknown footnote" in probs(lambda d: d["entries"][1].update(footnotes=["9"]))
    assert "names both" in probs(lambda d: d["meter_types"][1]["aliases"].append("electricity"))
    assert "lower_snake_case" in probs(lambda d: d["meter_types"][0].update(key="Elec"))
    assert "printed number" in probs(lambda d: d["entries"][0].update(multiplier_text="one"))
    assert "no heat content" in probs(
        lambda d: d["entries"][0].update(
            heat_content=next(e["heat_content"] for e in d["entries"] if e["heat_content"])
        )
    )
    assert "quick reference" in probs(
        lambda d: d["quick_reference"][0].update(kbtu=3.412142, kbtu_text="3.412142")
    )
    for key in ("regions", "meter_types", "entries", "source"):
        assert probs(lambda d, k=key: d.pop(k))


def test_the_factor_set_files_are_named_after_their_set():
    here = os.path.join(_ROOT, "camber", "energy_factors")
    for f in os.listdir(here):
        if f.endswith(".json"):
            with open(os.path.join(here, f), encoding="utf-8") as fh:
                assert json.load(fh)["name"] == f[: -len(".json")]


# --------------------------------------------------------------------------- transcription


def test_every_entry_matches_the_hand_checked_table(fs):
    counts = {}
    for mt, (hc_us, hc_ca, rows) in FIG3.items():
        assert fs.units(mt, "US") == fs.units(mt, "CA")
        assert set(fs.units(mt, "US")) == set(rows), mt
        for key, (us, ca) in rows.items():
            for region, text, hc in (("US", us, hc_us), ("CA", ca, hc_ca)):
                e = fs.entry(mt, region, key)
                assert e.multiplier_text == text, (mt, region, key)
                assert e.multiplier == float(text.replace(",", ""))
                if e.heat_content is not None:
                    assert e.heat_content["text"] == hc, (mt, region, key)
                assert (e.discrepancy is not None) == ((mt, region, key) in DISCREPANT)
        counts[mt] = len(rows)
        for region, hc in (("US", hc_us), ("CA", hc_ca)):
            printed = fs.meter_types[mt]["heat_content"][region]
            assert printed.get("text") == hc if hc else "value" not in printed
    assert set(counts) == set(fs.meter_types)
    assert len(fs.entries) == 2 * sum(counts.values()) == 210


def test_heat_contents_follow_the_meter_type_and_energy_units_take_none(fs):
    for e in fs.entries:
        kind = ef.UNIT_KEYS[e.unit_key][0]
        if kind == "energy":
            assert e.heat_content is None
    # gaseous propane has no printed gas-phase heat content (note 4)
    assert fs.entry("propane", "US", "ft3").heat_content is None
    assert "note 4" in fs.entry("propane", "US", "kcf").note
    assert fs.entry("coal_anthracite", "US", "short_ton").heat_content["unit_key"] == (
        "MMBtu/short_ton"
    )
    assert "5" in fs.entry("natural_gas", "US", "MMcf").footnotes
    assert "3" in fs.entry("fuel_oil_5_6", "CA", "gal_US").footnotes
    assert "1c" in fs.entry("district_steam", "US", "klb").footnotes
    assert "2b" in fs.entry("district_steam", "CA", "klb").footnotes


def test_quick_reference_multipliers(fs):
    q = {r["unit_key"]: (r["kbtu_text"], r["gj_text"]) for r in fs.quick_reference}
    assert q == {
        "kWh": ("3.412", "0.00360"),
        "MWh": ("3412", "3.60"),
        "kBtu": ("1", "0.00106"),
        "MMBtu": ("1000", "1.06"),
        "GJ": ("947.817", "1"),
    }
    for r in fs.quick_reference:  # the GJ column is the kBtu column / 947.817, to its digits
        decimals = len(r["gj_text"].split(".")[1]) if "." in r["gj_text"] else 0
        assert r["gj"] == pytest.approx(r["kbtu"] / 947.817, abs=0.5 * 10.0**-decimals)
    # 947.817 kBtu per GJ is the IT-Btu value, rounded
    assert eu.convert(1.0, "GJ", "kBtu") == pytest.approx(947.817, abs=5e-4)


# --------------------------------------------------------------------------- consistency


def test_internal_consistency_heat_content_times_unit_size(fs):
    gal_uk = ef.UNIT_KEYS["gal_UK"][1] / ef.UNIT_KEYS["gal_US"][1]
    assert gal_uk == pytest.approx(1.200950, abs=1e-6)
    us = lambda mt, k: fs.entry(mt, "US", k).multiplier  # noqa: E731
    assert us("natural_gas", "MMcf") == 1000 * us("natural_gas", "kcf") == 1e6 * 1.026
    assert us("district_steam", "kg") == pytest.approx(1.194 / 0.45359237, abs=5e-4)
    assert us("coal_anthracite", "short_ton") == 2000 * us("coal_anthracite", "lb")
    assert us("fuel_oil_2", "L") == pytest.approx(138 / 3.785411784, abs=5e-4)


def test_a_consistent_row_marked_discrepant_or_an_unmarked_discrepancy_is_refused(doc):
    d = copy.deepcopy(doc)
    for e in d["entries"]:
        e.pop("consistency", None)
    p = ef.validate_factor_set(d)
    assert len(p) == len(DISCREPANT) and all("source_discrepancy" in x for x in p)
    d = copy.deepcopy(doc)
    i = next(i for i, e in enumerate(d["entries"]) if e["unit_key"] == "kcf")
    d["entries"][i]["consistency"] = {"status": "source_discrepancy", "note": "x"}
    assert any("but is consistent" in x for x in ef.validate_factor_set(d))
    d["entries"][i]["consistency"] = {"status": "other"}
    assert any("consistency must be" in x for x in ef.validate_factor_set(d))
    d = copy.deepcopy(doc)
    d["entries"][i]["heat_content"] = {"value": 1, "value_text": "1", "unit_key": "Btu/lb"}
    assert any("heat_content must be" in x for x in ef.validate_factor_set(d))


def test_bps_eui_constants_agree_with_energy_star(fs):
    """bps.EUI_FACTORS_KBTU is kept as it is (outputs must not move). Its electricity, gas and
    chilled-water rows equal ENERGY STAR's; its propane and fuel-oil rows are other published
    values and differ from ENERGY STAR's by < 0.5% (pinned here so a change is deliberate)."""
    from camber.bps import EUI_FACTORS_KBTU as F

    us = lambda mt, k: fs.entry(mt, "US", k).multiplier  # noqa: E731
    assert F["electricity"] == us("electricity", "kWh") == 3.412
    assert F["natural_gas"] == us("natural_gas", "therm") == 100.0
    assert F["district_chw"] == us("district_chilled_water", "ton-hour") == 12.0
    assert (F["propane"], us("propane", "gal_US")) == (91.6, 92)
    assert (F["fuel_oil"], us("fuel_oil_2", "gal_US")) == (138.7, 138)
    # the exact factor in camber.energy_units is unchanged, and differs from the rounded 3.412
    assert eu.KBTU_PER_KWH == pytest.approx(3.412142, abs=5e-7)


# --------------------------------------------------------------------------- units + "M"


def test_unambiguous_labels_resolve(fs):
    for text, key in [
        ("kcf", "kcf"), ("Kcf", "kcf"), ("thousand cubic feet", "kcf"), ("MMcf", "MMcf"),
        ("million cf", "MMcf"), ("MMBtu", "MMBtu"), ("dekatherm", "MMBtu"), ("klb", "klb"),
        ("MMlb", "MMlb"), ("million pounds", "MMlb"), ("gallons", "gal_US"),
        ("UK gallons", "gal_UK"), ("litres", "L"), ("tons", "short_ton"), ("tonnes", "tonne"),
        ("therms", "therm"), ("ton-hours", "ton-hour"), ("CCF", "CCF"), ("m³", "m3"),
        ("kWh", "kWh"), ("GJ", "GJ"), ("lbs", "lb"), ("kg", "kg"), ("cf", "ft3"),
    ]:  # fmt: skip
        assert ef.resolve_unit(text, factor_set=ES) == (key, ()), text


def test_bare_mcf_keeps_the_thousand_reading_with_a_caveat():
    key, cav = ef.resolve_unit("Mcf", factor_set=ES)
    assert key == "kcf" and len(cav) == 1
    assert "THOUSAND" in cav[0] and "MILLION" in cav[0] and "1,000x" in cav[0]
    assert ES in cav[0] and "note 5" in cav[0]
    assert "ENERGY STAR" in ef.resolve_unit("MSCF")[1][0]  # without a set, still named
    # camber.energy_units' own reading is unchanged: Mcf = 1,000 ft3, MBtu / Mlb refused
    assert eu.parse_unit("Mcf").scale == 1000.0
    for amb in ("MBtu", "Mlb"):
        with pytest.raises(ValueError, match="ambiguous"):
            eu.parse_unit(amb)
        with pytest.raises(ValueError, match="ambiguous"):
            ef.resolve_unit(amb, factor_set=ES)
    with pytest.raises(ValueError, match="factor set"):
        eu.parse_unit("MMcf")


def test_unit_refusals():
    with pytest.raises(ValueError, match="no unit"):
        ef.resolve_unit(" ")
    with pytest.raises(ValueError, match="not a unit factor sets list"):
        ef.resolve_unit("Wh")
    with pytest.raises(ValueError, match="unknown"):
        ef.resolve_unit("furlongs")


# --------------------------------------------------------------------------- to_kbtu


@pytest.mark.parametrize(
    "mt,unit,region,expect",
    [
        ("electricity", "kWh", "US", 3.412),
        ("electricity", "MWh", "CA", 3412),
        ("natural_gas", "kcf", "US", 1026),
        ("natural_gas", "MMcf", "CA", 1_031_430),
        ("natural_gas", "therms", "US", 100),
        ("natural_gas", "m3", "CA", 36.425),
        ("fuel_oil_1", "gallons", "US", 139),
        ("fuel_oil_2", "liters", "CA", 36.775),
        ("fuel_oil_4", "UK gallons", "US", 175.333),
        ("Fuel Oil (No. 5 & No. 6)", "gal", "CA", 152.485),
        ("diesel", "gal", "US", 138),
        ("kerosene", "L", "CA", 35.714),
        ("propane", "gallons", "US", 92),
        ("propane", "ccf", "CA", 251.6),
        ("steam", "klb", "US", 1194),
        ("district_steam", "kg", "CA", 2.632),
        ("hot_water", "therm", "US", 100),
        ("district_chw", "ton-hours", "CA", 12.0),
        ("coal_anthracite", "tonnes", "US", 27658.355),
        ("Coal (bituminous)", "MMlb", "CA", 10_748_245),
        ("coke", "tons", "US", 24800),
        ("wood", "tons", "CA", 15477),
        ("other", "kBtu", "US", 1.0),
        ("gas", "MMBtu", "US", 1000),
        ("electricity", "GJ", "CA", 947.817),
    ],
)
def test_to_kbtu_each_meter_type(mt, unit, region, expect):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = ef.to_kbtu(2.0, unit, mt, factor_set=ES, region=region)
    assert got == pytest.approx(2.0 * expect, rel=1e-12)


def test_to_kbtu_arrays_warnings_and_refusals():
    arr = ef.to_kbtu(np.array([1.0, 2.0]), "kcf", "natural_gas", factor_set=ES, region="US")
    assert list(arr) == [1026.0, 2052.0]
    with pytest.warns(ef.EnergyFactorWarning, match="THOUSAND"):
        assert ef.to_kbtu(1.0, "Mcf", "natural_gas", factor_set=ES, region="US") == 1026.0
    with pytest.warns(ef.EnergyFactorWarning, match="not consistent"):
        assert ef.to_kbtu(1.0, "tonnes", "wood", factor_set=ES, region="US") == 15857.0
    with pytest.raises(ValueError, match="no region"):
        ef.to_kbtu(1.0, "kcf", "natural_gas", factor_set=ES, region="MX")
    with pytest.raises(ValueError, match="unknown meter type"):
        ef.to_kbtu(1.0, "kcf", "plasma", factor_set=ES, region="US")
    with pytest.raises(ValueError, match="lists no 'gal_US' for natural_gas"):
        ef.to_kbtu(1.0, "gallons", "natural_gas", factor_set=ES, region="US")
    with pytest.raises(ValueError, match="did you mean ton-hour"):
        ef.to_kbtu(1.0, "tons", "district_chilled_water", factor_set=ES, region="US")
    c = ef.factor_for("klb", "steam", factor_set=ES, region="US")
    assert c.as_dict()["heat_content"] == "1,194 Btu/Lb" and c.multiplier == 1194.0
    assert "1 kLbs (thousand pounds) = 1,194 kBtu, heat content 1,194 Btu/Lb" in c.describe()


# --------------------------------------------------------------------------- docs


def test_generated_reference_docs_are_in_sync():
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    try:
        import energy_factors_refresh as r
    finally:
        sys.path.pop(0)
    with open(r.DOCS, encoding="utf-8") as fh:
        cur = fh.read()
    assert r.docs_text(cur) == cur, "run: python scripts/energy_factors_refresh.py --docs --write"
    md = ef.reference_markdown(ES)
    assert "| Mcf (million cubic feet) | `MMcf` | 1,026,000 |" in md
    assert "15,857 (!)" in md


def test_refresh_script_pins_in_place(tmp_path):
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    try:
        import energy_factors_refresh as r
    finally:
        sys.path.pop(0)
    with open(_JSON, encoding="utf-8") as fh:
        text = fh.read()
    new = r.pin_text(text, sha256="0" * 64, retrieved="2027-01-01", edition="2026-08")
    d = json.loads(new)
    assert d["source"]["sha256"] == "0" * 64 and d["source"]["edition"] == "2026-08"
    assert len(new.splitlines()) == len(text.splitlines())  # layout kept
    src = tmp_path / "ref.pdf"
    src.write_bytes(b"not the reference")
    assert r.source_sha256("https://example.invalid/x.pdf", str(src)) != d["source"]["sha256"]
    assert r.main(["--validate"]) == 0
    assert r.main(["--check", ES, "--local", str(src)]) == 1
    assert r.main(["--docs"]) == 0
    with pytest.raises(ValueError, match="no source.sha256"):
        r.pin_text("{}", sha256="0" * 64, retrieved="2027-01-01", edition=None)
