"""Per-fault dollar impact: turn a Finding into an estimated annual energy/cost waste.

Detection ranks faults by severity; an operator with a fixed budget wants them ranked by
**money**. This module estimates the annual energy waste a fault represents and prices it,
so the prioritizer can sort by dollars rather than severity alone.

The estimate is deliberately **triage-grade**, not audit-grade. Each fault archetype combines
the fault's *intensity* metric (usually a percent of operating hours, already measured by the
rule) with the equipment's *sizing* (capacity, motor power) and a small set of **documented,
override-able assumptions** (reheat diversity, chiller COP, etc.). It answers "which faults are
worth an engineer's week" -- not "what will the retrofit save" (that is the M&V / ECM track:
:mod:`camber.mandv`, :mod:`camber.finance`).

Every :class:`FaultCost` carries the energy split, the dollar figure, the ``basis`` (which
model ran), and the ``assumptions`` actually used -- so a number can always be defended or
re-run with better inputs. When the required sizing is missing, the estimator returns an
**uncosted** result that says what input it needs rather than fabricating a figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .integrate.tickets import _attr

# 1 kWh = 3.412 kBtu; 1 therm = 100 kBtu.
_KBTU_PER_KWH = 3.412
_KBTU_PER_THERM = 100.0

# Documented default assumptions. Override per call via ``params=`` (shallow-merged).
DEFAULTS = {
    "reheat_diversity": 0.30,        # mean reheat-coil output as a fraction of capacity when faulted
    "cool_cop": 3.5,                 # chiller COP for the cooling that fights the reheat
    "boiler_efficiency": 0.80,       # combustion/output efficiency (gas input = output / eff)
    "chiller_target_kw_per_ton": 0.65,   # fallback efficiency target if the finding lacks a design value
    "chiller_load_factor": 0.60,     # mean chiller load as a fraction of full when running
    "ct_kw_per_approach_f": 0.015,   # chiller power penalty per °F of cooling-tower approach above design
    "fan_excess_frac": 0.15,         # supply-fan power wasted when duct static rides high
    "pump_excess_frac": 0.15,        # loop-pump power wasted when it rides the curve (no DP reset)
    "per_start_loss": 0.005,         # boiler standby/purge loss per excess start/day (capped at 10%)
}


@dataclass(frozen=True)
class EnergyPrice:
    """Marginal energy prices used to value waste (defaults are placeholders -- set yours)."""

    electricity_per_kwh: float = 0.15
    gas_per_therm: float = 1.20


@dataclass
class EquipmentLoad:
    """Sizing / operating context for one equipment. All optional; each estimator uses what it
    needs and reports what is missing. Capacities prefer values the rule already measured (e.g.
    chiller tons), falling back to these."""

    heating_capacity_kbtuh: float | None = None   # reheat coil / burner output capacity
    cooling_tons: float | None = None             # chiller / cooling-coil capacity
    chiller_kw_per_ton: float | None = None       # full-load electrical input
    fan_kw: float | None = None                   # supply-fan motor power
    pump_kw: float | None = None                  # loop-pump motor power
    annual_hours: float = 8760.0                  # operating hours per year for this equipment


@dataclass(frozen=True)
class FaultCost:
    """Estimated annual cost of one fault, with the energy split and the basis/assumptions."""

    rule: str
    equip: str
    severity: str
    electricity_kwh: float
    gas_therms: float
    annual_cost_usd: float
    basis: str                       # which model ran, or why it couldn't
    costed: bool                     # False = uncosted (missing inputs / no model)
    assumptions: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def _num(metrics, *keys):
    """First numeric value among ``keys`` in ``metrics`` (else None)."""
    for k in keys:
        v = (metrics or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _frac(pct):
    """Clamp a percentage to a 0..1 fraction."""
    if pct is None:
        return 0.0
    return max(0.0, min(1.0, float(pct) / 100.0))


def _price(elec_kwh, gas_therms, price):
    return elec_kwh * price.electricity_per_kwh + gas_therms * price.gas_per_therm


# --------------------------------------------------------------------------- #
# Estimators: (metrics, load, price, P) -> (elec_kwh, gas_therms, basis, used_assumptions)
# Return elec_kwh=gas_therms=0 with a basis string when a required input is missing.
# --------------------------------------------------------------------------- #

def _reheat_gas(metrics, load, price, P, *, pct_keys, with_cooling):
    """Reheat / simultaneous-H/C gas (and, if paired, the cooling it fights)."""
    cap = load.heating_capacity_kbtuh
    if not cap:
        return 0.0, 0.0, "needs EquipmentLoad.heating_capacity_kbtuh", {}
    frac = _frac(_num(metrics, *pct_keys))
    div, eff = P["reheat_diversity"], P["boiler_efficiency"]
    gas_kbtu_out = cap * load.annual_hours * frac * div
    gas_therms = gas_kbtu_out / _KBTU_PER_THERM / eff
    elec_kwh = 0.0
    used = {"faulted_frac": round(frac, 3), "reheat_diversity": div, "boiler_efficiency": eff}
    if with_cooling:
        cop = P["cool_cop"]
        elec_kwh = (gas_kbtu_out / _KBTU_PER_KWH) / cop      # cooling to remove the reheat heat
        used["cool_cop"] = cop
    return elec_kwh, gas_therms, "reheat-gas" + ("+paired-cooling" if with_cooling else ""), used


def _est_simultaneous(m, load, price, P):
    return _reheat_gas(m, load, price, P, pct_keys=("simultaneous_hc_pct",), with_cooling=True)


def _est_reheat_penalty(m, load, price, P):
    return _reheat_gas(m, load, price, P,
                       pct_keys=("reheat_above_min_flow_pct", "reheat_and_coldsupply_pct",
                                 "valve_open_pct"), with_cooling=False)


def _est_reheat_min(m, load, price, P):
    return _reheat_gas(m, load, price, P,
                       pct_keys=("reheat_hours_pct",), with_cooling=False)


def _est_chiller(m, load, price, P):
    """Excess chiller electricity from running above an efficiency target."""
    kwpt = _num(m, "kw_per_ton_median")
    tons = _num(m, "tons_median") or load.cooling_tons
    target = _num(m, "design_kw_per_ton") or P["chiller_target_kw_per_ton"]
    if kwpt is None or not tons:
        return 0.0, 0.0, "needs kw_per_ton + tons (finding metric or EquipmentLoad)", {}
    excess = max(0.0, kwpt - target)
    hours = _frac(_num(m, "pct_hours_inefficient")) * load.annual_hours
    elec = excess * tons * hours
    return elec, 0.0, "chiller-excess-kw/ton", {
        "excess_kw_per_ton": round(excess, 3), "target_kw_per_ton": round(target, 3),
        "tons": round(tons, 1), "inefficient_hours": round(hours)}


def _est_cooling_tower(m, load, price, P):
    """Chiller power penalty from a cooling-tower approach above design."""
    tons = _num(m, "tons_median") or load.cooling_tons
    kwpt = load.chiller_kw_per_ton or _num(m, "kw_per_ton_median")
    if not tons or not kwpt:
        return 0.0, 0.0, "needs cooling_tons + chiller_kw_per_ton", {}
    approach = _num(m, "approach_median_f")
    design = _num(m, "design_approach_f") or 0.0
    excess_f = max(0.0, (approach or 0.0) - design)
    if excess_f <= 0:
        return 0.0, 0.0, "approach within design (no penalty)", {"excess_approach_f": 0.0}
    penalty = excess_f * P["ct_kw_per_approach_f"]
    hours = _frac(_num(m, "pct_hours_high_approach")) * load.annual_hours
    chiller_kw = tons * kwpt * P["chiller_load_factor"]
    elec = penalty * chiller_kw * hours
    return elec, 0.0, "cooling-tower-approach->chiller", {
        "excess_approach_f": round(excess_f, 1), "penalty_frac": round(penalty, 4),
        "chiller_kw": round(chiller_kw, 1), "high_approach_hours": round(hours)}


def _est_pump(m, load, price, P):
    """Loop-pump energy wasted by riding the curve (no DP reset)."""
    if not load.pump_kw:
        return 0.0, 0.0, "needs EquipmentLoad.pump_kw", {}
    frac = _frac(_num(m, "pct_running_near_full", "median_speed_pct"))
    excess = P["pump_excess_frac"]
    elec = load.pump_kw * load.annual_hours * frac * excess
    return elec, 0.0, "pump-riding-curve", {
        "near_full_frac": round(frac, 3), "pump_excess_frac": excess}


def _est_static(m, load, price, P):
    """Supply-fan energy wasted when duct static rides high (boxes throttling)."""
    if not load.fan_kw:
        return 0.0, 0.0, "needs EquipmentLoad.fan_kw", {}
    frac = _frac(_num(m, "pct_boxes_low"))           # boxes choked down -> static too high
    excess = P["fan_excess_frac"]
    elec = load.fan_kw * load.annual_hours * frac * excess
    return elec, 0.0, "duct-static-high->fan", {
        "throttled_box_frac": round(frac, 3), "fan_excess_frac": excess}


def _est_boiler_cycle(m, load, price, P):
    """Boiler standby/purge loss from short-cycling."""
    cap = load.heating_capacity_kbtuh
    if not cap:
        return 0.0, 0.0, "needs EquipmentLoad.heating_capacity_kbtuh", {}
    starts = _num(m, "starts_per_day") or 0.0
    limit = _num(m, "max_starts_per_day") or 0.0
    extra = max(0.0, starts - limit)
    loss = min(0.10, extra * P["per_start_loss"])
    if loss <= 0:
        return 0.0, 0.0, "within start limit (no penalty)", {"extra_starts_per_day": 0.0}
    eff = P["boiler_efficiency"]
    runtime = _frac(_num(m, "runtime_pct"))
    gas_input = cap * load.annual_hours * runtime / _KBTU_PER_THERM / eff
    return 0.0, gas_input * loss, "boiler-short-cycle", {
        "extra_starts_per_day": round(extra, 1), "loss_frac": round(loss, 3),
        "runtime_frac": round(runtime, 3)}


# rule name -> estimator
DEFAULT_MODELS = {
    "simultaneous_heat_cool": _est_simultaneous,
    "reheat_penalty": _est_reheat_penalty,
    "reheat_minimization_g36": _est_reheat_min,
    "chiller_efficiency": _est_chiller,
    "cooling_tower_approach": _est_cooling_tower,
    "hw_pump_dp_reset": _est_pump,
    "chw_pump_dp_reset": _est_pump,
    "duct_static_high": _est_static,     # alias if a static rule emits this name
    "damper_census": _est_static,
    "boiler_short_cycle": _est_boiler_cycle,
}


def estimate_cost(finding, load: EquipmentLoad | None = None, price: EnergyPrice | None = None,
                  *, params: dict | None = None, models: dict | None = None) -> FaultCost:
    """Estimate one finding's annual cost. Falls back to explicit ``waste_*`` metrics, then
    to an uncosted result when no model applies or required sizing is absent."""
    price = price or EnergyPrice()
    load = load or EquipmentLoad()
    P = {**DEFAULTS, **(params or {})}
    models = models if models is not None else DEFAULT_MODELS
    rule = _attr(finding, "rule", "")
    equip = _attr(finding, "equip", "")
    sev = _attr(finding, "severity", "info")
    metrics = _attr(finding, "metrics", {}) or {}

    est = models.get(rule)
    if est is not None:
        elec, gas, basis, used = est(metrics, load, price, P)
        costed = (elec > 0 or gas > 0)
    else:
        # generic fallback: price an explicit energy estimate the rule may have attached
        elec = _num(metrics, "waste_kwh") or 0.0
        gas = _num(metrics, "waste_therms") or 0.0
        costed = (elec > 0 or gas > 0)
        basis = "explicit-waste-metric" if costed else f"no cost model for rule '{rule}'"
        used = {}
    cost = _price(elec, gas, price)
    return FaultCost(rule=rule, equip=equip, severity=sev,
                     electricity_kwh=round(elec, 1), gas_therms=round(gas, 1),
                     annual_cost_usd=round(cost, 2), basis=basis, costed=costed,
                     assumptions=used)


def _load_for(loads, equip):
    if loads is None:
        return EquipmentLoad()
    if isinstance(loads, EquipmentLoad):
        return loads
    return loads.get(equip, EquipmentLoad())


def cost_findings(findings, loads=None, price: EnergyPrice | None = None,
                  *, params: dict | None = None, models: dict | None = None) -> list:
    """Estimate cost for many findings. ``loads`` is one :class:`EquipmentLoad` for all, or a
    ``{equip: EquipmentLoad}`` map (missing equipment get defaults)."""
    return [estimate_cost(f, _load_for(loads, _attr(f, "equip", "")), price,
                          params=params, models=models) for f in findings]


def annotate_costs(findings, loads=None, price: EnergyPrice | None = None,
                   *, params: dict | None = None, models: dict | None = None) -> list:
    """Write ``annual_cost_usd`` / ``waste_kwh`` / ``waste_therms`` into each finding's metrics
    (in place) so the severity-first prioritizer can rank within a tier by dollars via
    ``rank_findings(..., magnitude_key="annual_cost_usd")``. Returns the findings."""
    out = list(findings)
    for f in out:
        fc = estimate_cost(f, _load_for(loads, _attr(f, "equip", "")), price,
                           params=params, models=models)
        m = _attr(f, "metrics", None)
        if isinstance(m, dict):
            m["annual_cost_usd"] = fc.annual_cost_usd
            m["waste_kwh"] = fc.electricity_kwh
            m["waste_therms"] = fc.gas_therms
    return out


def rank_by_cost(findings, loads=None, price: EnergyPrice | None = None,
                 *, params: dict | None = None, models: dict | None = None,
                 costed_only: bool = False) -> list:
    """Rank findings purely by estimated annual dollars (worst first). Returns the
    :class:`FaultCost` items in order; ``costed_only`` drops faults with no usable estimate."""
    costs = cost_findings(findings, loads, price, params=params, models=models)
    if costed_only:
        costs = [c for c in costs if c.costed]
    return sorted(costs, key=lambda c: -c.annual_cost_usd)


def total_cost(fault_costs) -> dict:
    """Roll up a list of :class:`FaultCost` into totals (kWh, therms, $, n costed/uncosted)."""
    elec = sum(c.electricity_kwh for c in fault_costs)
    gas = sum(c.gas_therms for c in fault_costs)
    usd = sum(c.annual_cost_usd for c in fault_costs)
    n_costed = sum(1 for c in fault_costs if c.costed)
    return {"electricity_kwh": round(elec, 1), "gas_therms": round(gas, 1),
            "annual_cost_usd": round(usd, 2), "n_costed": n_costed,
            "n_uncosted": len(fault_costs) - n_costed}
