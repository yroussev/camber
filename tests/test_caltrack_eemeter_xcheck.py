"""Optional cross-check of the CalTRACK 2.0 methods against eemeter 2.10.11 (dev-only).

Skipped unless ``CAMBER_EEMETER_PYTHON`` names an interpreter in which eemeter 2.10 imports.
eemeter 2.x needs pandas 1.x and numpy 1.x, so it lives in its own environment and runs
``tests/eemeter_oracle.py`` on the synthetic cases this test writes; eemeter is never a CAMBER
dependency. See docs/MANDV.md, "Cross-checking against eemeter", for the environment pins.

CAMBER runs with ``eemeter_compat=True`` and must agree exactly on the selected model, its
balance points, parameters and adjusted R^2, and on every prediction both sides make. The one
expected difference is CT-H-6.3 (an hour of the week unseen in a segment): eemeter 2.10 predicts
from a ~0 coefficient there, CAMBER predicts NaN in both modes.
"""

from __future__ import annotations

import json
import os
import subprocess

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.eemeter_oracle

_PY = os.environ.get("CAMBER_EEMETER_PYTHON")
_ORACLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eemeter_oracle.py")


def _eemeter_ok() -> bool:
    if not _PY:
        return False
    try:
        out = subprocess.run(
            [_PY, "-c", "import eemeter; print(eemeter.get_version())"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.strip().startswith("2.10")


if not _eemeter_ok():
    pytest.skip(
        "eemeter 2.10 cross-check: set CAMBER_EEMETER_PYTHON to an interpreter with eemeter 2.10",
        allow_module_level=True,
    )

from camber.mandv.caltrack_daily import fit_caltrack_billing, fit_caltrack_daily  # noqa: E402
from camber.mandv.caltrack_hourly import fit_caltrack_hourly  # noqa: E402

_DAYS = pd.date_range("2017-01-01", periods=365, freq="D")
_HOURS = pd.date_range("2017-01-01", periods=365 * 24, freq="h")


def _temp_w():
    """Fixture W: T_d = 30 + (d mod 61), constant over the day's 24 hours."""
    T = 30.0 + (np.arange(365) % 61)
    return pd.Series(np.repeat(T, 24), index=_HOURS), T


def _temp_sine(seed=1):
    rng = np.random.default_rng(seed)
    doy = _HOURS.dayofyear.to_numpy()
    hr = _HOURS.hour.to_numpy()
    T = 55 - 25 * np.cos(2 * np.pi * doy / 365) + 5 * np.sin(2 * np.pi * hr / 24)
    return pd.Series(T + rng.normal(0, 2.0, len(T)), index=_HOURS)


def _write(d, name, case, meter, temp, predict_temp=None):
    meter.to_frame("value").to_csv(os.path.join(d, f"{name}_meter.csv"))
    temp.to_frame("t").to_csv(os.path.join(d, f"{name}_temp.csv"))
    case.update({"meter": f"{name}_meter.csv", "temp": f"{name}_temp.csv"})
    if predict_temp is not None:
        predict_temp.to_frame("t").to_csv(os.path.join(d, f"{name}_ptemp.csv"))
        case["predict_temp"] = f"{name}_ptemp.csv"
    with open(os.path.join(d, f"{name}.json"), "w") as fh:
        json.dump(case, fh)


def _daily_cases():
    tw, T = _temp_w()
    d = np.arange(365)
    hdd = lambda b: np.maximum(b - T, 0)  # noqa: E731
    cdd = lambda b: np.maximum(T - b, 0)  # noqa: E731
    ts = _temp_sine()
    Ts = ts.resample("D").mean().to_numpy()
    rng = np.random.default_rng(7)
    return {
        "d2_gas": ("gas", 10 + 2 * hdd(60), tw),
        "d3_elec": ("electricity", 20 + 1.5 * hdd(54) + 3 * cdd(72), tw),
        "d7_gas": ("gas", 10 + 2 * hdd(60) + 0.5 * np.sin(1.7 * d), tw),
        "d7_elec": ("electricity", 10 + 2 * hdd(60) + 0.5 * np.sin(1.7 * d), tw),
        "sine_elec": (
            "electricity",
            40
            + 1.2 * np.maximum(57 - Ts, 0)
            + 2.1 * np.maximum(Ts - 68, 0)
            + rng.normal(0, 3, 365),
            ts,
        ),
        "sine_gas": ("gas", 5 + 0.8 * np.maximum(61 - Ts, 0) + rng.normal(0, 1, 365), ts),
        "flat_elec": ("electricity", 30 + rng.normal(0, 1, 365), ts),
    }


def _run_oracle(d):
    out = subprocess.run([_PY, _ORACLE, str(d)], capture_output=True, text=True, timeout=1800)
    assert out.returncode == 0, out.stderr[-3000:]


def _read(d, name):
    with open(os.path.join(d, f"{name}.out.json")) as fh:
        return json.load(fh)


def _agree(camber_model, ee: dict, camber_pred: np.ndarray) -> float:
    assert camber_model.model_type == ee["model_type"]
    p = ee["params"]
    assert camber_model.heating_balance_point == p.get("heating_balance_point")
    assert camber_model.cooling_balance_point == p.get("cooling_balance_point")
    assert camber_model.intercept == pytest.approx(p["intercept"], rel=1e-9, abs=1e-9)
    assert camber_model.beta_hdd == pytest.approx(p.get("beta_hdd", 0.0), rel=1e-8, abs=1e-9)
    assert camber_model.beta_cdd == pytest.approx(p.get("beta_cdd", 0.0), rel=1e-8, abs=1e-9)
    assert camber_model.r_squared_adj == pytest.approx(ee["r_squared_adj"], rel=1e-9, abs=1e-12)
    ep = np.array([np.nan if v is None else v for v in ee["predicted"]], dtype=float)
    both = np.isfinite(ep) & np.isfinite(camber_pred)
    assert (np.isfinite(ep) == np.isfinite(camber_pred)).all()
    return float(np.max(np.abs(ep[both] - camber_pred[both]))) if both.any() else 0.0


def test_daily_and_billing_agree_with_eemeter(tmp_path, capsys):
    cases = _daily_cases()
    for name, (fuel, U, temp) in cases.items():
        meter = pd.Series(
            np.append(U, np.nan), index=_DAYS.append(pd.DatetimeIndex(["2018-01-01"]))
        )
        case = {
            "kind": "daily",
            "fuel": fuel,
            "predict_index": [str(x) for x in _DAYS[:60].append(_DAYS[60:61])],
            "n_predict": 60,
        }
        _write(tmp_path, name, case, meter, temp)
    # billing: calendar-month bills built from the daily usage of two of the daily cases
    for name in ("sine_elec", "sine_gas", "d7_gas"):
        fuel, U, temp = cases[name]
        daily = pd.Series(U, index=_DAYS)
        bills = daily.resample("MS").sum()
        starts = bills.index.append(pd.DatetimeIndex(["2018-01-01"]))
        meter = pd.Series(np.append(bills.to_numpy(), np.nan), index=starts)
        case = {
            "kind": "billing",
            "fuel": fuel,
            "predict_index": [str(x) for x in starts],
            "n_predict": 12,
        }
        _write(tmp_path, f"bill_{name}", case, meter, temp)
    _run_oracle(tmp_path)
    diffs = {}
    for name, (fuel, U, temp) in cases.items():
        m, _ = fit_caltrack_daily(pd.Series(U, index=_DAYS), temp, fuel=fuel, eemeter_compat=True)
        pred = m.predict(_DAYS[:60], temp).to_numpy()
        diffs[name] = _agree(m, _read(tmp_path, name), pred)
    for name in ("sine_elec", "sine_gas", "d7_gas"):
        fuel, U, temp = cases[name]
        daily = pd.Series(U, index=_DAYS)
        bills = daily.resample("MS").sum()
        frame = pd.DataFrame(
            {
                "start": bills.index,
                "end": bills.index.shift(1, freq="MS"),
                "energy": bills.to_numpy(),
            }
        )
        m, _ = fit_caltrack_billing(frame, temp, fuel=fuel, eemeter_compat=True)
        pred = m.predict(frame[["start", "end"]], temp).to_numpy()
        diffs[f"bill_{name}"] = _agree(m, _read(tmp_path, f"bill_{name}"), pred)
    with capsys.disabled():
        print("\nmax |CAMBER - eemeter| prediction per case:", json.dumps(diffs))
    assert max(diffs.values()) < 1e-6


def _hourly_case(drop_monday_9=False):
    idx = _HOURS
    doy = idx.dayofyear.to_numpy()
    hr = idx.hour.to_numpy()
    T = 55 - 25 * np.cos(2 * np.pi * doy / 365) + 5 * np.sin(2 * np.pi * hr / 24)
    rng = np.random.default_rng(0)
    occ = (idx.dayofweek < 5) & (hr >= 8) & (hr < 18)
    y = 5 + 10 * occ + 0.2 * np.maximum(T - 65, 0) + rng.normal(0, 0.1, len(idx))
    e = pd.Series(y, index=idx)
    if drop_monday_9:
        drop = (idx.month <= 3) & (idx.dayofweek == 0) & (hr == 9)
        e = e.where(~drop)
    return e, pd.Series(T, index=idx)


def test_hourly_agrees_with_eemeter(tmp_path, capsys):
    rep = pd.date_range("2018-01-01", periods=24 * 59, freq="h")
    doy = rep.dayofyear.to_numpy()
    hr = rep.hour.to_numpy()
    rT = pd.Series(
        55 - 25 * np.cos(2 * np.pi * doy / 365) + 5 * np.sin(2 * np.pi * hr / 24), index=rep
    )
    # eemeter needs the prediction temperatures on a calendar it can segment; predict on the
    # baseline's own January-February with the reporting temperatures' shape
    pidx = _HOURS[: 24 * 59]
    ptemp = pd.Series(rT.to_numpy(), index=pidx)
    for name, drop in (("h6", False), ("h7", True)):
        e, t = _hourly_case(drop)
        # eemeter reads the last meter row as the end of the final period: add that sentinel
        sentinel = pd.Series([np.nan], index=pd.DatetimeIndex(["2018-01-01"]))
        _write(
            tmp_path,
            name,
            {"kind": "hourly", "fuel": "electricity", "predict_index": [str(x) for x in pidx]},
            pd.concat([e.dropna(), sentinel]),
            t,
            ptemp,
        )
    _run_oracle(tmp_path)
    diffs = {}
    for name, drop in (("h6", False), ("h7", True)):
        e, t = _hourly_case(drop)
        m, _ = fit_caltrack_hourly(
            e.dropna(), t, eemeter_compat=True, intervention_start="2018-01-01"
        )
        ee = _read(tmp_path, name)
        for seg_name, es in ee["segments"].items():
            seg = m.segments[seg_name]
            assert [bool(v) for v in seg.occupancy == 1.0] == es["occupancy"], seg_name
            assert seg.endpoints == es["endpoints"], seg_name
            assert seg.num_parameters == es["n_params"], seg_name
        cp = m.predict(pidx, ptemp.to_numpy()).to_numpy()
        ep = np.array([np.nan if v is None else v for v in ee["predicted"]], dtype=float)
        both = np.isfinite(cp) & np.isfinite(ep)
        diffs[name] = float(np.max(np.abs(cp[both] - ep[both])))
        only_ee = np.isfinite(ep) & ~np.isfinite(cp)
        if drop:  # CT-H-6.3: February's unseen Monday 09:00 hours are NaN in CAMBER only
            mon9 = (pidx.dayofweek == 0) & (pidx.hour == 9) & (pidx.month == 2)
            assert (only_ee == mon9).all()
        else:
            assert not only_ee.any()
    with capsys.disabled():
        print("\nmax |CAMBER - eemeter| hourly prediction per case:", json.dumps(diffs))
    assert max(diffs.values()) < 1e-6
