"""The change-point + driver model form on the config ``mv`` path (``mv[].model: "cp_driver"``).

An occupancy-driven building fails validity with the temperature-only form; with a weekday
driver it passes and the saving is recovered. The default form is unchanged.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.config import run_config, run_mv_config  # noqa: E402
from camber.mandv import _mvform  # noqa: E402
from camber.mandv.rebaseline import fit_frame_sha256  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402

SAVING = 0.10
HOLIDAY = "2016-07-04"


def _daily(seed=4, end="2018-12-31"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-01", end, freq="D")
    doy = idx.dayofyear.to_numpy()
    oat = 55 - 25 * np.cos((doy - 15) / 365 * 2 * np.pi) + rng.normal(0, 4, len(idx))
    occ = ((idx.dayofweek < 5) & (idx != pd.Timestamp(HOLIDAY))).astype(float)
    e = 300 + 400 * occ + 12 * np.maximum(0, oat - 60) + rng.normal(0, 15, len(idx))
    e = np.where(idx >= "2017-01-01", e * (1 - SAVING), e)
    return pd.DataFrame({"oat": oat, "energy": e, "occ": occ}, index=idx)


def _write(tmp_path, entry: dict, *, end="2018-12-31"):
    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("Occupied site", facility_id="f1", reason="test", activate=True)
    d = _daily(end=end)
    idx = pd.date_range(d.index[0], d.index[-1] + pd.Timedelta(hours=23), freq="1h")
    frame = pd.DataFrame(
        {
            Role.POWER: (d["energy"] / 24.0).reindex(idx, method="ffill").to_numpy(),
            Role.OAT: d["oat"].reindex(idx, method="ffill").to_numpy(),
            Role.OCCUPANCY: d["occ"].reindex(idx, method="ffill").to_numpy(),
        },
        index=idx,
    )
    pf.store.write_role_frame(frame, facility_id="f1", equip="meter", equip_class="M")
    cfg = {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "f1"},
        "equipment": [{"class": "M"}],
        "mv": [
            {
                "class": "M",
                "role": "power",
                "period": ["2016-01-01", "2016-12-31"],
                "reporting_period": ["2017-01-01", "2017-12-31"],
                "method": "forecast",
                **entry,
            }
        ],
    }
    path = str(tmp_path / "config.json")
    open(path, "w").write(json.dumps(cfg))
    return cfg, path, ws


def _by_rule(findings):
    out: dict = {}
    for f in findings:
        out.setdefault(f.rule, []).append(f)
    return out


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "entry,msg",
    [
        ({"model": "gam"}, "mv.model must be one of"),
        ({"drivers": ["weekday"]}, 'needs mv.model "cp_driver"'),
        ({"model": "cp_driver"}, "needs drivers"),
        ({"model": "cp_driver", "drivers": ["weekdays"]}, "neither weekday nor occupied_day"),
        ({"model": "cp_driver", "drivers": ["oat"]}, "already the model's first term"),
        ({"model": "cp_driver", "drivers": ["weekday", "weekday"]}, "listed twice"),
        ({"model": "cp_driver", "drivers": [3]}, "each driver is a name"),
        ({"model": "cp_driver", "drivers": ["weekday"], "method": "auto"}, "temperature-only"),
        (
            {
                "model": "cp_driver",
                "drivers": ["weekday"],
                "method": "standard_conditions",
                "normal_year": [50.0] * 365,
            },
            "standard driver values",
        ),  # fmt: skip
        ({"model": "cp_driver", "drivers": ["weekday"], "holidays": [HOLIDAY]}, "occupied_day"),
        (
            {"model": "cp_driver", "drivers": ["occupied_day"], "occupied_weekdays": ["funday"]},
            "unknown day",
        ),
        ({"model": "cp_driver", "drivers": ["occupied_day"], "holidays": ["nope"]}, "not a date"),
    ],
)
def test_a_bad_model_form_is_a_config_error(tmp_path, entry, msg):
    cfg, _path, _ws = _write(tmp_path, entry, end="2017-12-31")
    with pytest.raises(ValueError, match=msg):
        run_config(cfg)


def test_the_default_form_is_unchanged(tmp_path):
    cfg, _path, _ws = _write(tmp_path, {}, end="2017-12-31")
    f = _by_rule(run_config(cfg).findings)
    (b,) = f["mv_baseline"]
    assert "drivers" not in b.metrics and "model_form" not in b.metrics
    # an occupancy-driven building: the temperature-only form fails daily G14 acceptance
    assert b.metrics["accept"] is False
    d = _daily(end="2017-12-31")[["oat", "energy"]]
    assert fit_frame_sha256(_mvform.add_drivers(d, {})) == fit_frame_sha256(d)


# --------------------------------------------------------------------------- the form at work


def test_weekday_driver_makes_the_baseline_valid_and_recovers_the_saving(tmp_path):
    cfg, _path, _ws = _write(
        tmp_path, {"model": "cp_driver", "drivers": ["weekday"], "validity": "both"},
        end="2017-12-31",
    )  # fmt: skip
    f = _by_rule(run_config(cfg).findings)
    (b,) = f["mv_baseline"]
    assert b.metrics["model_form"] == "cp_driver" and b.metrics["drivers"] == ["weekday"]
    assert b.metrics["accept"] is True and b.metrics["cv_rmse"] < 0.10
    assert b.metrics["driver_coef"][0] == pytest.approx(400 * 365 / 366, rel=0.1)
    assert "+ weekday baseline" in b.summary
    (s,) = f["mv_savings"]
    assert s.metrics["sep_valid"] is True
    assert s.metrics["savings_pct"] == pytest.approx(SAVING, abs=0.02)
    assert s.metrics["abs_uncertainty"] > 0


def test_occupied_day_with_holidays_and_a_mapped_numeric_role(tmp_path):
    base = {"model": "cp_driver", "method": "backcast"}
    cfg, _p, _w = _write(
        tmp_path / "a", {**base, "drivers": ["occupied_day"], "holidays": [HOLIDAY]},
        end="2017-12-31",
    )  # fmt: skip
    (s,) = _by_rule(run_config(cfg).findings)["mv_savings"]
    assert s.metrics["method"] == "backcast" and s.metrics["reporting_drivers"] == ["occupied_day"]
    assert s.metrics["savings_pct"] == pytest.approx(SAVING, abs=0.02)
    cfg, _p, _w = _write(tmp_path / "b", {**base, "drivers": ["occupancy"]}, end="2017-12-31")
    f = _by_rule(run_config(cfg).findings)
    assert f["mv_baseline"][0].metrics["drivers"] == ["occupancy"]
    assert f["mv_savings"][0].metrics["savings_pct"] == pytest.approx(SAVING, abs=0.02)
    # a numeric role that is not mapped declines the meter, naming the role
    cfg, _p, _w = _write(
        tmp_path / "c", {**base, "drivers": ["space_temp"]}, end="2017-12-31"
    )  # fmt: skip
    f = _by_rule(run_config(cfg).findings)
    assert "space_temp" in f["mv_baseline"][0].metrics["declined_reason"]


def test_chaining_and_an_indicator_ledger_use_the_drivers(tmp_path):
    entry = {
        "model": "cp_driver",
        "drivers": ["weekday"],
        "method": "chaining",
        "period": ["2016-01-01", "2016-12-31"],
        "intermediate_period": ["2017-01-01", "2017-12-31"],
        "reporting_period": ["2018-01-01", "2018-12-31"],
        "adjustments": [
            {"kind": "nra", "method": "indicator", "start": "2018-06-01", "reason": "new load",
             "fit_period": "reporting"}
        ],
    }  # fmt: skip
    cfg, _p, _w = _write(tmp_path, entry)
    (s,) = _by_rule(run_config(cfg).findings)["mv_savings"]
    assert s.metrics["method"] == "chaining" and not s.metrics["declined"]
    assert s.metrics.get("adjusted") is True, s.metrics.get("adjustments_refused")
    # backcast + a reporting-period indicator: the reporting model is refitted with it
    entry.update(method="backcast", reporting_period=["2017-01-01", "2017-12-31"])
    entry["adjustments"][0]["start"] = "2017-06-01"
    entry.pop("intermediate_period")
    cfg, _p, _w = _write(tmp_path / "bc", entry, end="2017-12-31")
    (s,) = _by_rule(run_config(cfg).findings)["mv_savings"]
    assert s.metrics.get("adjusted") is True, s.metrics.get("adjustments_refused")
    assert any("refitted with the indicator" in c for c in s.caveats)


def test_the_versioned_path_freezes_and_measures_the_driver_model(tmp_path, capsys):
    cfg, path, ws = _write(tmp_path, {"model": "cp_driver", "drivers": ["weekday"]})
    assert main(["mv", "freeze", path, "--reason", "init", "--by", "ana", "--apply"]) == 0
    capsys.readouterr()
    assert main(["mv", "propose", path]) == 0
    assert "ranks temperature-only models" in capsys.readouterr().out
    fs = _by_rule(run_mv_config(cfg, base_dir=os.path.dirname(path)))
    (b,) = fs["mv_baseline"]
    assert b.metrics["baseline_version"] == "v1" and b.metrics["drivers"] == ["weekday"]
    assert b.metrics["baseline_data_changed"] is False  # the sha covers the driver columns
    (s,) = fs["mv_savings"]
    assert s.metrics["savings_pct"] == pytest.approx(SAVING, abs=0.02)
    out = str(tmp_path / "r.html")
    assert (
        main(
            ["mv", "report", path, "--out", out, "--json", str(tmp_path / "r.json"), "--no-charts"]
        )
        == 0
    )
    rep = json.load(open(tmp_path / "r.json"))
    assert rep["meters"] and rep["meters"][0]["links"]
    # the frozen model reads its own drivers: an entry that drops them declines, naming them
    cfg["mv"][0].pop("drivers")
    cfg["mv"][0].pop("model")
    fs = _by_rule(run_mv_config(cfg, base_dir=os.path.dirname(path)))
    assert "weekday" in fs["mv_baseline"][0].metrics["declined_reason"]


def test_design_rows_and_n_params():
    d = _mvform.add_drivers(
        _daily(end="2016-12-31"), {"model": "cp_driver", "drivers": ["weekday"]}
    )
    assert _mvform.driver_columns(d) == ["drv:weekday"]
    m = _mvform.fit(d)
    X = _mvform.design_rows(d, m)
    assert X.shape == (len(d), 2) and _mvform.n_params(m) == m.p
    m0 = _mvform.fit(d[["oat", "energy"]])
    assert _mvform.design_rows(d, m0).ndim == 1 and _mvform.metrics(m0) == {}
    with pytest.raises(ValueError, match="needs driver"):
        _mvform.design_rows(d[["oat", "energy"]], m)
