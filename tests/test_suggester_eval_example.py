"""examples/suggester_eval (0.96, #45): the scoring pipelines on synthetic data (no real data)."""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples", "suggester_eval"))

import bts as ev  # noqa: E402
import messy_names  # noqa: E402
import real_names  # noqa: E402

from camber.mapping_timeseries import profile_series  # noqa: E402

IDX = pd.date_range("2024-01-01", periods=96 * 21, freq="15min")
HOUR = IDX.hour.to_numpy()
RNG = np.random.default_rng(3)


def _rec(site, role, cls, series, i):
    return {
        "site": site,
        "stream_id": f"{i:08x}_0000_4000_8000_000000000000",
        "brick_class": cls,
        "role": role,
        "equip_class": "ZONE",
        "profile": profile_series(pd.Series(series, index=IDX)).as_dict(),
    }


def _records():
    out = []
    i = 0
    for site in "ABC":
        for _ in range(3):
            i += 1
            zone = 22 + 0.5 * np.sin(HOUR / 24 * 2 * np.pi) + RNG.normal(0, 0.1, len(IDX))
            out.append(_rec(site, "space_temp", "Zone_Air_Temperature_Sensor", zone, i))
            i += 1
            status = ((HOUR >= 7) & (HOUR < 18)).astype(float)
            out.append(_rec(site, "pump_status", "Pump_Status", status, i))
    return out


def test_evaluate_scores_every_method_and_summarises(tmp_path):
    recs = _records()
    res = ev.evaluate(recs, ["A", "B", "C"])
    assert set(res) == {f"{m}/{n}" for m, n in ev.METHODS}
    lex = res["lexical/named"]
    assert lex["n"] == 18 and lex["top1"] == 100.0  # the Brick class text names both roles
    assert res["lexical/anonymised"]["top1"] == 0.0  # a UUID says nothing
    ts = res["timeseries_templates/none"]
    assert ts["by_role"]["space_temp"]["top1"] == 100.0 and set(ts["by_site"]) == {"A", "B", "C"}
    assert res["timeseries_fitted/none"]["top1"] == 100.0  # two clean classes, other buildings
    assert res["combined_templates/named"]["top1"] >= lex["top1"]
    assert ts["macro_top1"] is not None and isinstance(ts["confusions"], list)
    table = ev.table(res)
    assert "| method | names | top-1 %" in table and "A top-1" in table
    assert "Brick-class labels used as names (upper bound, not real-world naming)" in table


# ------------------------------------------------------------- real_names.py (synthetic points)


def _named(site, name, role, series, i):
    return {
        "site": site,
        "name": name,
        "equip": f"EQ{i}",
        "run": "r",
        "role": role,
        "label_source": "CAMBER mapping",
        "profile": profile_series(pd.Series(series, index=IDX)).as_dict(),
    }


def _named_records():
    zone = 22 + 0.5 * np.sin(HOUR / 24 * 2 * np.pi)
    status = ((HOUR >= 7) & (HOUR < 18)).astype(float)
    return [
        _named("irish-ahu", "ZoneTemp_1", "space_temp", zone, 1),
        _named("irish-ahu", "AI_0417", "space_temp", zone + 0.2, 2),  # a name that says nothing
        _named("robod", "SF_Status", "supply_fan_status", status, 3),
        _named("lbnl-sdahu", "SA_TEMP", "supply_air_temp", 13 + 0 * zone, 4),
    ]


def test_real_names_reports_real_and_simulated_apart():
    res = real_names.evaluate(_named_records())
    assert set(res) == {"real", "real, excluding in-sample names", "simulated"}
    # irish-ahu's names are in-sample for the tokenizer: left out of the reference pool
    assert set(res["real, excluding in-sample names"]["methods"]["lexical"]["by_site"]) == {"robod"}
    real = res["real"]["methods"]
    assert set(real) == set(real_names.METHODS)
    assert real["lexical"]["n"] == 3 and set(real["lexical"]["by_site"]) == {"irish-ahu", "robod"}
    assert res["simulated"]["methods"]["lexical"]["n"] == 1
    # the data places the anonymous zone temperature the name cannot
    helped = {row[1] for row in res["real"]["changes"]["helped"]}
    assert "AI_0417" in helped
    table = real_names.table(res)
    assert "**pooled, real**" in table and "**pooled, simulated**" in table


def test_real_names_published_name_and_run_order():
    run = {"members": {"co2": "filleddata/co2_room_1.csv"}}
    assert real_names._published_name(run, "co2") == "co2_room_1"
    assert real_names._published_name({}, "DaTemp") == "DaTemp"

    class _Entry:
        def runs(self, subset):
            return [{"id": "fault", "label": "bias"}, {"id": "base", "label": ""}]

    assert [r["id"] for r in real_names.select_runs(_Entry(), "full")] == ["base", "fault"]


# ------------------------------------------------------------ messy_names.py (synthetic names)


def test_messy_names_are_seeded_and_scored_per_style():
    recs = _records()
    a = messy_names.messy_names(recs, seed=7)
    assert a == messy_names.messy_names(recs, seed=7)  # deterministic for a seed
    assert set(a) == set(messy_names.STYLES) and all(len(v) == len(recs) for v in a.values())
    # a water point never carries an air-stream initial
    rng = messy_names.random.Random(1)
    for _ in range(20):
        name = messy_names.style_dash_space(rng, "Chilled_Water_Return_Temperature_Sensor", "AHU")
        assert "-RA-" not in name
    res = messy_names.evaluate(recs, seed=7)
    for r in res.values():
        assert r["lexical"]["n"] == len(recs) and r["combined"]["top1"] is not None
    assert messy_names.table(res).startswith("| naming style (synthetic)")
