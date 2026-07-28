"""Cross-capability demo: GEB flexibility, carbon-aware timing, and load forecasting.

Runs entirely on synthetic data (no downloads) to show the sprint capabilities working together:
a day's demand-response event, the building's carbon timing premium against an hourly grid factor,
its operation-timing score, and a next-day load forecast with learned-normal anomaly flags.

    python examples/geb_carbon_demo.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from camber.carbon_hourly import hourly_emissions  # noqa: E402
from camber.forecast import backtest, forecast_anomalies, seasonal_forecast  # noqa: E402
from camber.geb import demand_response, flexibility, operation_score  # noqa: E402


def synth(weeks=4, seed=7):
    idx = pd.date_range("2026-06-01", periods=weeks * 7 * 24, freq="1h")
    rng = np.random.default_rng(seed)
    occ = ((idx.hour >= 7) & (idx.hour <= 18) & (idx.dayofweek < 5)).astype(float)
    load = pd.Series(45 + 70 * occ + rng.normal(0, 2, len(idx)), index=idx)
    # dirty/expensive midday, clean/cheap overnight (aligned to the same index)
    factor = pd.Series(0.25 + 0.35 * ((idx.hour >= 10) & (idx.hour <= 17)), index=idx)
    return load, factor


def main() -> int:
    load, factor = synth()
    print("CAMBER GEB + carbon + forecast demo (synthetic)\n" + "=" * 48)

    # 1) Flexibility headroom
    f = flexibility(load, baseload_pct=10)
    print(
        f"\nFlexibility: baseload {f.baseload_kw} kW, sheddable {f.sheddable_kw} kW "
        f"({f.sheddable_frac:.0%}), peak/avg {f.peak_to_average}"
    )

    # 2) A 3-hour afternoon DR event on the last full weekday (shed occupied load)
    weekday_afternoons = load.index[(load.index.dayofweek < 5) & (load.index.hour == 15)]
    ev_start = weekday_afternoons[-1]
    ev_end = ev_start + pd.Timedelta(hours=3)
    shed = load.copy()
    ev = (shed.index >= ev_start) & (shed.index <= ev_end)
    baseline = float(load[ev].mean())  # expected occupied load absent the event
    shed[ev] = f.baseload_kw + 20  # curtail toward baseload
    dr = demand_response(shed, baseline, event_start=ev_start, event_end=ev_end)
    print(
        f"\nDR event {ev_start:%m-%d %H:%M}–{ev_end:%H:%M}: shed {dr.energy_shed_kwh} kWh "
        f"({dr.pct_shed:.0%}), peak {dr.peak_shed_kw} kW, rebound {dr.rebound_kwh} kWh"
    )

    # 3) Carbon timing + operation score against the grid factor
    e = hourly_emissions(load, factor)
    print(
        f"\nCarbon: {e.co2e_kg:.0f} kgCO2e over {e.kwh:.0f} kWh; effective factor "
        f"{e.effective_factor} vs avg {e.avg_factor} -> timing premium {e.timing_premium_pct:+.1f}%"
    )
    os_ = operation_score(load, factor, label="carbon")
    print(f"Operation-timing score: {os_.score:.2f} (1=ideal), {os_.vs_flat_pct:+.1f}% vs flat")

    # 4) Forecast the last week + flag anomalies (with two injected spikes)
    horizon = load.index[-7 * 24 :]
    fc = seasonal_forecast(load.iloc[: -7 * 24], horizon)
    actual = load.loc[horizon].copy()
    actual.iloc[30] += 180
    actual.iloc[120] -= 150
    bt = backtest(load, test_frac=0.25)
    rep = forecast_anomalies(actual, fc, k=4.0)
    print(f"\nForecast backtest: MAPE {bt['mape']:.1%}, CV(RMSE) {bt['cv_rmse']:.1%}")
    print(
        f"Learned-normal anomalies: {rep.n_anomalies} of {rep.n} intervals flagged "
        f"(band ±{rep.band:.0f} kW)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
