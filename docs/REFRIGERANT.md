# Refrigerant properties: pressure to saturation temperature

*Provisional, 0.93 (GitHub issue #39).*

A BAS or lab logger usually trends refrigerant **pressures** and **line temperatures**. The charge and
heat-transfer detectors read the saturation-referenced differences a technician takes off a gauge
set. `camber.refrigerant` converts the first into the second:

| Quantity | Transform | Saturation line |
|---|---|---|
| liquid subcooling | `T_sat(P_liquid) - T_liquid_line` | bubble |
| suction superheat | `T_suction_line - T_sat(P_suction)` | dew |
| discharge superheat | `T_discharge_line - T_sat(P_discharge)` | dew |
| condenser approach | `T_sat(P_discharge) - T_condenser_leaving_water` | dew |
| evaporator approach | `T_chw_leaving - T_sat(P_suction)` | dew |

```python
from camber import refrigerant as R

R.saturation_temp(118.8, "R-410A")  # ~40 degF; psig by default
R.saturation_temp(830, "R-410A", pressure_unit="kPa", basis="absolute", temp_unit="C")
R.subcooling(liquid_pressure, liquid_line_temp, "R-410A")  # Series in, Series out
```

## Fluids and sources

CAMBER adds no property library. Each fluid's saturation curve is a published vapour-pressure
correlation of the Wagner form, `ln(p/p_c) = (T_c/T) * sum(n_i * (1 - T/T_c)^t_i)`. It is evaluated
directly for `p(T)` and inverted by bisection for `T(p)`.

| Fluid | Correlation |
|---|---|
| **R-410A** | Bubble and dew equations of Lemmon (2003), *Int. J. Thermophys.* 24(4):991-1006, doi:10.1023/A:1025048800563: the pseudo-pure R-410A model's own saturation lines |
| **R-744** (CO₂) | Vapour-pressure equation (eq. 3.13) of Span & Wagner (1996), *J. Phys. Chem. Ref. Data* 25(6):1509, doi:10.1063/1.555991 |
| **R-134a**, **R-22**, **R-32** | Ancillary vapour-pressure fits to the reference equations of state (Tillner-Roth & Baehr 1994; Kamei, Beyerlein & Jacobsen 1995; Tillner-Roth & Yokozeki 1997) as published in CoolProp 8.0.0 (Bell et al. 2014, doi:10.1021/ie4033999; MIT licence, credited in NOTICE) |

**Accuracy.** The target was ±0.5 °F across the HVAC range. On R-410A the correlation matches the
NIST REFPROP saturation temperatures that NIST publishes beside the raw pressures in its
residential heat-pump FDD data (doi:10.18434/M32132). Over 7,374 test points from 78 to 625 psia,
the bubble line is within 0.011 °F and the dew line within 0.007 °F. The pure-fluid correlations
agree with their full reference equations of state (evaluated with CoolProp) to within 0.014 °F
from -40 to 150 °F. The test suite pins the NIST values and, when CoolProp is installed, repeats
the CoolProp comparison. CoolProp is used only for that cross-check and is never a dependency. For
comparison, a field transducer's 1 psi error at 120 psig shifts R-410A saturation by about 0.6 °F.

## Gauge, absolute and altitude

CAMBER's pressure roles are **gauge** (psig). A dataset published in psia is converted at ingest.
`basis="gauge"` is therefore the default, and absolute pressure is the reading plus
`STANDARD_ATM_PSIA` (14.696 psia). A site well above sea level can pass its own `atm_psia`: at
5,000 ft the atmosphere is about 12.2 psia, and assuming 14.7 there reads R-410A saturation about
1.3 °F high at suction and 0.5 °F high at discharge.

## Blends, glide and which pressure

R-410A is a near-azeotropic blend. At one pressure its bubble (saturated liquid) and dew (saturated
vapour) temperatures differ by about 0.2 °F. Subcooling is referenced to the bubble point and every
superheat to the dew point, which is the convention of manufacturer charging tables and of NIST's
own columns. The approaches use the dew point, the compressor-rating convention for a blend's
saturated temperature.

Subcooling should use the **liquid-line** pressure (`liquid_line_pressure`). Where only the
discharge pressure is trended, CAMBER uses it instead. It sits a few psi above the liquid line, so
subcooling reads slightly high, but the offset is constant and a drift detector absorbs it. Suction
pressure likewise stands in for evaporator pressure.

## CO₂ is transcritical

Above its critical point (87.8 °F, 1,070 psia) CO₂ has no saturation state. A gas cooler's
pressure then has no condensing temperature, and subcooling is undefined. `saturation_temp`
returns NaN there, and does the same for any fluid at or above its critical pressure, below its
lowest valid temperature, or for a reading below a perfect vacuum (a dead transducer). Every
transform therefore **declines** row by row instead of extrapolating a curve that does not exist.
`is_supercritical` flags those rows.

A transcritical booster rack crosses the critical pressure routinely in warm weather. In the
`ornl-supermarket-fdd` CO₂ rack, the 0.89 intake found the medium-temperature discharge
supercritical in 7-12% of summer rows. The high-side transforms of an R-744 system are therefore
available only while it runs subcritical. The suction side is always subcritical.

## Turning it on for equipment

Name the refrigerant on an equipment entry of a run config:

```json
"equipment": [{"class": "CHILLER", "marker_role": "discharge_pressure", "refrigerant": "R-410A"}]
```

With the refrigerant named, any rule that asks for `subcooling_temp`, `superheat_temp`,
`discharge_superheat_temp`, `cond_approach_temp` or `evap_approach_temp` gets it derived at resolve
time (`camber.refrigerant.derive_refrigerant_roles`). The derivation reads the pressure roles and
the new raw-temperature roles:

| Role | Meaning |
|---|---|
| `liquid_line_temp` | refrigerant liquid-line temperature, °F |
| `suction_line_temp` | refrigerant suction-line temperature, °F |
| `discharge_line_temp` | compressor discharge-line temperature, °F |
| `liquid_line_pressure` | liquid-line refrigerant pressure, psig |
| `discharge_superheat_temp` | discharge superheat (a difference), °F |

A value a controller already reports is kept. From a Parquet store the derivation runs on the stored
grid, before any resample. From per-point CSV folders it runs on the resampled bin means.

## Validation on NIST IBAL (false alarms)

`nist-ibal` is a nominal 5-ton R-410A water-cooled lab chiller. It logs gauge pressures and
liquid- and suction-line RTDs, but no subcooling or superheat. Its run template now names R-410A,
so the whole refrigerant side of the `chiller` drift family runs: head and suction pressure,
subcooling, superheat, and the condenser and evaporator approaches. Condenser-water range runs too;
the tower approach declines because the export has no tower. On running hours the derived values
are plausible for a healthy TXV machine: subcooling about 11 °F, superheat about 7 °F, and
approaches about 9-10 °F.

The logged refrigerant leak and recharges predate the refrigerant sensors, so IBAL measures
**specificity only**. The results:

- **Default window** (baseline 2025-06-28 to 07-01, current 07-02 to 07-09): all six evaluated
  detectors `ok`. No magnitude alarm and no sustained (CUSUM) alarm.
- **Full 2025 export**, with a frozen baseline of 2025-01-28 to 03-31 and six monthly current
  windows (April to September): **0 of 36** detector-windows raised a magnitude (warn / fault)
  alarm. Two of 36 raised a provisional sustained-shift (CUSUM) prompt: head pressure and
  subcooling in April, both at severity `ok`. The CUSUM parameters are documented as untuned.
- Sub-vacuum pressure readings (a dead transducer; the `refrigerant-pressure-below-vacuum` data
  issue) derive no saturation temperature and drop out rather than producing a false alarm.
