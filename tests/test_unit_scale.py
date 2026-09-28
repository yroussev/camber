"""Unit-scale plausibility (#71): is each meter's billed quantity right at x0.001, x1 or x1000?

Synthetic bills with injected 1000x errors (steam billed "MLb" for thousands of pounds, gas in
MMcf for Mcf, electricity in MWh labelled kWh with a tariff recompute, fuel oil), correct bills
that must not be flagged (large, small, laboratory, data centre, restaurant), the offline and EIA
paths without keys, the explicit override, and the billing M&V / benchmark / bps / ingest hooks.
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_factors as ef  # noqa: E402
from camber import unit_scale as us  # noqa: E402
from camber.bps import site_eui_plausibility  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.interop import eia  # noqa: E402
from camber.mandv.billing import BillingSeries  # noqa: E402
from camber.tariff import flat_tariff, tou_tariff  # noqa: E402

HDD = np.array([1000, 850, 700, 400, 150, 20, 0, 0, 60, 350, 650, 950], float)


def _months(n=24, start="2023-01-01"):
    st = pd.date_range(start, periods=n, freq="MS")
    return st, st + pd.offsets.MonthBegin(1)


def _heating(area, eui_kbtu, kbtu_per_unit, price_per_mmbtu, *, n=24, base=0.2, fixed=40.0):
    """Monthly heating-fuel bills: ``eui_kbtu`` kBtu/ft2/yr, 80 % weather-driven."""
    st, en = _months(n)
    hdd = np.resize(HDD, n)
    kbtu = eui_kbtu * area * ((1 - base) * hdd / HDD.sum() + base / 12)
    q = kbtu / kbtu_per_unit
    return pd.DataFrame(
        {
            "start": st,
            "end": en,
            "quantity": q,
            "cost": kbtu / 1000 * price_per_mmbtu + fixed,
            "hdd": hdd,
        }
    )


def _elec(area, eui_kbtu, *, n=24, price=0.12, lf=0.55, tariff=None):
    st, en = _months(n)
    kwh = eui_kbtu * area / 3.412142 / 12 * (1 + 0.15 * np.sin(np.arange(n) / 12 * 2 * np.pi))
    hours = ((en - st) / pd.Timedelta(hours=1)).to_numpy(float)
    kw = kwh / (hours * lf)
    if tariff is not None:
        cost = [us._bill_cost(tariff, s, e, q, d) * 1.05 for s, e, q, d in zip(st, en, kwh, kw)]
    else:
        cost = kwh * price + 50
    return pd.DataFrame({"start": st, "end": en, "quantity": kwh, "cost": cost, "demand": kw})


# --------------------------------------------------------------------------- reference sets


def test_reference_sets_are_registered_by_kind_and_validated():
    assert ef.factor_sets() == ["energy_star_thermal_2015"]  # the default is unchanged
    assert ef.factor_sets("price_band") == ["camber_price_bands_2024"]
    assert ef.factor_sets("eui_reference") == ["energy_star_us_median_eui_2024"]
    assert len(ef.factor_sets(None)) == 3
    with pytest.raises(ValueError, match="get_reference_set"):
        ef.get_factor_set("camber_price_bands_2024")
    with pytest.raises(ValueError, match="unknown reference set"):
        ef.get_reference_set("nope")
    pb = ef.get_reference_set(us.DEFAULT_PRICE_BANDS)
    for mt in ef.PRICE_BAND_METER_TYPES:
        b = pb.band(mt)
        # the invariant: an in-band price read 1000x off is outside the implausible bounds
        assert b["plausible"][1] / 1000 < b["implausible_below"]
        assert b["plausible"][0] * 1000 > b["implausible_above"]
    assert pb.band("coal_anthracite") is None
    assert "Energy Information Administration" in pb.citation()
    eui = ef.get_reference_set(us.DEFAULT_EUI_REFERENCE)
    assert eui.property_type("Office")["site_eui"] == 52.9
    assert eui.property_type("college laboratory")["key"] == "laboratory"
    assert eui.property_type("Data Center")["site_eui"] is None
    assert eui.property_type(None) is None and eui.property_type("spaceport") is None
    # every printed Portfolio Manager row, in the PDF's order (the full table, 0.92)
    pts = eui.doc["property_types"]
    assert len(pts) == 46 and len({p["key"] for p in pts}) == 46
    keys = [p["key"] for p in pts]
    # page 4: Public Services opens with the row after Parking, then fire/police station
    ps = [p for p in pts if p["broad_category"] == "Public Services"]
    assert ps[0]["reference_data"] == "CBECS - " + ps[0]["name"]
    assert (ps[0]["site_eui_text"], ps[0]["source_eui_text"]) == ("101.2", "211.4")
    assert ps[0]["key"] == ps[0]["name"].lower() and ps[0]["aliases"] == []
    assert keys.index(ps[0]["key"]) + 1 == keys.index("fire_police_station")
    assert keys.index("office") + 1 == keys.index(ps[0]["key"])
    assert eui.property_type(ps[0]["name"])["site_eui"] == 101.2
    assert eui.doc["source"]["sha256"].startswith("5e5dfab8")
    assert "5e5dfab80800" in eui.citation()
    assert "policy" in eui.policy["note"].lower()
    with pytest.raises(ValueError, match="not an eui_reference"):
        pb.property_type("office")
    with pytest.raises(ValueError, match="not a price_band"):
        eui.band("electricity")


def _doc(name):
    path = os.path.join(os.path.dirname(ef.__file__), f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_price_band_validator_catches_problems():
    d = _doc("camber_price_bands_2024")
    assert ef.validate_factor_set(d) == []
    bad = json.loads(json.dumps(d))
    bad["bands"][0]["plausible"] = [5, 900]  # breaks the 1000x invariant
    bad["bands"][1]["meter_type"] = "unobtainium"
    bad["bands"][2]["sources"] = ["ghost"]
    bad["bands"][3]["plausible"] = [0, 1]
    bad["bands"][4]["implausible_below"] = -1
    bad["bands"].append(dict(d["bands"][5]))  # a duplicate
    bad["sources"][0]["url"] = "http://x"
    bad["sources"][0]["retrieved"] = "yesterday"
    bad["sources"][3]["sha256"] = "abc"
    bad["policy"] = "bands"
    bad["output_unit"] = "USD/kWh"
    p = "\n".join(ef.validate_factor_set(bad))
    for frag in (
        "invariant",
        "unobtainium",
        "unknown source 'ghost'",
        "both positive",
        "must be positive",
        "duplicate band",
        "url must be https",
        "YYYY-MM-DD",
        "64 lowercase hex",
        "CAMBER screening policy",
        "output_unit",
    ):
        assert frag in p, frag
    assert ef.validate_factor_set({**d, "sources": []})[-1].startswith("'sources'")
    assert ef.validate_factor_set({**d, "bands": []})[-1].startswith("'bands'")
    with pytest.raises(ValueError, match="invalid"):
        ef.load_reference_set(bad)


def test_eui_reference_validator_catches_problems():
    d = _doc("energy_star_us_median_eui_2024")
    assert ef.validate_factor_set(d) == []
    bad = json.loads(json.dumps(d))
    bad["property_types"][0]["site_eui"] = 88.4  # text says 88.3
    bad["property_types"][1]["aliases"] = ["bank"]  # already the bank branch's
    bad["property_types"][2]["key"] = "Bad Key"
    bad["property_types"][-1]["site_eui_text"] = "none"
    bad["policy"]["total"]["implausible_low_factor"] = 2
    bad["policy"]["single_fuel"]["implausible_high_factor"] = 1
    bad["policy"]["hard"]["nuclear"] = {"implausible_above": -3}
    bad["policy"]["generic_plausible"]["thermal"] = [9, 1]
    bad["policy"]["generic_plausible"]["gold"] = [1]
    bad["source"]["sha256"] = "x"
    bad["source"]["url"] = "ftp://x"
    bad["source"]["retrieved"] = "2026"
    p = "\n".join(ef.validate_factor_set(bad))
    for frag in (
        "does not equal its text",
        "names both",
        "lower_snake_case",
        "printed 'N/A'",
        "must exceed the plausible",
        "plausible_high_factor < implausible_high_factor",
        "unknown group 'nuclear'",
        "positive",
        "low must be below high",
        "must be [low, high]",
        "sha256",
        "https",
        "YYYY-MM-DD",
    ):
        assert frag in p, frag
    assert "four positive" in "\n".join(
        ef.validate_factor_set({**d, "policy": {**d["policy"], "total": {}}})
    )
    assert ef.validate_factor_set({**d, "policy": {"note": "x"}})[-1].startswith("'policy'")
    assert ef.validate_factor_set({**d, "property_types": []})[-1].startswith("'property_types'")
    assert ef.validate_factor_set({**d, "source": 1})[-1] == "'source' must be an object"


# --------------------------------------------------------------------------- units and fuels


def test_fuel_groups_and_screening_conversions():
    assert us.fuel_group("steam") == "district_steam"
    assert us.fuel_group("chilledwater") == "district_chilled_water"
    assert us.fuel_group("fuel_oil_5_6") == "fuel_oil"
    assert us.fuel_group("coal_bituminous") is None
    assert us.fuel_group("unobtainium") is None
    assert us.fuel_group(None, "MWh") == "electricity"
    assert us.fuel_group(None, "Mlb") == "district_steam"
    assert us.fuel_group(None, "gal") is None
    k, reading, notes = us.kbtu_per_unit("kWh")
    assert k == pytest.approx(3.412142, rel=1e-6) and notes == []
    k, _, notes = us.kbtu_per_unit("Mlb", "steam")
    assert k == 1194 and "thousand pounds" in notes[0]
    assert us.kbtu_per_unit("MMcf", "natural_gas")[0] == 1_026_000
    assert us.kbtu_per_unit("Mcf", "gas", heat_content="1030 Btu/ft3")[0] == pytest.approx(1030)
    assert us.kbtu_per_unit("klb", enthalpy="1000 Btu/lb")[0] == pytest.approx(1000)
    assert us.kbtu_per_unit("gal", "fuel_oil")[0] == pytest.approx(138)
    with pytest.raises(ValueError, match="needs a fuel"):
        us.kbtu_per_unit("gal")


# --------------------------------------------------------------------------- synthetic detection


def test_steam_billed_mlb_for_thousands_is_caught_in_the_maintainers_direction():
    area = 200_000.0
    b = _heating(area, 40, 1194.0, 25.0)  # the quantities are klb
    ok = us.check_bills(b, unit="klb", fuel="steam", area=area, property_type="office")
    assert ok.status == "plausible" and ok.scale == 1.0 and ok.confidence == "high"
    # read as million pounds (the "M" taken for a million): 1000x too much steam
    bad = us.check_bills(b, unit="MMlb", fuel="steam", area=area, property_type="office")
    assert bad.implausible and bad.scale == 0.001 and bad.confidence == "high"
    names = {e.name for e in bad.evidence if e.verdicts[1.0] == -1}
    assert {"price", "eui", "weather_intensity"} <= names
    w = next(e for e in bad.evidence if e.name == "weather_intensity")
    assert w.metrics["peak_bill_lb_h"]["x0.001"] < w.metrics["peak_bill_lb_h"]["x1"]
    assert "Not corrected" in bad.explanation and "scale_override" in bad.explanation
    # an ambiguous "Mlb" is read with the US utility M (thousand), noted
    amb = us.check_bills(b, unit="Mlb", fuel="steam", area=area, property_type="office")
    assert amb.status == "plausible" and any("thousand pounds" in n for n in amb.notes)
    # price alone decides (no area): decisive price evidence is enough on its own
    alone = us.check_bills(b[["start", "end", "quantity", "cost"]], unit="MMlb", fuel="steam")
    assert alone.implausible and alone.scale == 0.001 and alone.confidence == "medium"


def test_gas_mmcf_versus_mcf():
    area = 80_000.0
    b = _heating(area, 45, 1026.0, 9.0)  # quantities in Mcf (thousand cf)
    ok = us.check_bills(b, unit="Mcf", fuel="natural_gas", area=area, property_type="office")
    assert ok.status == "plausible" and ok.scale == 1.0
    bad = us.check_bills(b, unit="MMcf", fuel="natural_gas", area=area, property_type="office")
    assert bad.implausible and bad.scale == 0.001 and bad.confidence == "high"
    # the reverse: million cf billed but entered as Mcf -> 1000x too little gas
    small = b.assign(quantity=b["quantity"] / 1000.0)
    low = us.check_bills(small, unit="Mcf", fuel="natural_gas", area=area, property_type="office")
    assert low.implausible and low.scale == 1000.0


def test_electricity_mwh_labelled_kwh_with_a_tariff_recompute():
    area = 150_000.0
    t = flat_tariff(0.10, demand_rate=14.0, fixed_monthly=75.0)
    b = _elec(area, 50, tariff=t)
    ok = us.check_bills(b, unit="kWh", tariff=t)
    tar = next(e for e in ok.evidence if e.name == "tariff")
    assert tar.verdicts == {0.001: -1, 1.0: 1, 1000.0: -1}
    assert ok.status == "plausible" and ok.confidence == "high"
    mwh = b.assign(quantity=b["quantity"] / 1000.0)  # MWh numbers under a kWh label
    bad = us.check_bills(mwh, unit="kWh", tariff=t, area=area, property_type="office")
    assert bad.implausible and bad.scale == 1000.0 and bad.confidence == "high"
    assert {"tariff", "price", "load_factor", "eui"} <= {
        e.name for e in bad.evidence if e.verdicts[1000.0] == 1
    }
    # the same numbers correctly labelled MWh are plausible, tariff and load factor in kWh
    right = us.check_bills(mwh, unit="MWh", tariff=t)
    assert right.status == "plausible"
    # a URDB rate dict works the same way
    urdb = {
        "name": "flat",
        "fixedchargefirstmeter": 75.0,
        "fixedchargeunits": "$/month",
        "energyratestructure": [[{"rate": 0.10}]],
        "flatdemandstructure": [[{"rate": 14.0}]],
        "flatdemandmonths": [0] * 12,
    }
    via = us.check_bills(mwh, unit="kWh", urdb=urdb)
    assert via.implausible and via.scale == 1000.0


def test_tariff_bill_cost_places_the_peak_and_one_fixed_charge():
    t = tou_tariff(0.08, 0.20, range(12, 18), demand_rate=10.0, fixed_monthly=30.0)
    s, e = pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-14")
    cost = us._bill_cost(t, s, e, 72_000.0, 200.0)
    assert 30 + 200 * 10 + 72_000 * 0.08 < cost < 30 + 200 * 10 + 72_000 * 0.20
    assert us._bill_cost(t, s, s, 1.0, 1.0) == 30.0  # an empty period: the fixed charge only


def test_fuel_oil_gallons_entered_in_thousands():
    area = 30_000.0
    b = _heating(area, 60, 138.0, 28.0)  # gallons
    ok = us.check_bills(b, unit="gal", fuel="fuel_oil_2", area=area, property_type="k-12 school")
    assert ok.status == "plausible"
    kgal = b.assign(quantity=b["quantity"] * 1000.0)  # the quantity field held thousandths
    bad = us.check_bills(kgal, unit="gal", fuel="fuel_oil_2", area=area)
    assert bad.implausible and bad.scale == 0.001


@pytest.mark.parametrize(
    ("label", "frame", "unit", "fuel", "area", "ptype"),
    [
        (
            "large hospital steam",
            _heating(1_200_000, 150, 1194.0, 22.0),
            "klb",
            "steam",
            1.2e6,
            "hospital",
        ),
        (
            "small retail gas",
            _heating(2_000, 40, 100.0, 14.0, fixed=25.0),
            "therm",
            "gas",
            2e3,
            "retail store",
        ),
        (
            "laboratory steam",
            _heating(90_000, 420, 1194.0, 30.0),
            "klb",
            "steam",
            9e4,
            "laboratory",
        ),
        ("restaurant gas", _heating(4_000, 900, 100.0, 11.0), "therm", "gas", 4e3, "restaurant"),
        (
            "data centre electricity",
            _elec(40_000, 2500, lf=0.9),
            "kWh",
            "electricity",
            4e4,
            "data center",
        ),
        ("small office electricity", _elec(3_000, 45), "kWh", "electricity", 3e3, "office"),
        (
            "warehouse electricity",
            _elec(250_000, 8, lf=0.3),
            "kWh",
            "electricity",
            2.5e5,
            "warehouse",
        ),
        (
            "mild-climate hot water",
            _heating(60_000, 15, 100.0, 20.0),
            "therm",
            "hot_water",
            6e4,
            None,
        ),
    ],
)
def test_correct_bills_are_not_flagged(label, frame, unit, fuel, area, ptype):
    c = us.check_bills(frame, unit=unit, fuel=fuel, area=area, property_type=ptype, label=label)
    assert not c.implausible, (label, c.explanation)
    assert c.scale == 1.0 and c.status == "plausible"


def test_insufficient_and_uncertain_statuses():
    b = _heating(50_000, 50, 100.0, 12.0)[["start", "end", "quantity"]]
    c = us.check_bills(b, unit="therm", fuel="gas")
    assert c.status == "insufficient" and c.scale is None
    assert "no evidence" in c.explanation and c.finding("m").severity == "ok"
    # one policy-weight EUI item against x1 cannot rule it out alone -> uncertain at most
    big = _heating(10_000, 2000, 100.0, 12.0)[["start", "end", "quantity"]]
    u = us.check_bills(big, unit="therm", fuel="gas", area=10_000, property_type="office")
    assert not u.implausible and u.status in ("uncertain", "plausible")
    if u.status == "uncertain":
        assert "not ruled out" in u.explanation and u.finding("m").severity == "info"


def test_the_combination_rule():
    E = us.Evidence

    def ev(name, verdicts, w):
        return E(name, dict(zip(us.SCALES, verdicts)), dict.fromkeys(us.SCALES, w), "")

    # a price band (3) outweighs EUI + weather (2 + 2)
    scale, status, _, ruled = us._combine(
        [ev("price", (-1, 1, -1), 3), ev("eui", (1, -1, -1), 2), ev("weather", (1, -1, -1), 2)]
    )
    assert scale == 1.0 and status == "plausible" and 1.0 not in ruled
    # EUI + weather together rule x1 out when nothing supports it
    scale, status, conf, _ = us._combine([ev("eui", (1, -1, -1), 2), ev("w", (1, -1, -1), 2)])
    assert (scale, status, conf) == (0.001, "implausible", "high")
    # equal weights on both sides rule nothing out
    _, status, _, ruled = us._combine([ev("price", (0, 1, 0), 3), ev("eui", (0, -1, 0), 3)])
    assert status == "plausible" and not ruled
    # the tariff decides alone
    scale, _, conf, _ = us._combine([ev("tariff", (-1, -1, 1), 4)])
    assert scale == 1000.0 and conf == "high"
    # everything ruled out
    scale, status, conf, _ = us._combine([ev("eui", (-1, -1, -1), 3)])
    assert scale is None and status == "implausible" and conf == "low"
    assert us._combine([ev("x", (0, 0, 0), 2)])[1] == "insufficient"


# --------------------------------------------------------------------------- other evidence


def test_meter_reads_and_load_factor():
    st, en = _months(12)
    reads = np.cumsum(np.full(12, 400.0))
    b = pd.DataFrame(
        {
            "start": st,
            "end": en,
            "quantity": np.full(12, 400.0 * 40 * 1000),  # 1000x the reads x multiplier
            "read_start": reads - 400.0,
            "read_end": reads,
            "multiplier": 40.0,
            "demand": 90.0,
        }
    )
    c = us.check_bills(b, unit="kWh")
    mr = next(e for e in c.evidence if e.name == "meter_reads")
    assert mr.verdicts == {0.001: 1, 1.0: -1, 1000.0: -1}
    lf = next(e for e in c.evidence if e.name == "load_factor")
    assert lf.verdicts[1.0] == -1  # 16,000 kWh/month x 1000 over 90 kW is > 100 % load factor
    assert c.implausible and c.scale == 0.001 and c.confidence == "high"


def test_continuity_steps_bill_to_bill_and_year_over_year():
    b = _elec(100_000, 40)[["start", "end", "quantity"]]
    b.loc[12:, "quantity"] /= 1000.0  # the utility switched to MWh in 2024
    c = us.check_bills(b, unit="kWh")
    assert c.implausible and len(c.steps) == 1 and c.steps[0]["start"] == "2024-01-01"
    assert c.steps[0]["direction"] == "down" and 500 < c.steps[0]["ratio"] < 2000
    assert len(c.segments) == 2 and "Segments" in c.explanation
    assert c.evidence[0].name == "continuity"
    # a heating meter's seasonal 1000x swing is not a step
    g = _heating(40_000, 50, 100.0, 12.0, base=0.001)[["start", "end", "quantity"]]
    g.loc[g["quantity"] < g["quantity"].max() / 300, "quantity"] = g["quantity"].max() / 3000
    assert not us.check_bills(g, unit="therm", fuel="gas").steps
    # a gas meter's unit change one year in, confirmed against the year before
    y = _heating(40_000, 50, 100.0, 12.0, n=36)[["start", "end", "quantity"]]
    y.loc[24:, "quantity"] /= 1000.0
    cy = us.check_bills(y, unit="therm", fuel="gas")
    assert cy.implausible and cy.steps[0]["start"] == "2025-01-01"
    assert "year over year" in cy.steps[0]["found_by"]
    # one year of a seasonal meter with a 1000x drop: noted, not flagged
    one = _heating(40_000, 50, 100.0, 12.0, n=12)[["start", "end", "quantity"]]
    one.loc[6:, "quantity"] /= 1000.0
    c1 = us.check_bills(one, unit="therm", fuel="gas")
    assert not c1.steps and any("no year before it" in n for n in c1.notes)
    # a meter outage filled with one repeated value is not a unit change
    flat = _elec(100_000, 40)[["start", "end", "quantity"]]
    flat.loc[18:, "quantity"] = 0.05
    assert not us.check_bills(flat, unit="kWh").steps


def test_check_series_eui_and_bps():
    idx = pd.date_range("2016-01-01", "2016-12-31 23:00", freq="h")
    t = np.arange(len(idx))
    oat = pd.Series(50 - 25 * np.cos(2 * np.pi * t / len(idx)), index=idx)
    chw = pd.Series(np.maximum(0, oat - 55) * 40.0, index=idx)  # kWh per hour
    ok = us.check_series(chw, unit="kWh", fuel="chilledwater", area=50_000, oat=oat)
    assert not ok.implausible
    bad = us.check_series(chw * 1000, unit="kWh", fuel="chilledwater", area=50_000, oat=oat)
    assert bad.implausible and bad.scale == 0.001
    with pytest.raises(ValueError, match="no numeric"):
        us.check_series(pd.Series([], dtype=float), unit="kWh")
    with pytest.raises(ValueError, match="no month"):
        us.check_series(chw.iloc[:24], unit="kWh")
    # a stated EUI
    assert us.check_eui(55.0, property_type="office").status == "plausible"
    big = us.check_eui(52_900.0, property_type="office")
    assert big.implausible and big.scale == 0.001
    si = us.check_eui(170.0, "kWh/m2/yr")
    assert si.status == "plausible"
    assert us.check_eui(0.02).implausible
    # bps: per-fuel checks behind an EUI, the total only where it discriminates
    r = site_eui_plausibility(
        {"electricity": 1.5e6, "natural_gas": 3.0e7}, 100_000, property_type="office"
    )
    assert r["natural_gas"].implausible and r["natural_gas"].scale == 0.001
    assert not r["electricity"].implausible
    assert any("total EUI is implausible" in n for n in r["electricity"].notes)
    r2 = site_eui_plausibility(
        {"electricity": 1.5e6, "steam": 3000.0},
        100_000,
        units={"steam": "klb"},
        cost_by_fuel={"electricity": 180_000, "steam": 90_000},
    )
    assert not any(c.implausible for c in r2.values())
    with pytest.raises(ValueError, match="no unit"):
        site_eui_plausibility({"steam": 1.0}, 1000.0)


def test_input_validation_and_serialisation():
    b = _heating(20_000, 50, 100.0, 12.0)
    with pytest.raises(ValueError, match="unit is required"):
        us.check_bills(b, unit="")
    with pytest.raises(ValueError, match="missing"):
        us.check_bills(b.drop(columns="quantity"), unit="therm")
    with pytest.raises(ValueError, match="end after it starts"):
        us.check_bills(b.assign(end=b["start"]), unit="therm")
    with pytest.raises(ValueError, match="price_source"):
        us.check_bills(b, unit="therm", price_source="guess")
    c = us.check_bills(
        b.rename(columns={"quantity": "energy"}), unit="therm", fuel="coal_bituminous"
    )
    assert any("no price band" in n for n in c.notes)
    c = us.check_bills(b, unit="therm", area=20_000, property_type="spaceport", label="m")
    assert any("not in energy_star_us_median_eui_2024" in n for n in c.notes)
    d = json.loads(json.dumps(c.as_dict()))
    assert d["candidates"] == ["x0.001", "x1", "x1000"] and d["status"] == "plausible"
    f = c.finding()
    assert f.rule == "unit_scale" and f.equip == "m" and f.metrics["unit_scale"]["scale"] == 1.0
    # short series: EUI not judged
    short = us.check_bills(b.iloc[:1], unit="therm", area=20_000)
    assert any("EUI not judged" in n for n in short.notes)
    # a BillingSeries input, end dates inclusive
    bs = BillingSeries.from_frame(
        b.assign(end=b["end"] - pd.Timedelta(days=1)), energy="quantity", units="therm"
    )
    assert us.check_bills(bs, unit="therm").status == "insufficient"
    inc = b.assign(end=b["end"] - pd.Timedelta(days=1))
    assert us.check_bills(inc, unit="therm", end_inclusive=True).status == "plausible"


# --------------------------------------------------------------------------- override


def test_scale_override_is_explicit_and_recorded():
    assert us.parse_scale_override(None) is None
    for bad, msg in (
        (0.001, "must be"),
        ({"factor": 0.001}, "reason is required"),
        ({"factor": "x", "reason": "r"}, "must be a number"),
        ({"factor": -1, "reason": "r"}, "positive"),
        ({"factor": 1, "reason": "r", "extra": 1}, "unknown key"),
    ):
        with pytest.raises(ValueError, match=msg):
            us.parse_scale_override(bad)
    area = 200_000.0
    b = _heating(area, 40, 1194.0, 25.0)
    ov = {"factor": 0.001, "reason": "the utility's MLb is thousands of pounds"}
    c = us.check_bills(
        b.assign(quantity=b["quantity"] * 1000.0),
        unit="klb",
        fuel="steam",
        area=area,
        scale_override=ov,
    )
    assert c.status == "plausible" and c.override["factor"] == 0.001
    assert c.explanation.startswith("quantities multiplied by 0.001 (scale_override: the utility")


# --------------------------------------------------------------------------- EIA (opt-in)


def _eia_payload(price):
    return {"response": {"data": [{"period": "2024-01", "price": str(price)}] * 3}}


def test_eia_state_price_opt_in_cached_and_keyless_url(tmp_path):
    seen = []

    def fake(url):
        seen.append(url)
        if "natural-gas" in url:
            return {
                "response": {"data": [{"period": "2024-01", "value": "10.26"}, {"value": None}]}
            }
        return _eia_payload(12.0)

    sp = eia.fetch_state_price("electricity", "ny", "2024-01-05", "2024-03-01", transport=fake)
    assert sp.state == "NY" and sp.native_unit == "cents/kWh"
    assert sp.usd_per_mmbtu == pytest.approx(0.12 / 0.00341214163, rel=1e-4)
    assert "api_key" not in seen[0] and "facets[stateid][]=NY" in seen[0]
    assert "start=2024-01&end=2024-03" in seen[0] and sp.as_dict()["periods"] == ["2024-01"] * 3
    g = eia.fetch_state_price("natural_gas", "MI", "2024-01", "2024-12", transport=fake)
    assert g.usd_per_mmbtu == pytest.approx(10.0) and g.native_unit == "$/Mcf"
    # cached by the key-free URL; offline serves the cache and never calls the network
    cache = str(tmp_path / "eia")
    eia.fetch_state_price(
        "electricity", "TX", "2024-01", "2024-02", transport=fake, cache_dir=cache
    )
    n = len(seen)
    again = eia.fetch_state_price(
        "electricity", "TX", "2024-01", "2024-02", cache_dir=cache, offline=True
    )
    assert len(seen) == n and again.state == "TX"
    for kw, msg in (
        ({"fuel": "steam"}, "cover"),
        ({"state": "ZZ"}, "unknown U.S. state"),
        ({"start": "soon"}, "a date"),
    ):
        args = {"fuel": "electricity", "state": "NY", "start": "2024-01", "end": "2024-02", **kw}
        with pytest.raises(ValueError, match=msg):
            eia.fetch_state_price(**args, transport=fake)
    with pytest.raises(ValueError, match="no electricity price"):
        eia.fetch_state_price("electricity", "NY", "2024-01", "2024-02", transport=lambda u: {})
    with pytest.raises(ValueError, match="give cache_dir"):
        eia.fetch_state_price("electricity", "NY", "2024-01", "2024-02", offline=True)


def test_price_source_eia_and_offline_fallback_without_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    b = _elec(100_000, 45)
    # no key, no transport: falls back to the bundled bands, with a note
    c = us.check_bills(b, unit="kWh", price_source="eia", state="CA")
    pr = next(e for e in c.evidence if e.name == "price")
    assert pr.metrics["reference"].startswith("camber_price_bands_2024")
    assert any("EIA state price unavailable" in n and "EIA_API_KEY" in n for n in c.notes)
    # offline with an empty cache: the same fallback
    c = us.check_bills(
        b, unit="kWh", price_source="eia", state="CA", eia_cache_dir=str(tmp_path), eia_offline=True
    )
    assert any("EIA state price unavailable" in n for n in c.notes)
    # no state
    c = us.check_bills(b, unit="kWh", price_source="eia")
    assert any("needs a state" in n for n in c.notes)
    # with a transport: the state's band
    c = us.check_bills(
        b, unit="kWh", price_source="eia", state="CA", eia_transport=lambda u: _eia_payload(22.0)
    )
    pr = next(e for e in c.evidence if e.name == "price")
    assert pr.metrics["reference"].startswith("EIA CA") and c.status == "plausible"
    # a StatePrice or a number as the reference
    sp = eia.fetch_state_price(
        "electricity", "CA", "2024-01", "2024-02", transport=lambda u: _eia_payload(22.0)
    )
    assert us.check_bills(
        b.assign(quantity=b["quantity"] * 1000), unit="kWh", price_source=sp
    ).implausible
    assert us.check_bills(b, unit="kWh", price_source=35.0).status == "plausible"


# --------------------------------------------------------------------------- config hooks


def _write_bills(tmp_path, factor=1.0, *, units="klb", cost=True, extra=None):
    rng = np.random.default_rng(3)
    days = pd.date_range("2021-01-01", "2023-12-31", freq="D")
    T = 52 - 24 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) + rng.normal(0, 5, len(days))
    klb = np.asarray(0.5 + 0.12 * np.maximum(0, 60 - T))  # klb per day, a 100,000 ft2 office
    rows = []
    for s in pd.date_range("2021-01-01", "2023-12-01", freq="MS"):
        e = s + pd.offsets.MonthBegin(1)
        m = (days >= s) & (days < e)
        q = float(klb[m].sum())
        row = {"start": s.date(), "end": (e - pd.Timedelta(days=1)).date(), "steam": q * factor}
        row["units"] = units
        if cost:
            row["cost"] = round(q * 30 + 100, 2)
        row.update(extra or {})
        rows.append(row)
    pd.DataFrame(rows).to_csv(tmp_path / "steam.csv", index=False)
    pd.DataFrame({"timestamp": days, "oat": np.round(T, 2)}).to_csv(
        tmp_path / "oat.csv", index=False
    )


def _cfg(bills=None, **entry):
    e = {
        "bills": {"file": "steam.csv", "energy": "steam", **(bills or {})},
        "name": "Steam",
        "period": ["2021-01-01", "2022-12-31"],
    }
    e.update(entry)
    return {"site": "Demo", "shared_oat": {"file": "oat.csv"}, "mv": [e]}


def _rules(res):
    return [f.rule for f in res.findings]


def test_billing_path_declines_implausible_bills_and_accepts_an_override(tmp_path):
    sc = {"fuel": "steam", "area": 100_000, "property_type": "office"}
    _write_bills(tmp_path)
    good = run_config(_cfg({"scale_check": sc}), base_dir=str(tmp_path))
    assert _rules(good) == ["mv_baseline"]  # plausible: nothing added
    default = run_config(_cfg(), base_dir=str(tmp_path))
    assert _rules(default) == ["mv_baseline"]
    # billed "MLb" meaning thousands, entered as million pounds: declined with a warning
    _write_bills(tmp_path, units="MMlb")
    res = run_config(_cfg({"scale_check": sc}), base_dir=str(tmp_path))
    assert _rules(res) == ["unit_scale", "mv_baseline"]
    us_f, mv = res.findings
    assert us_f.severity == "warn" and us_f.metrics["scale"] == 0.001
    assert mv.metrics["declined"] and "implausible" in mv.metrics["declined_reason"]
    # price evidence alone (no scale_check block): still caught, from the cost column
    res = run_config(_cfg(), base_dir=str(tmp_path))
    assert _rules(res)[0] == "unit_scale"
    # warn only: the baseline is fitted as billed
    res = run_config(
        _cfg({"scale_check": {**sc, "on_implausible": "warn"}}), base_dir=str(tmp_path)
    )
    assert _rules(res) == ["unit_scale", "mv_baseline"] and not res.findings[1].metrics.get(
        "declined"
    )
    # an explicit override: corrected, recorded, no warning
    ov = {"factor": 0.001, "reason": "the bill's MLb is thousands of pounds"}
    res = run_config(_cfg({"scale_check": sc, "scale_override": ov}), base_dir=str(tmp_path))
    assert _rules(res) == ["mv_baseline"]
    b = res.findings[0]
    assert b.metrics["scale_override"] == ov
    assert any("multiplied by 0.001" in c for c in b.caveats)
    # disabled
    res = run_config(_cfg({"scale_check": False}), base_dir=str(tmp_path))
    assert _rules(res) == ["mv_baseline"]


def test_billing_scale_check_config_errors(tmp_path):
    _write_bills(tmp_path, units="klb")
    for bills, msg in (
        ({"scale_check": 3}, "must be an object"),
        ({"scale_check": {"colour": 1}}, "unknown key"),
        ({"scale_check": {"on_implausible": "panic"}}, "decline"),
        ({"scale_check": {"cost": "dollars"}}, "no column 'dollars'"),
        ({"scale_check": {"tariff": 3}}, "tariff must be an object"),
        ({"scale_check": {"tariff": {"voltage": 1}}}, "tariff"),
        ({"scale_override": {"factor": 0.001}}, "reason is required"),
        ({"scale_check": {"fuel": "coal_bituminous"}, "units": "klb"}, "scale_check"),
    ):
        with pytest.raises(ValueError, match=msg):
            run_config(_cfg(bills), base_dir=str(tmp_path))
    _write_bills(tmp_path, units="", cost=False)
    df = pd.read_csv(tmp_path / "steam.csv").drop(columns="units")
    df.to_csv(tmp_path / "steam.csv", index=False)
    with pytest.raises(ValueError, match="needs the bills' unit"):
        run_config(_cfg({"scale_check": {"fuel": "steam"}}), base_dir=str(tmp_path))
    assert _rules(run_config(_cfg(), base_dir=str(tmp_path))) == ["mv_baseline"]


def test_billing_tariff_from_config_and_urdb_file(tmp_path):
    rng = np.random.default_rng(5)
    days = pd.date_range("2022-01-01", "2023-12-31", freq="D")
    T = 55 - 20 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) + rng.normal(0, 4, len(days))
    kwh = np.asarray(4000 + 60 * np.maximum(0, T - 60))
    rows = []
    for s in pd.date_range("2022-01-01", "2023-12-01", freq="MS"):
        e = s + pd.offsets.MonthBegin(1)
        q = float(kwh[(days >= s) & (days < e)].sum())
        rows.append(
            {
                "start": s.date(),
                "end": (e - pd.Timedelta(days=1)).date(),
                "kwh": q / 1000.0,  # MWh values under a kWh label
                "units": "kWh",
                "amount": round(q * 0.11 + 50, 2),
            }
        )
    pd.DataFrame(rows).to_csv(tmp_path / "steam.csv", index=False)
    pd.DataFrame({"timestamp": days, "oat": T}).to_csv(tmp_path / "oat.csv", index=False)
    tar = {"tariff": {"name": "flat", "fixed_monthly": 50.0, "energy_rates": [[[None, 0.11]]]}}
    cfg = _cfg({"energy": "kwh", "scale_check": {"cost": "amount", **tar}})
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.findings[0].rule == "unit_scale" and res.findings[0].metrics["scale"] == 1000.0
    ev = {e["name"] for e in res.findings[0].metrics["unit_scale"]["evidence"]}
    assert "tariff" in ev
    urdb = {"name": "flat", "fixedchargefirstmeter": 50, "energyratestructure": [[{"rate": 0.11}]]}
    (tmp_path / "rate.json").write_text(json.dumps(urdb))
    cfg = _cfg(
        {"energy": "kwh", "scale_check": {"cost": "amount", "tariff": {"urdb_file": "rate.json"}}}
    )
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.findings[0].rule == "unit_scale"


def test_report_benchmark_eui_scale_finding(tmp_path):
    cfg = {"site": "Demo", "report": {"benchmark": {"site_eui": 55.0, "peer_median_eui": 52.9}}}
    assert "unit_scale" not in _rules(run_config(cfg, base_dir=str(tmp_path)))
    cfg["report"]["benchmark"] = {
        "site_eui": 58_000.0,
        "peer_median_eui": 52.9,
        "property_type": "office",
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    (f,) = [f for f in res.findings if f.rule == "unit_scale"]
    assert f.equip == "benchmark" and f.metrics["scale"] == 0.001
    cfg["report"]["benchmark"] = {"site_eui": "n/a", "peer_median_eui": 52.9}
    from camber.config import _benchmark_scale_finding

    assert _benchmark_scale_finding(cfg["report"]["benchmark"]) is None


def test_catalog_ingest_meter_warning():
    from camber.datasets._ingest import meter_scale_warning

    idx = pd.date_range("2016-01-01", "2016-12-31 23:00", freq="h")
    s = pd.Series(30.0 + (idx.hour > 8) * 20.0, index=idx)  # kWh per hour
    kw = {"label": "ds-x/b__electricity", "meter": "electricity", "area_ft2": 50_000.0}
    assert meter_scale_warning(s, property_type="Office", **kw) is None
    w = meter_scale_warning(s * 1000, property_type="Office", **kw)
    assert w.startswith("ds-x/b__electricity: unit scale:") and "x0.001" in w
    assert meter_scale_warning(s, label="x", meter="water") is None
    assert meter_scale_warning(s.iloc[:5], label="x", meter="electricity") is None
    assert (
        meter_scale_warning(s * 1000, label="x", meter="electricity", area_ft2=float("nan")) is None
    )


def test_hdd_helper_handles_gaps_and_timezones():
    st, en = _months(2)
    f = pd.DataFrame({"start": st, "end": en, "days": [31.0, 29.0]})
    idx = pd.date_range("2023-01-01", "2023-01-31 23:00", freq="h", tz="UTC")
    h = us._hdd_per_bill(f, pd.Series(45.0, index=idx))
    assert h[0] == pytest.approx(20 * 31) and math.isnan(h[1])
    assert np.isnan(us._hdd_per_bill(f, pd.Series([], dtype=float))).all()
