"""SEP chaining on real meters: two university buildings in Valladolid, Spain, 2016-2019 (#50).

The published real-data chaining case of issue #50 (carried over from #49 / 21e, where the planned
``lbnl-b59`` chain turned out to need hand corrections). Data: Mariano, D. (2024).
*Building Energy Consumption Data*, Mendeley Data, V2, doi:10.17632/mzkyh37mtr.2 (CC BY 4.0) --
the catalog entry ``valladolid-uva``. Two buildings, hourly whole-building electricity (kWh per
hour) 2016-2020, plus daily NASA POWER weather repeated on every hour. The buildings are described
in Mariano-Hernandez et al., *Energy Science & Engineering* 10:4694-4707 (2022),
doi:10.1002/ese3.1298, Table 2: Building 1 (Faculty of Science, offices and laboratories, stable
consumption) and Building 2 (Faculty of Economics, offices and classrooms, "replacement of
low-efficiency equipment for high-efficiency equipment and the incorporation of renewable energy").
The files are ``db_building_A.csv`` / ``db_building_B.csv``; the catalog's ``building-labels``
data issue records why A is read as Building 1 and B as Building 2.

**The analysis, declared before any saving was looked at** (the method-shopping guard):

* **Clock.** Timestamps are local Spanish time, labelled at the *end* of each hour (the spring
  03:00 label is missing and the autumn 03:00 label repeats); every reading is moved to the start
  of its hour before days are formed.
* **Whole days.** A day enters with at least 23 readings and no missing value (as the BDG2
  benchmarks do).
* **Working days only.** The publisher's ``HOLIDAY`` flag marks weekends, public holidays and the
  academic breaks (all of July and August, Christmas, Easter) in 2016-2019. A university building
  on a non-working day is a different load, so the models are fitted on working days
  (Monday-Friday with ``HOLIDAY`` 0 on the majority of the day's hours). The savings are therefore
  **working-day savings**; non-working days are not modelled.
* **Monthly rows weighted by days.** Daily working-day energy against temperature leaves too much
  schedule scatter (Building 2 fails SEP validity on R2 in every year). Each month's
  working days are therefore one row: mean working-day energy (kWh/day) against the mean OAT of
  the same days, weighted by the number of working days (the 0.92 days-weighted fits, #64). A
  month with fewer than 5 working days (August) is dropped.
* **Weather.** Hourly OAT from CAMBER's weather source (``oat_reference_auto``: the nearest ISD
  station, gap-filled from other stations and then NASA POWER / Open-Meteo), daily means on the
  same days. The file's own daily NASA POWER ``T2M`` is run as a sensitivity.
* **Periods.** Baseline 2016, reporting 2019, and the intermediate periods between them. **2020 is
  excluded**: Spain's COVID-19 state of alarm closed the university from 14 March 2020, and the
  publisher's 2020 ``HOLIDAY`` flag no longer marks weekends or the academic breaks.
* **Methods.** :func:`camber.mandv.methods.select_method` (forecast, backcast, chaining, in SEP's
  order; it only proposes) and :func:`camber.mandv.methods.chained_savings` with each calendar-year
  intermediate (2017, 2018). Every band is at 90%.

Run (the data are fetched, never redistributed)::

    camber datasets fetch valladolid-uva            # or download the two CSVs from Mendeley
    python examples/valladolid/chaining.py --data-dir <dir with db_building_A.csv> \
        --weather-cache <dir> [--json out.json]

The weather is cached under ``--weather-cache``; ``--offline`` refuses the network. Not gated: the
numbers are described in docs/VALIDATION.md for maintainer sign-off.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from camber.mandv.methods import chained_savings, select_method  # noqa: E402
from camber.mandv.models import N_PARAMS, fit_model  # noqa: E402
from camber.mandv.stats import (  # noqa: E402
    fit_stats,
    logical_signs,
    model_regression_tests,
    sep_validity,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "valladolid")
# the two buildings' coordinates (Mariano-Hernandez et al. 2022, Table 2), midpoint
LAT, LON = (41.663411 + 41.658586) / 2, (-4.705539 - 4.710667) / 2
TZ = "Europe/Madrid"
YEARS = (2016, 2017, 2018, 2019)
BASELINE = ("2016-01-01", "2016-12-31")
REPORTING = ("2019-01-01", "2019-12-31")
INTERMEDIATES = {"2017": ("2017-01-01", "2017-12-31"), "2018": ("2018-01-01", "2018-12-31")}
KINDS = ("2P", "3PC", "3PH", "4P", "5P")
MIN_WORKDAYS = 5
BUILDINGS = {"A": "Building 1 (Faculty of Science)", "B": "Building 2 (Faculty of Economics)"}


def hourly(path: str) -> pd.DataFrame:
    """The published hourly file, each reading moved to the start of its (hour-ending) interval."""
    d = pd.read_csv(path)
    d.index = pd.to_datetime(d["DATE"], format="%m/%d/%Y %H:%M") - pd.Timedelta(hours=1)
    return d


def daily(d: pd.DataFrame, oat: pd.Series) -> pd.DataFrame:
    """Whole days: energy (kWh/day), working-day flag, station OAT and the file's T2M (degF)."""
    e = d["ENERGY"]
    day = e.index.normalize()
    out = pd.DataFrame(
        {
            "energy": e.groupby(day).sum(min_count=1),
            "n": e.groupby(day).count(),
            "missing": e.isna().groupby(day).sum(),
            "holiday": d["HOLIDAY"].groupby(day).median().round(),
            "t2m_power": d["T2M"].groupby(day).first() * 9.0 / 5.0 + 32.0,
        }
    )
    out.loc[(out["n"] < 23) | (out["missing"] > 0), "energy"] = np.nan
    o = oat.copy()
    o.index = o.index - pd.Timedelta(hours=1)  # the same hour-ending convention as the meters
    og = o.groupby(o.index.normalize()).agg(["mean", "count"])
    out["oat"] = og["mean"].where(og["count"] >= 20)
    out["workday"] = (out.index.dayofweek < 5) & (out["holiday"] == 0)
    return out


def monthly(day: pd.DataFrame, driver: str = "oat") -> pd.DataFrame:
    """One row per month of working days: mean kWh/day, mean driver, and the day count."""
    w = day[day["workday"]].dropna(subset=["energy", driver])
    w = w[(w.index.year >= YEARS[0]) & (w.index.year <= YEARS[-1])]
    g = w.groupby(w.index.to_period("M"))
    m = pd.DataFrame(
        {"energy": g["energy"].mean(), "oat": g[driver].mean(), "days": g["energy"].size()}
    )
    m.index = m.index.to_timestamp()
    return m[m["days"] >= MIN_WORKDAYS]


def ranked_fit(m: pd.DataFrame) -> dict:
    """The best model of one period, ranked as select_method ranks (SEP validity, adj. R2)."""
    T, y, w = m["oat"].to_numpy(float), m["energy"].to_numpy(float), m["days"].to_numpy(float)
    best = None
    for kind in KINDS:
        try:
            mod = fit_model(T, y, kind, time_index=m.index, weights=w)
            st = fit_stats(y, mod.predict(T), N_PARAMS[kind], time_index=m.index, weights=w)
            tests = model_regression_tests(mod, T, y, time_index=m.index, weights=w)
            v = sep_validity(tests, signs=logical_signs(mod))
        except (ValueError, TypeError, np.linalg.LinAlgError):
            continue
        adj = tests.adj_r2 if tests.adj_r2 is not None else float("-inf")
        cand = {
            "model": mod,
            "kind": kind,
            "sep_valid": v.sep_valid,
            "sep_failures": list(v.failures),
            "adj_r2": round(float(adj), 4),
            "r2": st.r2,
            "cv_rmse": st.cv_rmse,
            "nmbe": st.nmbe,
            "g14_accept": st.accept,
            "n_months": st.n,
            "n_days": int(w.sum()),
        }
        if best is None or (cand["sep_valid"], cand["adj_r2"]) > (
            best["sep_valid"],
            best["adj_r2"],
        ):
            best = cand
    return best


def _public(fit: dict) -> dict:
    return {k: v for k, v in fit.items() if k != "model"}


def _result(r) -> dict:
    keys = ("method", "kernel", "savings", "savings_pct", "enpi", "enpi_uncertainty")
    out = {k: getattr(r, k) for k in keys}
    out["abs_uncertainty"] = r.abs_uncertainty
    out["declined"] = r.declined
    out["sep_range_valid"] = getattr(r, "sep_range_valid", None)
    return out


def analyse(m: pd.DataFrame) -> dict:
    """Per-year fits, the select_method proposal and the explicit calendar-year chains."""
    res: dict = {"years": {}}
    for y in YEARS:
        fit = ranked_fit(m.loc[str(y)])
        res["years"][str(y)] = _public(fit) if fit else None
    p = select_method(m, baseline=list(BASELINE), reporting=list(REPORTING), days="days")
    res["select_method"] = {
        "proposed": p.proposed,
        "intermediate_period": p.intermediate_period,
        "steps": p.steps,
        "sensitivity": p.sensitivity,
    }
    b, r = m.loc[BASELINE[0] : BASELINE[1]], m.loc[REPORTING[0] : REPORTING[1]]
    chains = {}
    for label, win in INTERMEDIATES.items():
        fit = ranked_fit(m.loc[win[0] : win[1]])
        c = chained_savings(
            fit["model"],
            b["oat"].to_numpy(float),
            b["energy"].to_numpy(float),
            r["oat"].to_numpy(float),
            r["energy"].to_numpy(float),
            periods={
                "baseline": list(BASELINE),
                "intermediate": list(win),
                "reporting": list(REPORTING),
            },
            days_baseline=b["days"].to_numpy(float),
            days_reporting=r["days"].to_numpy(float),
        )
        chains[label] = {"intermediate_model": _public(fit), **_result(c)}
    res["chains"] = chains
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=DATA, help="directory holding db_building_A/B.csv")
    ap.add_argument("--weather-cache", default=None, help="cache directory for the weather")
    ap.add_argument("--offline", action="store_true", help="use the weather cache only")
    ap.add_argument("--json", metavar="PATH", help="write the results as JSON")
    args = ap.parse_args(argv)

    from camber.weather_source import oat_reference_auto

    oat = oat_reference_auto(
        LAT,
        LON,
        "2016-01-01",
        "2019-12-31",
        source="auto",
        tz=TZ,
        cache_dir=args.weather_cache,
        offline=args.offline,
    )
    prov = oat.attrs.get("weather_provenance", {})
    out: dict = {
        "weather": {
            "method": prov.get("method"),
            "stations": [
                {k: s.get(k) for k in ("usaf", "wban", "name", "distance_km", "hours_filled")}
                for s in prov.get("stations", [])
            ],
        },
        "buildings": {},
    }
    for b, name in BUILDINGS.items():
        day = daily(hourly(os.path.join(args.data_dir, f"db_building_{b}.csv")), oat)
        station = analyse(monthly(day, "oat"))
        power = analyse(monthly(day, "t2m_power"))
        out["buildings"][b] = {"name": name, "station_oat": station, "nasa_power_t2m": power}
        print(f"\n== {b}: {name}")
        for y, f in station["years"].items():
            print(
                f"  {y}: {f['kind']:3s} R2 {f['r2']:.3f} CV(RMSE) {f['cv_rmse']:.3f} "
                f"NMBE {f['nmbe']:+.4f} SEP {'valid' if f['sep_valid'] else 'invalid'} "
                f"G14 {'accept' if f['g14_accept'] else 'reject'} ({f['n_months']} months)"
            )
        sm = station["select_method"]
        print(
            f"  select_method proposes: {sm['proposed']} (chain window {sm['intermediate_period']})"
        )
        for row in sm["sensitivity"]:
            print(
                f"    {row['method']:10s} SEnPI {row['enpi']:.3f} +/- {row['enpi_uncertainty']:.3f}"
                f"  saving {row['savings_pct']:+.1%}"
            )
        for label, c in station["chains"].items():
            pw = power["chains"][label]
            print(
                f"  chain via {label}: SEnPI {c['enpi']:.3f} +/- {c['enpi_uncertainty']:.3f} "
                f"(saving {c['savings_pct']:+.1%}, {c['savings']:,.0f} kWh of working days); "
                f"with NASA POWER T2M {pw['enpi']:.3f} +/- {pw['enpi_uncertainty']:.3f}"
            )
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
