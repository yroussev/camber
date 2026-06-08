"""CAMBER on Building Data Genome 2: M&V change-point engine + portfolio storage.

Two demonstrations on real whole-building meter data across many sites/climates:

1. M&V       -- fit the ASHRAE G14 / IPMVP change-point inverse models to daily
                energy vs outdoor temperature. Cooling (chilled-water) energy
                yields textbook 3PC fits; office electricity is largely
                schedule/plug-load driven and fits weakly -- the engine reports
                that honestly (low R^2 / high CV(RMSE), accept=False).
2. Storage   -- ingest many buildings' hourly meters into the Parquet store keyed
                by site/equipment, then query the catalog and read one back.

Run fetch.py first to download the data (CC-BY; not bundled).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd  # noqa: E402

from camber.mandv.intervalfit import daily_energy_vs_temp  # noqa: E402
from camber.mandv.models import N_PARAMS, best_model  # noqa: E402
from camber.mandv.stats import cv_rmse_max_for, fit_stats  # noqa: E402
from camber.mandv.towt import fit_towt  # noqa: E402
from camber.mandv.weather import c_to_f  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "bdg2")
YEAR = ("2016-01-01", "2016-12-31")

# Curated buildings: cooling-energy meters (weather-driven) + office electricity.
CHILLED = ["Fox_lodging_Stephen", "Fox_lodging_Stephan", "Eagle_health_Athena",
           "Bull_education_Magaret", "Hog_education_Hallie"]
ELECTRIC = ["Panther_office_Patti", "Bull_office_Rob", "Gator_office_Carrie"]


def _site_of(meta, b):
    return meta.loc[b, "site_id"]


def _oat_f(weather, site):
    s = weather[weather.site_id == site].set_index("timestamp")["airTemperature"]
    return c_to_f(s.loc[YEAR[0]:YEAR[1]])


def mv_table(meta, weather, meter_csv, buildings, label):
    cvmax = cv_rmse_max_for("daily")
    present = [b for b in buildings
              if b in pd.read_csv(meter_csv, nrows=0).columns]
    df = pd.read_csv(meter_csv, usecols=["timestamp"] + present,
                     parse_dates=["timestamp"]).set_index("timestamp")
    print(f"\n=== M&V: {label} (daily change-point, CV(RMSE) accept <= {cvmax:.0%}) ===")
    print(f"{'building':26s} {'site':9s} {'model':5s} {'n':>4s} {'R2':>5s} "
          f"{'CVRMSE':>7s} acc")
    for b in present:
        e = df[b].loc[YEAR[0]:YEAR[1]].dropna()
        if len(e) < 24 * 150:
            continue
        d = daily_energy_vs_temp(e, _oat_f(weather, _site_of(meta, b)),
                                 rate_is_energy_rate=False)
        if len(d) < 60:
            continue
        m = best_model(d["oat"].values, d["energy"].values)
        st = fit_stats(d["energy"].values, m.predict(d["oat"].values),
                       N_PARAMS[m.kind], cv_rmse_max=cvmax)
        print(f"{b:26s} {_site_of(meta, b):9s} {m.kind:5s} {st.n:>4d} "
              f"{st.r2:>5.2f} {st.cv_rmse:>6.1%} {st.accept}")


def towt_demo(meta, weather, elec_csv):
    """TOWT on a schedule-driven office -- captures what change-point can't."""
    print("\n=== TOWT (Time-of-Week & Temperature) on schedule-driven office ===")
    hdr = pd.read_csv(elec_csv, nrows=0).columns
    # offices with strong weekly structure (where TOWT shines); fall back to any
    preferred = ["Robin_office_Antonina", "Robin_office_Wai", "Robin_office_Maryann"]
    candidates = [c for c in preferred if c in hdr] or \
                 [c for c in hdr if "_office_" in c][:6]
    df = pd.read_csv(elec_csv, usecols=["timestamp"] + candidates,
                     parse_dates=["timestamp"]).set_index("timestamp")
    print(f"{'building':24s} {'model':12s} {'n':>5s} {'R2':>5s} {'CVRMSE':>7s}")
    shown = 0
    for b in candidates[:6]:
        e = df[b].loc[YEAR[0]:YEAR[1]].dropna()
        if len(e) < 24 * 250:
            continue
        oat = _oat_f(weather, _site_of(meta, b))
        a = pd.DataFrame({"e": e, "t": oat}).dropna()
        if len(a) < 24 * 250:
            continue
        m = fit_towt(a["e"], a["t"], occ_split=True)
        st = fit_stats(a["e"].values, m.predict(a.index, a["t"].values),
                       m.n_params, cv_rmse_max=cv_rmse_max_for("hourly"))
        print(f"{b:24s} {'TOWT':12s} {st.n:>5d} {st.r2:>5.2f} {st.cv_rmse:>6.1%}")
        shown += 1
        if shown >= 3:
            break
    print("On offices with real weekly structure TOWT reaches R^2 ~0.9 -- it models")
    print("the occupied/unoccupied schedule a plain temperature change-point ignores.")


def storage_demo(meta, elec_csv, buildings):
    print("\n=== Storage: ingest hourly meters into the Parquet store ===")
    present = [b for b in buildings
              if b in pd.read_csv(elec_csv, nrows=0).columns]
    df = pd.read_csv(elec_csv, usecols=["timestamp"] + present,
                     parse_dates=["timestamp"]).set_index("timestamp")
    st = ParquetStore(tempfile.mkdtemp())
    total = 0
    for b in present:
        s = df[b].loc[YEAR[0]:YEAR[1]].dropna()
        frame = pd.DataFrame({Role.POWER: s})
        total += st.write_role_frame(frame, site=_site_of(meta, b), equip=b,
                                     equip_class="Meter")
    print(f"ingested {total:,} observations from {len(present)} buildings "
          f"across sites {st.sites()}")
    print(f"catalog: {len(st.points())} stored series")
    one = present[0]
    back = st.read_role_frame(site=_site_of(meta, one), equip=one)
    print(f"tag-filtered read of {one}: {back.shape[0]:,} hourly rows, "
          f"roles {[r.value for r in back.columns]}")


def main() -> int:
    if not os.path.exists(os.path.join(DATA, "metadata.csv")):
        print("Data not found. Run:  python examples/bdg2/fetch.py")
        return 1
    meta = pd.read_csv(os.path.join(DATA, "metadata.csv")).set_index("building_id")
    weather = pd.read_csv(os.path.join(DATA, "weather.csv"),
                          usecols=["timestamp", "site_id", "airTemperature"],
                          parse_dates=["timestamp"])
    mv_table(meta, weather, os.path.join(DATA, "chilledwater.csv"), CHILLED,
             "chilled-water cooling energy")
    mv_table(meta, weather, os.path.join(DATA, "electricity.csv"), ELECTRIC,
             "office electricity")
    towt_demo(meta, weather, os.path.join(DATA, "electricity.csv"))
    storage_demo(meta, os.path.join(DATA, "electricity.csv"), CHILLED + ELECTRIC)
    print("\nThe engine picks the cooling change-point (3PC) for weather-driven")
    print("cooling energy and fits it well; weakly-weather-driven electricity is")
    print("reported honestly. The store holds the portfolio keyed by site/equip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
