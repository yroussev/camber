"""SYNTHETIC check: the role suggester on vendor-style names generated from BTS Brick classes.

BTS (``bts.py``) publishes no point names, and the Brick class text is effectively the label. This
script turns each labelled BTS point's Brick class into vendor-style point names -- abbreviations,
an equipment prefix and number, separators and site conventions -- with a seeded generator and
five naming styles, then scores the name-only suggester and the name + data suggester
(``use_timeseries=True``) on them. Every point is named once per style.

**These names are synthetic.** They test tolerance to abbreviation and convention, not real-world
naming; the real-name figures come from ``real_names.py``. The abbreviation tables below were
written once from common BAS practice and were not tuned to the scores.

    python examples/suggester_eval/bts.py            # builds the BTS profile cache first
    python examples/suggester_eval/messy_names.py
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import bts as bts_eval  # noqa: E402

from camber.mapping_assist import FeatureSuggester  # noqa: E402
from camber.mapping_timeseries import SeriesProfile  # noqa: E402

DEFAULT_PROFILES = os.path.join(bts_eval.DEFAULT_OUT, "profiles.json")
SEED = 45

#: Brick class word -> (short forms, long forms); "" drops the word
ABBREV = {
    "zone": (["Zn", "Z", "Rm"], ["Zone", "Room"]),
    "air": ([""], ["", "Air"]),
    "temperature": (["T", "Tmp", "TEMP"], ["Temp", "Temperature"]),
    "supply": (["SA", "S", "Sup"], ["Supply", "Sply"]),
    "discharge": (["DA", "D", "Dis"], ["Disch", "Discharge"]),
    "return": (["RA", "R", "Ret"], ["Return", "Rtn"]),
    "outside": (["OA", "O", "OSA"], ["Outside", "Outdoor"]),
    "mixed": (["MA", "M", "Mx"], ["Mixed", "Mix"]),
    "setpoint": (["SP", "Stpt", "SPT"], ["Setpt", "Setpoint"]),
    "sensor": ([""], ["", "Sensor", "Sen"]),
    "status": (["Sts", "S", "ST"], ["Status", "Stat"]),
    "on": ([""], [""]),
    "off": (["OnOff", "SS", "Run"], ["OnOff", "Run", "StartStop"]),
    "valve": (["V", "Vlv", "VLV"], ["Valve", "Vlv"]),
    "damper": (["Dmp", "Dpr", "D"], ["Damper", "Dmpr"]),
    "position": (["Pos", "", "%"], ["Position", "Pos"]),
    "flow": (["F", "Flw", "CFM"], ["Flow", "Airflow"]),
    "pressure": (["P", "Pr", "PRS"], ["Press", "Pressure"]),
    "static": (["S", "St", "Stat"], ["Static", "Stat"]),
    "differential": (["D", "DP", "d"], ["Diff", "Differential"]),
    "filter": (["Flt", "Filt", "F"], ["Filter", "Filt"]),
    "humidity": (["RH", "H", "Hum"], ["Humidity", "Humid"]),
    "co2": (["CO2"], ["CO2"]),
    "chilled": (["CHW", "ChW", "CW"], ["Chilled", "ChW"]),
    "hot": (["HW", "HHW", "H"], ["Hot", "HW"]),
    "water": ([""], ["", "Water"]),
    "wet": (["WB", "Wb"], ["Wet"]),
    "bulb": ([""], ["Bulb"]),
    "pump": (["P", "Pmp", "PU"], ["Pump", "Pmp"]),
}
#: in a water point, the air-stream initials (SA, RA) would name the wrong medium
WATER_FORMS = {
    "supply": (["S", "Sup", "ST"], ["Supply", "Sply"]),
    "return": (["R", "Ret", "RT"], ["Return", "Rtn"]),
}
#: CAMBER equipment class -> the prefixes a site might use
EQUIP_PREFIX = {
    "AHU": ["AHU", "AH", "ACU", "RTU"],
    "FCU": ["FCU", "FC", "FanCoil"],
    "VAV": ["VAV", "VAVR", "TU"],
    "ZONE": ["VAV", "Z", "RM", "TU"],
    "PUMP": ["CHWP", "HWP", "P", "PMP"],
    "CHW_PLANT": ["CHWPLANT", "CP", "Plant"],
    "HW_PLANT": ["HWPLANT", "BLR", "Plant"],
    "CHILLER": ["CH", "CHLR", "Chiller"],
    "SITE": ["BLDG", "SITE", "B"],
    "WEATHER": ["WX", "WEATHER", "Site"],
}


def _words(rng: random.Random, brick_class: str, long: bool) -> list:
    out = []
    words = brick_class.lower().split("_")
    table = {**ABBREV, **WATER_FORMS} if "water" in words else ABBREV
    for w in words:
        short, full = table.get(w, ([w[:3].title()], [w.title()]))
        form = rng.choice(full if long else short)
        if form:
            out.append(form)
    return out


def _prefix(rng: random.Random, equip_class: str) -> str:
    return rng.choice(EQUIP_PREFIX.get(equip_class, [equip_class or "EQ"]))


def style_upper_compact(rng, cls, eq):  # AHU1_SAT
    return f"{_prefix(rng, eq)}{rng.randint(1, 12)}_" + "".join(_words(rng, cls, False)).upper()


def style_dash_space(rng, cls, eq):  # VAV-2-14 DA-T
    return (
        f"{_prefix(rng, eq)}-{rng.randint(1, 6)}-{rng.randint(1, 40)} "
        + "-".join(_words(rng, cls, False)).upper()
    )


def style_dotted_camel(rng, cls, eq):  # B2.L3.FCU07.RmTmp
    return (
        f"B{rng.randint(1, 4)}.L{rng.randint(0, 9)}.{_prefix(rng, eq)}{rng.randint(1, 30):02d}."
        + "".join(_words(rng, cls, rng.random() < 0.5))
    )


def style_snake_lower(rng, cls, eq):  # ahu_03_supply_temp
    return f"{_prefix(rng, eq).lower()}_{rng.randint(1, 20):02d}_" + "_".join(
        w.lower() for w in _words(rng, cls, True)
    )


def style_bacnet_object(rng, cls, eq):  # 201-AHU3:SA-TMP (object instance, site code)
    return f"{rng.randint(100, 999)}-{_prefix(rng, eq)}{rng.randint(1, 9)}:" + "-".join(
        _words(rng, cls, False)
    )


STYLES = {
    "UPPER_COMPACT (AHU1_SAT)": style_upper_compact,
    "dash-space (VAV-2-14 DA-T)": style_dash_space,
    "dotted.Camel (B2.L3.FCU07.RmTmp)": style_dotted_camel,
    "snake_lower (ahu_03_supply_temp)": style_snake_lower,
    "object-id (201-AHU3:SA-TMP)": style_bacnet_object,
}


def messy_names(records, seed: int = SEED) -> dict:
    """``{style: [name per record]}`` from a seeded generator (deterministic for a seed)."""
    out = {}
    for i, (style, fn) in enumerate(STYLES.items()):
        rng = random.Random(seed * 1000 + i)
        out[style] = [fn(rng, r["brick_class"], r.get("equip_class", "")) for r in records]
    return out


def evaluate(records, seed: int = SEED) -> dict:
    """Per style: the name-only and name + data summaries, and a few example names."""
    names = messy_names(records, seed)
    profiles = [SeriesProfile(**r["profile"]) for r in records]
    lex, comb = FeatureSuggester(), FeatureSuggester(use_timeseries=True)
    out = {}
    for style, ns in names.items():
        res = {}
        for method in ("lexical", "combined"):
            pairs = []
            for rec, name, prof in zip(records, ns, profiles):
                if method == "lexical":
                    sugg = lex.suggest(name, k=3)
                else:
                    sugg = comb.suggest(name, profile=prof, k=3)
                pairs.append((rec, [s.role for s in sugg]))
            res[method] = bts_eval.summarise(pairs)
        res["examples"] = list(dict.fromkeys(ns))[:6]
        out[style] = res
    return out


def table(results: dict) -> str:
    lines = [
        "| naming style (synthetic) | points | name only top-1 / top-3 % | "
        "name + data top-1 / top-3 % |",
        "|---|---|---|---|",
    ]
    for style, r in results.items():
        a, b = r["lexical"], r["combined"]
        lines.append(
            f"| {style} | {a['n']} | {a['top1']} / {a['top3']} | {b['top1']} / {b['top3']} |"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--profiles", default=DEFAULT_PROFILES, help="the BTS profile cache (bts.py)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(DEFAULT_PROFILES), "messy.json"))
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    with open(args.profiles, encoding="utf-8") as fh:
        records = json.load(fh)
    results = evaluate(records, args.seed)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print("SYNTHETIC names generated from BTS Brick classes -- not real-world naming\n")
    print(table(results))
    for style, r in results.items():
        print(f"\n{style}: e.g. {', '.join(r['examples'][:4])}")
        for t, p, c in r["lexical"]["confusions"][:5]:
            print(f"  name only: {t} -> {p}: {c}")
    print(f"\nresults: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
