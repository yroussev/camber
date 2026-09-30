"""examples/bts_suggester (0.96, #45): the scoring pipeline on synthetic profiles (no BTS data)."""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples", "bts_suggester"))

import evaluate as ev  # noqa: E402

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
    assert table.startswith("| method | names | top-1 %") and "A top-1" in table
