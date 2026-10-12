"""Run eemeter 2.10 on the CalTRACK cross-check cases (dev-only; not part of the test suite).

``tests/test_caltrack_eemeter_xcheck.py`` writes synthetic cases to a directory and runs this
script under a separate interpreter that has eemeter 2.10.11 (it cannot share CAMBER's
environment: eemeter 2.x needs pandas 1.x and numpy 1.x)::

    python tests/eemeter_oracle.py <case-dir>

Each ``<case-dir>/<name>.json`` names its inputs (CSV files in the same directory, naive
timestamps read as UTC) and the result is written to ``<case-dir>/<name>.out.json``. See
docs/MANDV.md, "Cross-checking against eemeter", for the environment.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def _series(path):
    s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0].astype(float)
    s.index = pd.DatetimeIndex(s.index).tz_localize("UTC")
    return s


def _hourly_temp(path):
    t = _series(path)
    return t.asfreq("H")


def _idx(values):
    return pd.DatetimeIndex(pd.to_datetime(values)).tz_localize("UTC")


def _floats(a):
    return [None if not np.isfinite(v) else float(v) for v in np.asarray(a, dtype=float)]


def _usage_per_day(case, d):
    import eemeter

    temp = _hourly_temp(os.path.join(d, case["temp"]))
    meter = _series(os.path.join(d, case["meter"])).to_frame("value")
    is_elec = case["fuel"] == "electricity"
    if case["kind"] == "daily":
        dm = eemeter.create_caltrack_daily_design_matrix(meter, temp)
        r = eemeter.fit_caltrack_usage_per_day_model(dm, fit_cdd=is_elec)
    else:
        dm = eemeter.create_caltrack_billing_design_matrix(meter, temp)
        r = eemeter.fit_caltrack_usage_per_day_model(
            dm, fit_cdd=is_elec, use_billing_presets=True, weights_col="n_days_kept"
        )
    out = {"status": r.status, "model_type": None, "params": {}, "r_squared_adj": None}
    if r.model is not None:
        out["model_type"] = r.model.model_type
        out["params"] = {k: float(v) for k, v in r.model.model_params.items()}
        out["r_squared_adj"] = float(r.model.r_squared_adj)
        out["n_candidates"] = len(r.candidates)
        p = r.model.predict(_idx(case["predict_index"]), temp).result["predicted_usage"]
        out["predicted"] = _floats(p.to_numpy()[: case["n_predict"]])
    return out


def _hourly(case, d):
    import eemeter

    temp = _hourly_temp(os.path.join(d, case["temp"]))
    meter = _series(os.path.join(d, case["meter"])).to_frame("value")
    meter = meter.asfreq("H")
    pdm = eemeter.create_caltrack_hourly_preliminary_design_matrix(meter, temp)
    seg = eemeter.segment_time_series(pdm.index, "three_month_weighted")
    occ = eemeter.estimate_hour_of_week_occupancy(pdm, segmentation=seg)
    bins = eemeter.fit_temperature_bins(pdm, segmentation=seg)
    sdm = eemeter.create_caltrack_hourly_segmented_design_matrices(pdm, seg, occ, bins)
    model = eemeter.fit_caltrack_hourly_model(sdm, occ, bins)
    idx = _idx(case["predict_index"])
    ptemp = _hourly_temp(os.path.join(d, case["predict_temp"]))
    res = model.predict(idx, ptemp)
    pred = res.result["predicted_usage"].reindex(idx)
    segments = {}
    for m in model.model.segment_models:
        name = m.segment_name
        kept = list(bins.index[bins[name].astype(bool)])
        segments[name] = {
            "occupied_hours": int(occ[name].astype(bool).sum()),
            "occupancy": [bool(v) for v in occ[name].astype(bool).to_numpy()],
            "endpoints": [float(e) for e in kept],
            "n_params": len(m.model_params) if m.model_params is not None else 0,
        }
    return {"predicted": _floats(pred.to_numpy()), "segments": segments}


def main(d):
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        if path.endswith(".out.json"):
            continue
        with open(path) as fh:
            case = json.load(fh)
        out = _hourly(case, d) if case["kind"] == "hourly" else _usage_per_day(case, d)
        with open(path[: -len(".json")] + ".out.json", "w") as fh:
            json.dump(out, fh)


if __name__ == "__main__":
    main(sys.argv[1])
