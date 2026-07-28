# Grid-interactive efficient buildings (GEB)

Efficiency uses *less* energy; a grid-interactive building also *shifts and sheds* load in response
to price and grid-carbon signals. `camber.geb` quantifies that potential from interval load —
pairing with `camber.demand` (peak analytics), `camber.tariff` (rates), and `camber.carbon`.

## Demand response — `demand_response`

Quantify a DR event against an expected baseline:

```python
from camber.geb import demand_response

r = demand_response(
    load_kw,
    baseline_kw,
    event_start="2026-07-15 16:00",
    event_end="2026-07-15 19:00",
    rebound_hours=2,
)
r.energy_shed_kwh, r.avg_shed_kw, r.peak_shed_kw, r.pct_shed, r.rebound_kwh
```

`baseline_kw` is the expected load absent the event — a scalar or a Series (a typical-day profile or
a model projection). **Shed** = baseline − actual over the window; **rebound** = energy *above*
baseline in the `rebound_hours` after (the snap-back that erodes net benefit).

## Flexibility headroom — `flexibility`

```python
from camber.geb import flexibility

f = flexibility(load_kw, baseload_pct=10)
f.baseload_kw, f.sheddable_kw, f.sheddable_frac, f.peak_to_average
```

Sheddable load is the mean above the always-on **baseload** (the `baseload_pct` percentile). A high
sheddable fraction and peak-to-average ratio mean more DR/shift potential.

## Carbon-aware shifting — `carbon_aware_shift`

```python
from camber.geb import carbon_aware_shift

out = carbon_aware_shift(load_kw, hourly_emissions_factor, shift_kwh=500)
out["co2_saved_kg"], out["ef_high"], out["ef_low"], out["spread_kg_per_kwh"]
```

`emissions_factor` is an hourly grid factor (kgCO₂/kWh). Shifting `shift_kwh` from the dirtiest to
the cleanest decile of hours saves `shift_kwh × (EF_high − EF_low)` — an upper bound on the carbon
value of flexibility.

## Operation timing score — `operation_score`

How well is load *timed* against a cost or carbon signal?

```python
from camber.geb import operation_score

s = operation_score(load_kw, hourly_price, label="price")
s.load_weighted_avg  # $/kWh the building actually incurred
s.score  # 1 = every kWh in the cheapest hours, 0 = the worst, ~0.5 = flat
s.vs_flat_pct  # % better(-)/worse(+) than indifferent (flat) operation
```

The best/worst bounds keep the **same load magnitudes and the same signal values** and pair them
optimally (rearrangement inequality): best pairs the biggest loads with the smallest signal, worst
with the largest. `score` places the actual load-weighted average between them. Works for a price
($/kWh) or a carbon (kgCO₂/kWh) signal.

## Scope

These are *analytics* — they quantify shed, flexibility, and timing from measured load and a
supplied grid signal. They are advisory, not control: CAMBER stays read-only toward the BAS
(closed-loop DR dispatch is a roadmap Horizon item).

## OpenADR report export (`interop.openadr`)

Hand a measured DR event to a demand-response program in a shape it recognizes:

```python
from camber.interop.openadr import to_openadr_report

r = demand_response(load_kw, baseline_kw, event_start=..., event_end=...)
report = to_openadr_report(r, program_id="PROG-1", event_id="EV-42", client_name="HQ")
```

Maps a `DemandResponseResult` to an **OpenADR-3.0-shaped report** — baseline / actual / shed energy
as interval payloads (KWH) plus a performance summary (avg/peak reduction kW, %, rebound). It's a
**schema-level mapping for interop**, not a VTN/VEN transport; `openadr_report_json(...)` gives the
JSON. Timestamps are caller-supplied (`created=`) — nothing is fabricated.
