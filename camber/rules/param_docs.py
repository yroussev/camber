"""Documented, tunable rule parameters (0.98, #90): one entry per rule and constructor parameter.

Every numeric, flag or enumerated constructor parameter of a registered rule is documented here,
keyed by rule name and then parameter name. An entry records:

- ``unit`` -- the physical unit ("°F", "%", "in. w.c.", "ppm", "h", ...), or "flag" / "choice" /
  "-" (dimensionless) / "fraction" / "count";
- ``basis`` -- where the default comes from. It starts with one of :data:`BASIS_KINDS`:
  ``"standard: <document> §<section>"`` only where the code cites one, ``"public source: ..."``
  for a published, non-standard source (the PNNL Re-tuning guides, a manufacturer note),
  ``"CAMBER judgment"`` (optionally ``": why"``) when the value is an engineering choice, and
  ``"calibrated on <dataset/run>"`` when it was fitted to data;
- ``calibrate`` -- how to re-tune it from your own data;
- ``range`` -- a sensible range: ``(low, high)`` for a number, the allowed values for a choice
  (``("mean", "median")``) and ``(False, True)`` for a flag. A **dict-valued** parameter (a tier
  map such as ``{"warn": 5.0, "fault": 20.0}``) gives the range of **each value**, and its unit
  ends with the keys (``"%..., per key ('warn', 'fault')"``); the note states any order between
  the keys (``warn <= fault``). ``camber rules params`` and ``docs/THRESHOLDS.md`` print such a
  range as "each value: low to high". A parameter that takes **a keyword or a number** (0.98,
  #86: ``near_min_pct="auto"`` or ``25.0``) lists the keywords first and ends with the numeric
  ``low, high``: ``("auto", 25.0, 60.0)``, printed as '"auto", or 25.0 to 60.0';
- ``note`` (optional) -- what a ``None`` default means, or how the parameter interacts with others.

**Defaults are never copied here.** They are read from the rule constructors
(:func:`rule_params`), so this file cannot drift from the code. ``camber rules params`` prints the
registry with those defaults, and ``scripts/thresholds_doc.py`` renders it into
``docs/THRESHOLDS.md``. ``tests/test_param_docs.py`` fails when a constructor parameter of a
registered rule has no entry here (or no :data:`EXEMPT` reason), or when an entry names a parameter
that no longer exists.

Provisional API (0.98): the registry's shape may still change before 1.0.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

__all__ = [
    "BASIS_KINDS",
    "DELEGATES",
    "DRIFT_EXEMPT",
    "DRIFT_PARAM_DOCS",
    "EXEMPT",
    "FIXED",
    "PARAM_DOCS",
    "ParamDoc",
    "RuleParam",
    "config_snippet",
    "describe",
    "documented_drift_rules",
    "documented_rules",
    "drift_rule_params",
    "json_value",
    "render_text",
    "rule_params",
    "snippet_yaml",
]

#: The allowed prefixes of :attr:`ParamDoc.basis`.
BASIS_KINDS = ("standard: ", "public source: ", "CAMBER judgment", "calibrated on ")


@dataclass(frozen=True)
class ParamDoc:
    """How one tunable rule parameter is documented (the default is read from the code)."""

    unit: str
    basis: str
    calibrate: str
    range: tuple
    note: str = ""

    @property
    def is_choice(self) -> bool:
        """True for a flag or an enumerated parameter (``range`` lists the allowed values)."""
        return all(isinstance(v, (str, bool)) for v in self.range)


def _P(unit: str, basis: str, calibrate: str, range: tuple, note: str = "") -> ParamDoc:
    """Shorthand constructor used by the registry below."""
    return ParamDoc(unit=unit, basis=basis, calibrate=calibrate, range=range, note=note)


# --------------------------------------------------------------------------- shared entries
# Parameters that mean the same thing in several rules. A rule that uses one says so by
# splatting the shared dict, e.g. ``**_SCHEDULE``.

_SCHEDULE = {
    "start_hour": _P(
        "hour of day (0-23)",
        "CAMBER judgment: a typical weekday office schedule (07:00-18:00)",
        "Set it to the start of the building's occupied mode, read from the BAS schedule or from "
        "the hour the supply fan or occupied-mode point switches on in a typical week of trends.",
        (0, 23),
        "Used only when no occupancy point (the OCCUPANCY role) is mapped: one replaces the "
        "schedule.",
    ),
    "end_hour": _P(
        "hour of day (1-24)",
        "CAMBER judgment: a typical weekday office schedule (07:00-18:00)",
        "Set it to the end of occupied mode (exclusive), read from the BAS schedule or trends.",
        (1, 24),
        "Used only when no occupancy point (the OCCUPANCY role) is mapped: one replaces the "
        "schedule.",
    ),
    "occupied_days": _P(
        "weekday numbers (Mon=0 ... Sun=6)",
        "CAMBER judgment: a Monday-Friday schedule",
        "List the days the building runs occupied mode, e.g. [0, 1, 2, 3, 4, 5] for a Saturday "
        "schedule.",
        (0, 6),
        "A list of integers; each must lie in the range.",
    ),
}


# --------------------------------------------------------------------------- the registry
# One block per rule, in the order of camber.rules.builtin.RULE_CLASSES, then the extra
# instances. Sibling branches add their blocks inside marked comments.

PARAM_DOCS: dict[str, dict[str, ParamDoc]] = {}

#: Constructor parameters that are not thresholds (identity, storage handles, structured inputs
#: described elsewhere), each with the reason it is not documented as a tunable.
EXEMPT: dict[str, dict[str, str]] = {}

#: Rules whose ``**kwargs`` are passed through to another rule: their tunables are that rule's.
DELEGATES: dict[str, str] = {}

#: Thresholds that are still fixed in code (not constructor parameters), so a reader can see what
#: is not yet tunable. Every rule with no constructor parameter has an entry.
FIXED: dict[str, str] = {}

#: Entries for parameters a not-yet-merged branch adds: rule_params() uses one only once the
#: constructor has the parameter; at integration each moves into PARAM_DOCS. Empty after 0.98
#: wave 1 (its entries now live in the rule blocks below).
_PENDING: dict[str, dict[str, ParamDoc]] = {}


# ==== begin air block (air side: AHU coils, supply air, economizer, filter, static, G36 AFDD) ====

# Air-side rules: AHU coils, supply-air reset and control, economizer, filter, static pressure,
# hunting, G36 AFDD and VAV airflow tracking.

_AIR_FAN_GATE = _P(
    "flag",
    "CAMBER judgment: with the fan stopped the air temperatures describe still air, not the unit "
    "at work",
    "Leave it on. Turn it off only when the fan signal is known to be wrong (stuck status, a "
    "speed point that reads 0 while running) and every trended sample is a running sample.",
    (False, True),
    "The fan is read from supply-fan status, else speed, else airflow; a unit with none of them "
    "is judged ungated and the finding says so (the fan_gate metric).",
)

PARAM_DOCS["simultaneous_heat_cool"] = {
    "dehumidification": _P(
        "flag",
        "CAMBER judgment: the unit's sequence is unknown until declared",
        "Read the unit's sequence of operation: true if it has a dehumidification-with-reheat "
        "mode, false if it has none. Leave it unset only when the sequence is unknown.",
        (False, True),
        "None = unknown: each both-open interval is classified from the coil-leaving temperature, "
        "dew point and humidity. True accepts reheat after the coil as dehumidification unless a "
        "dew point shows the coil dry; False counts every both-open interval.",
    ),
    "reheat_lift_f": _P(
        "°F",
        "CAMBER judgment: a supply-air rise over the cooling-coil leaving air beyond sensor noise "
        "means heat is added after the coil",
        "In a known dehumidification period, take the supply-air minus coil-leaving temperature; "
        "in a cooling-only period with the heating valve shut, the same difference (fan heat and "
        "sensor offset) sets the floor. Choose a value between the two.",
        (0.5, 6.0),
    ),
    "dewpoint_margin_f": _P(
        "°F",
        "CAMBER judgment: allows for sensor and dew-point approximation error",
        "Compare the coil-leaving temperature with the entering dew point on hours the coil is "
        "known to condense (drain-pan flow, a falling return humidity); widen the margin if "
        "those hours read as dry.",
        (0.0, 5.0),
    ),
    "humid_rh_pct": _P(
        "% relative humidity (return air)",
        "CAMBER judgment: a common upper bound of the comfort humidity band",
        "Set it to the unit's humidity setpoint or high-humidity limit from the sequence. Used "
        "only where no dew point can be formed.",
        (40.0, 70.0),
    ),
    "fault_pct": _P(
        "% of occupied hours",
        "CAMBER judgment",
        "Look at simultaneous_hc_pct (unexplained_hc_pct when dehumidification is classified) on "
        "units known to be healthy; set the fault level well above their spread. Tuning it on "
        "the unit under test is circular.",
        (1.0, 30.0),
        "Must be at or above warn_pct.",
    ),
    "warn_pct": _P(
        "% of occupied hours",
        "CAMBER judgment",
        "Set it just above the both-open share that healthy units show during valve changeovers "
        "(read simultaneous_hc_pct on a known-good period).",
        (0.1, 10.0),
    ),
}

PARAM_DOCS["supply_air_reset"] = {"fan_gate": _AIR_FAN_GATE}

PARAM_DOCS["supply_air_reset_compliance"] = {
    "min_clg_sat": _P(
        "°F",
        "standard: ASHRAE Guideline 36-2021 §5.16.2.2 (Min_ClgSAT of the OAT-based SAT reset)",
        "Set it to the unit's minimum cooling supply-air setpoint from its sequence or BAS "
        "program; a non-G36 value marks the map as configured (reset_source).",
        (45.0, 60.0),
    ),
    "t_max": _P(
        "°F",
        "standard: ASHRAE Guideline 36-2021 §5.16.2.2 (the upper end of the OAT-based SAT reset)",
        "Set it to the highest cooling supply-air setpoint the sequence allows (G36 resets it "
        "further by trim-and-respond; use the typical value if it varies).",
        (55.0, 70.0),
        "Must be above min_clg_sat.",
    ),
    "oat_min": _P(
        "°F",
        "standard: ASHRAE Guideline 36-2021 §5.16.2.2 (low end of the OAT reset band)",
        "Set it to the outdoor temperature at or below which the sequence holds the warmest "
        "supply air.",
        (40.0, 70.0),
        "Must be below oat_max. Samples at or above it are the warm-weather samples of the "
        "tracking check.",
    ),
    "oat_max": _P(
        "°F",
        "standard: ASHRAE Guideline 36-2021 §5.16.2.2 (high end of the OAT reset band)",
        "Set it to the outdoor temperature at or above which the sequence holds min_clg_sat.",
        (55.0, 85.0),
    ),
    "tol_f": _P(
        "°F",
        "CAMBER judgment: screening-grade (docs/TR-RESET.md: provisional, untuned)",
        "Set it to the supply-air sensor's tolerance plus the loop's normal deadband; the "
        "mean_abs_error_f of a unit known to follow its reset shows the floor.",
        (0.5, 3.0),
    ),
    "warn_pct": _P(
        "% of gated samples",
        "CAMBER judgment: screening-grade (docs/TR-RESET.md: provisional, untuned)",
        "Read pct_below_g36_target on units known to follow the reset and set the warn level "
        "above their spread; scoring the same units you tuned on is circular.",
        (10.0, 80.0),
        "Also the share of warm-weather samples above target that trips the NOT-tracking warn.",
    ),
    "warn_gap_f": _P(
        "°F",
        "CAMBER judgment: a trivially small persistent gap should not warn",
        "Set it to the smallest mean below-target gap worth a reheat fix -- roughly the supply "
        "air change that moves reheat energy noticeably at the site (mean_gap_f reports it).",
        (0.5, 5.0),
    ),
    "track_gap_f": _P(
        "°F",
        "CAMBER judgment: screening-grade (docs/TR-RESET.md)",
        "Read mean_above_gap_f on units known to track their reset in warm weather; set it well "
        "above that.",
        (2.0, 10.0),
    ),
    "fan_gate": _AIR_FAN_GATE,
    "occupied_only": _P(
        "flag",
        "CAMBER judgment: the reset applies in occupied mode",
        "Leave it on. With no trended occupancy the gate assumes weekdays 07-18; map an "
        "occupancy point, or turn it off for a unit that runs occupied around the clock.",
        (False, True),
    ),
}
EXEMPT["supply_air_reset_compliance"] = {
    "reset_source": "a provenance label printed in the finding, not a threshold",
}

PARAM_DOCS["supply_air_control"] = {
    "tol_F": _P(
        "°F",
        "CAMBER judgment: the ±2 °F band a tuned discharge-air loop holds",
        "On a known-good period take the 95th percentile of |SAT - setpoint| over fan-on hours "
        "(mean_abs_dev_F is the finding's summary of it) and set the tolerance just above it.",
        (0.5, 5.0),
    ),
    "warn_pct": _P(
        "% of fan-on running samples",
        "CAMBER judgment",
        "Read off_setpoint_pct on units known to control well and set the warn level above their "
        "spread; tuning it on the unit under test is circular.",
        (2.0, 30.0),
    ),
    "fault_pct": _P(
        "% of fan-on running samples",
        "CAMBER judgment",
        "Set it well above warn_pct, at the off_setpoint_pct where comfort or reheat complaints "
        "begin at the site.",
        (10.0, 60.0),
        "Must be at or above warn_pct.",
    ),
    "occupancy_gate": _P(
        "choice",
        "CAMBER judgment (#84): on the fault-free lbnl-sdahu unit, 78 % of the fan-on hours more "
        "than 2 F too warm were unoccupied fan cycling (damper shut, warm return air), which "
        "graded a healthy unit warn at 12.1 %; gated on its trended occupancy (SYS_CTL) it reads "
        "ok at 2.97 %. PNNL's discharge-air guide asks whether the unit meets its setpoint while "
        "it serves the building, i.e. in occupied operation.",
        "Keep 'trended' when the unit trends an occupied/unoccupied (or occupied-mode) point. "
        "To see what the gate removes, run once with 'off' and compare too_warm_pct / "
        "too_cold_pct and n_running: a large drop that sits in night or weekend hours is "
        "unoccupied cycling, not a control fault. Use 'schedule' only when no occupancy is "
        "trended AND you know the unit follows a weekday office schedule; then set the "
        "building's own hours on a rule that takes them, since this one uses the generic "
        "Mon-Fri 07-18 window.",
        ("trended", "schedule", "off"),
        "'trended' (default): fan-on samples AND the trended occupancy when the unit trends one "
        "(any non-null value); with none trended, fan-on samples only (no schedule fallback, so "
        "a unit that runs evenings or weekends on purpose keeps those hours). 'schedule': the "
        "trended occupancy if present, else the assumed weekday 07-18 schedule. 'off': fan-on "
        "samples only (the pre-0.98 behaviour). The gate lives in the running mask, so the "
        "evidence chart and the triage violation mask judge the same samples. The finding's "
        "occupancy_gate metric reports 'trended occupancy', 'assumed schedule (weekdays "
        "07-18)', 'none trended (fan-on hours only)' or 'off'.",
    ),
}

PARAM_DOCS["outdoor_air_fraction"] = {
    "min_oa_pct": _P(
        "% of supply airflow (OA fraction)",
        "CAMBER judgment: a generic assumed design minimum; the real one is the unit's own",
        "Take it from the ventilation design or sequence, or measure the OA fraction at the "
        "unit's minimum damper position on a fan-on period with |OAT - RAT| of 10 °F or more; a "
        "10 % minimum damper position can be a 1.6 % OA fraction.",
        (0.0, 100.0),
        "Excess OA is OAF above min + 5 %; under-ventilation is a median OAF below min - 5 % "
        "(warn) or below half the minimum (fault).",
    ),
    "cooling_cutoff_f": _P(
        "°F",
        "public source: PNNL Re-tuning Ch.5 with CAMBER judgment (camber/oafraction.py)",
        "Set it to the economizer high limit of the sequence: above it outdoor air is a cooling "
        "penalty, not free cooling.",
        (55.0, 80.0),
    ),
    "denom_min_f": _P(
        "°F",
        "public source: PNNL Re-tuning Ch.5 (a stability guard on the temperature balance)",
        "Raise it when OA fractions scatter widely on mild days (n_masked_small_delta_t shows "
        "how many samples it drops); about 2-3 times the combined OAT/RAT sensor error.",
        (2.0, 15.0),
    ),
    "fan_gate": _AIR_FAN_GATE,
    "min_oa_pct_by_month": _P(
        "% of supply airflow, per month",
        "CAMBER judgment: only for a sequence with a seasonal minimum",
        "Enter the sequence's minimum for each month that differs from min_oa_pct, e.g. "
        "{6: 15, 7: 15, 8: 15}.",
        (0.0, 100.0),
        "None = min_oa_pct all year. A {month (1-12): pct} mapping; each value must lie in the "
        "range.",
    ),
}

PARAM_DOCS["economizer_high_limit"] = {
    "high_limit_f": _P(
        "°F",
        "CAMBER judgment: a typical, not universal, fixed dry-bulb high limit (CA Title 24 sets "
        "it by climate zone)",
        "Set it to the high-limit setpoint of the unit's economizer sequence or the energy code "
        "for its climate zone.",
        (55.0, 80.0),
    ),
    "min_damper": _P(
        "fraction (OA damper, 0-1)",
        "CAMBER judgment",
        "Set it to the minimum OA damper position of the sequence, or the damper position the "
        "unit holds on hot fan-on hours in a known-good period. Used only when no mixed/return "
        "air temperatures or airflows are trended.",
        (0.0, 1.0),
        "A sample counts as not locked out above min_damper + 0.05.",
    ),
    "min_oa_pct": _P(
        "% of supply airflow (OA fraction)",
        "CAMBER judgment: the design minimum is a building property",
        "Take it from the ventilation design, or measure the OA fraction at minimum damper "
        "position on a known-good fan-on period.",
        (0.0, 100.0),
        "None = unknown: excess is scored against conservative_min_oa_pct, and OA that is excess "
        "only against assumed_min_oa_pct is declined.",
    ),
    "oa_margin_pct": _P(
        "% of supply airflow (percentage points)",
        "CAMBER judgment: allows for temperature-balance noise",
        "Set it to the spread of the OA fraction at minimum position on known-good hot hours "
        "(for example its interquartile range).",
        (1.0, 15.0),
    ),
    "warn_pct": _P(
        "% of fan-on samples above the high limit",
        "CAMBER judgment",
        "Read not_locked_out_pct on units known to lock out correctly; set the warn level above "
        "their spread.",
        (2.0, 30.0),
    ),
    "fault_pct": _P(
        "% of fan-on samples above the high limit",
        "CAMBER judgment",
        "Set it well above warn_pct.",
        (10.0, 60.0),
        "Must be at or above warn_pct.",
    ),
    "denom_min_f": _P(
        "°F",
        "public source: PNNL Re-tuning Ch.5 (the same guard as camber/oafraction.py)",
        "Raise it when OA fractions scatter on hours with OAT close to RAT "
        "(n_masked_small_delta_t shows how many samples it drops).",
        (2.0, 15.0),
    ),
    "assumed_min_oa_pct": _P(
        "% of supply airflow (OA fraction)",
        "CAMBER judgment: a generic design minimum",
        "Prefer setting min_oa_pct; change this only to move the generic minimum used in the "
        "declined-case caveat.",
        (5.0, 40.0),
    ),
    "conservative_min_oa_pct": _P(
        "% of supply airflow (OA fraction)",
        "CAMBER judgment: above the design minimum of any ordinary mixed-air unit",
        "Prefer setting min_oa_pct; lower it only for a portfolio whose design minimums are all "
        "known to be lower.",
        (30.0, 80.0),
    ),
    "differential": _P(
        "flag",
        "CAMBER judgment: many economizers use a differential dry-bulb changeover",
        "Set it from the sequence: false for a fixed dry-bulb high limit, true for a differential "
        "(outdoor vs return) changeover.",
        (False, True),
    ),
    "fan_gate": _AIR_FAN_GATE,
}

PARAM_DOCS["free_cooling_missed"] = {
    # ---- begin 098-followups (#91): one free-cooling high limit ----
    "high_limit_f": _P(
        "°F (outdoor air dry-bulb)",
        "CAMBER judgment: a deliberately conservative screening default. Below 60 °F an "
        "economizer should be cooling with outside air in any climate, so a missed hour is "
        "clearly missed. Economizer guidance sets the dry-bulb high limit by climate (ASHRAE 90.1 "
        "§6.5.1.1.3, its high-limit table by climate zone; the PNNL economizer guide, reference "
        "pnnl-guide-economizer), higher in dry climates and lower in humid ones",
        "Set it to the dry-bulb high limit programmed in the unit's economizer sequence, or to "
        "the energy code's high limit for the site's climate zone (a few degrees below it, so "
        "only clearly cool weather counts). From trends, take the highest OAT at which the OA "
        "damper still opens fully over a summer of known-good operation. See docs/TUNING.md.",
        (45.0, 75.0),
        "camber.freecooling.free_cooling_opportunity uses the same default "
        "(DEFAULT_FREE_COOLING_HIGH_LIMIT_F, 60 °F; 65 °F before 0.98). The RCx report's "
        "economizer page passes economizer_high_limit's high_limit_f to it instead, and states "
        "that value. A higher value counts more hours as free-cooling weather.",
    ),
    # ---- end 098-followups (#91) ----
    "active": _P(
        "% of valve stroke (cooling valve)",
        "CAMBER judgment: a valve parked at a few percent is not mechanical cooling running",
        "Set it just above the cooling-valve position seen with cooling off on a known-good "
        "period (its 95th percentile when the chiller or compressor is off).",
        (1.0, 20.0),
    ),
    "warn_pct": _P(
        "% of free-cooling samples",
        "CAMBER judgment",
        "Read missed_pct on units whose economizers are known to work; set the warn level above "
        "their spread.",
        (2.0, 30.0),
    ),
    "fault_pct": _P(
        "% of free-cooling samples",
        "CAMBER judgment",
        "Set it well above warn_pct.",
        (10.0, 60.0),
        "Must be at or above warn_pct.",
    ),
}
# ---- begin 098-rcx-cause (#88): why free cooling was missed (additive metrics, no severity) ----
PARAM_DOCS["free_cooling_missed"].update(
    {
        "cmd_open_pct": _P(
            "% of damper stroke (OA damper command)",
            "CAMBER judgment: the full-outside-air damper test (ECON_DAMPER_MIN_PCT, 90 %); an "
            "economizer damper at full open commonly reads a few percent short",
            "Read the OA-damper command on a known-good unit's free-cooling hours at full "
            "economizer; set it a few percent below the value it holds there.",
            (50.0, 100.0),
            "Only the missed_cause metrics use it; severity does not.",
        ),
        "oaf_open_pct": _P(
            "% outdoor-air fraction (temperature balance)",
            "CAMBER judgment: the full-outside-air OA-fraction test (ECON_OAF_MIN_PCT, 80 %); "
            "sensor error and mixing-box stratification keep a true 100 % OA unit from reading 100",
            "Compute (RAT - MAT) / (RAT - OAT) on a known-good unit's full-economizer hours with "
            "|OAT - RAT| >= 5 °F; set it below their low percentile.",
            (50.0, 100.0),
            "Samples with |OAT - RAT| < 5 °F are not judged (the balance is ill-conditioned).",
        ),
        "stuck_min_share_pct": _P(
            "% of missed, well-conditioned free-cooling samples",
            "CAMBER judgment, checked on lbnl-sdahu (stuck-damper runs 40-87 %, fault-free and "
            "valve-leak runs 0 %) and lbnl-ddahu (stuck-closed run 95 %, fault-free 0 %)",
            "Read commanded_open_pct on units whose dampers are known to work: set it well above "
            "their value (usually 0 %) and below that of a unit with a known stuck damper.",
            (5.0, 60.0),
            "Below it, with a damper command trended, missed_cause is economizer_not_commanded.",
        ),
        "stuck_min_hours": _P(
            "hours",
            "CAMBER judgment: one day of evidence before naming a mechanical cause",
            "Raise it for long windows or noisy mixed-air sensors; one day is the floor for an "
            "hourly trend.",
            (6.0, 168.0),
            "With the share met on fewer hours, missed_cause is undetermined.",
        ),
        "stuck_low_oaf_pct": _P(
            "% outdoor-air fraction",
            "CAMBER judgment: below 30 % the damper delivers about its minimum-OA share or less",
            "Set it just above the unit's design minimum OA fraction plus a margin; a damper "
            "delivering less while commanded open reads 'stuck low', more reads 'stuck part open'.",
            (5.0, 60.0),
            "Recorded on the finding; the recommendation reads it (aso DEFAULT_PARAMS "
            "econ_stuck_low_oaf_pct mirrors the default).",
        ),
    }
)
# ---- end 098-rcx-cause ----

PARAM_DOCS["leaking_valve"] = {
    "fan_heat_f": _P(
        "°F",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (ΔT_SF, Table 5.16.14.7 initial value)",
        "Run a known-good period with both valves closed and the fan on; the median rise from "
        "mixed air to supply air is the fan heat. Record the run in a config comment.",
        (0.5, 4.0),
    ),
    "delta_thr_f": _P(
        "°F",
        "public source: PNNL Re-tuning Ch.5 with CAMBER judgment (a coil-side shift beyond noise)",
        "With both valves known tight, take the spread (e.g. 95th percentile) of the air shift "
        "beyond fan heat (median_delta_f and its per-coil values) and set the threshold above it.",
        (1.0, 6.0),
    ),
    "valve_closed_thr": _P(
        "% of valve stroke",
        "public source: PNNL Re-tuning Ch.5 with CAMBER judgment (a deadband)",
        "Set it just above the position the valve commands read when the BAS commands them "
        "closed (their 95th percentile on off hours).",
        (0.0, 15.0),
    ),
    "coil_sensor_fan_heat": _P(
        "flag",
        "CAMBER judgment: coil sensors sit upstream of a draw-through fan",
        "Set it from the unit's layout: true for a blow-through unit, where the fan sits ahead "
        "of the coils and the coil leaving-air sensors see its heat.",
        (False, True),
    ),
}
# ---- begin 098-air-leak (#84 item 1): measured fan heat, occupancy gate, dual-duct heating ----
PARAM_DOCS["leaking_valve"].update(
    {
        "measured_fan_heat_f": _P(
            "°F",
            "calibrated on the unit's own known-good hours (lbnl-sdahu template: 1.0 from the "
            "fault-free run's median supply minus mixed air, 1.06 °F over 1,400 occupied, fan-on, "
            "valve-shut hours; in-sample, since that run is also scored)",
            "On a period known to be leak-free, take the hours with both valves shut and the fan "
            "on (occupied hours if occupied_only is set) and use the median supply-air minus "
            "mixed-air rise. Calibrate on one period and score on another: a value fitted to a "
            "run makes that run's verdict in-sample.",
            (0.0, 4.0),
            "None = no fan heat credited: a cooling leak must pull the supply air cool_delta_thr_f "
            "below the mixed air. When set, a cooling leak is a rise below measured_fan_heat_f - "
            "cool_delta_thr_f; applies on the supply-air path (or a coil sensor with "
            "coil_sensor_fan_heat), and the cool_shift_f metric reports it.",
        ),
        "cool_delta_thr_f": _P(
            "°F",
            "CAMBER judgment: the cooling margin below the fan-heat line; lbnl-sdahu uses 1.0 "
            "with its 1.0 °F fan heat, so a leak must take the supply air below the mixed air "
            "(fault-free occupied hours below that line: 2.4 %)",
            "With measured_fan_heat_f set, read chw_median_delta_f and its spread on known-good "
            "valve-shut hours and set the margin so few of them (a few percent) fall below the "
            "line measured_fan_heat_f - margin.",
            (0.5, 6.0),
            "None = delta_thr_f.",
        ),
        "occupied_only": _P(
            "flag",
            "calibrated on lbnl-sdahu AHU__fault_free: unoccupied fan cycling reads 24.2 % of "
            "valve-shut hours below the cooling-leak line, occupied hours 2.4 %; off by default "
            "since a leak shows whenever the unit runs",
            "Turn it on when unoccupied fan-on hours (night cycling, morning warm-up) give a "
            "fault-free unit a leak signature; compare chw_leak_pct with it on and off on a "
            "known-good period. Map an occupancy point where one is trended.",
            (False, True),
            "Reads the trended occupancy (the OCCUPANCY role) when it has values, else assumes "
            "weekdays 07-18; the occupancy_gate metric says which.",
        ),
        "judge_heating_on_supply_air": _P(
            "flag",
            "CAMBER judgment: on a single-duct unit the supply air leaves the heating coil",
            "Set it false when the mapped supply air does not pass the heating coil -- a dual-duct "
            "unit whose supply_air_temp is the cold deck (lbnl-ddahu false-faults a stuck-damper "
            "run otherwise). Map the heating coil's leaving air where it is trended.",
            (False, True),
            "False leaves a heating coil without its own leaving-air sensor unjudged (a caveat "
            "says so); a coil with HEAT_COIL_LEAVING_TEMP is judged on it either way.",
        ),
    }
)
# ---- end 098-air-leak ----

PARAM_DOCS["filter_fouling"] = {
    "change_dp_inwc": _P(
        "in. w.c.",
        "CAMBER judgment: a common MERV-13 final-pressure-drop alarm (filter and fan dependent)",
        "Use the filter maker's recommended final resistance for the installed filter, or the "
        "differential pressure at which the site changes filters; filter_dp_median_inwc of a "
        "freshly changed filter gives the clean baseline.",
        (0.3, 2.0),
        "Warn when the median is at or above it; fault at 1.5 times it (fixed in code).",
    ),
}

PARAM_DOCS["static_pressure_reset"] = {
    "min_range_inwc": _P(
        "in. w.c.",
        "CAMBER judgment",
        "On a unit known to run trim-and-respond, read sp_range_inwc over a few weeks and set "
        "the threshold well below it; it should exceed setpoint rounding and a manual tweak.",
        (0.05, 0.5),
    ),
    "move_min_inwc": _P(
        "in. w.c.",
        "CAMBER judgment: about one G36 trim-and-respond response step (SPres ~0.04-0.06 in. w.c.)",
        "Set it to about one response step (SPres) of the site's trim-and-respond, and above "
        "the setpoint register's rounding.",
        (0.01, 0.2),
    ),
}

PARAM_DOCS["control_hunting"] = {
    "warn_per_hr": _P(
        "per hour (direction reversals)",
        "CAMBER judgment",
        "Read reversals_per_hr on loops known to be stable (1-5 min data) and set the warn level "
        "above their spread; the trend must resolve it (max_resolvable_per_hr).",
        (2.0, 20.0),
    ),
    "fault_per_hr": _P(
        "per hour (direction reversals)",
        "CAMBER judgment",
        "Set it well above warn_per_hr, at the reversal rate where the controlled variable "
        "visibly oscillates.",
        (4.0, 40.0),
        "Must be at or above warn_per_hr; a trend too coarse to resolve it caps severity at warn.",
    ),
    "deadband": _P(
        "% of stroke",
        "CAMBER judgment: ignores slow, legitimate modulation",
        "Set it above the output's normal step size on a stable loop (the median absolute "
        "sample-to-sample move) so that small corrections are not counted as reversals.",
        (1.0, 20.0),
    ),
    "gap_factor": _P(
        "- (multiple of the median sample interval)",
        "CAMBER judgment",
        "Raise it for a change-of-value trend whose intervals vary widely; lower it when logger "
        "outages are short but real.",
        (1.5, 10.0),
    ),
}

PARAM_DOCS["g36_afdd"] = {
    "heating_coil": _P(
        "flag",
        "CAMBER judgment: a missing heating-valve point is read as no heating coil",
        "Set it from the unit's schedule or drawings: true when the AHU has a heating coil whose "
        "valve is not trended (the rule then declines), false to ignore a mapped valve.",
        (False, True),
        "None = inferred from whether a heating-valve point is mapped.",
    ),
    "mat_sat_as_coil_temps": _P(
        "flag",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (MAT/SAT may stand in for the coil "
        "temperatures, depending on the AHU configuration)",
        "Leave it on for an AHU with no heating coil between the mixed- and supply-air sensors; "
        "turn it off when another component (a preheat coil, an energy wheel) sits between them.",
        (False, True),
    ),
    "min_oa_pct": _P(
        "% of supply airflow (OA fraction)",
        "CAMBER judgment: the design minimum is a building property",
        "Take it from the design minimum OA over design airflow, or measure the OA fraction at "
        "minimum damper position on a known-good fan-on period.",
        (0.0, 100.0),
        "None = FC6 is declined. G36 uses the active minimum-OA setpoint over actual airflow.",
    ),
    # ---- begin 0100-ddahu (#97): FC6 against a seasonal minimum ----
    "min_oa_pct_by_month": _P(
        "% of supply airflow, per month",
        "CAMBER judgment: only for a sequence with a seasonal minimum (as outdoor_air_fraction "
        "takes it); G36 judges %OA against the active minimum-OA setpoint, which a seasonal "
        "sequence changes",
        "Enter the sequence's minimum for each month that differs from min_oa_pct, e.g. "
        "{6: 11.9, 7: 11.9, 8: 11.9} on lbnl-ddahu (a 28 % damper minimum in Jun-Aug, 45 % "
        "otherwise), and use the same values outdoor_air_fraction uses.",
        (0.0, 100.0),
        "None = min_oa_pct all year. A {month (1-12): pct} mapping; it needs min_oa_pct, the "
        "minimum in the other months. Each value must lie in the range.",
    ),
    # ---- end 0100-ddahu (#97) ----
    "mode_delay_min": _P(
        "min",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (ModeDelay; verified against Addendum p)",
        "Keep the G36 value; lengthen it only if trends show the unit still settling after a "
        "fan start or mode change beyond 30 minutes.",
        (0.0, 120.0),
    ),
    "alarm_delay_min": _P(
        "min",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (AlarmDelay; verified against Addendum p)",
        "Keep the G36 value; it should be several sample intervals long so that a fault must "
        "persist across more than one sample.",
        (0.0, 120.0),
    ),
    "avg_window_min": _P(
        "min",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (five-minute rolling averages)",
        "Keep the G36 value; on trends coarser than 5 minutes the window holds a single sample.",
        (1.0, 30.0),
    ),
    "econ_damper_open": _P(
        "% of damper stroke (OA damper)",
        "CAMBER judgment: separates G36 OS#3 (mechanical cooling on 100 % OA) from OS#4",
        "Set it just below the position the OA damper holds when the sequence calls for full "
        "economizer (its 5th percentile on such hours).",
        (50.0, 100.0),
    ),
    "valve_thr": _P(
        "% of valve stroke",
        "CAMBER judgment: a noise deadband for 'coil active'",
        "Set it just above the valve command's reading when commanded closed (its 95th "
        "percentile on idle hours).",
        (0.0, 15.0),
    ),
    # ---- begin 098-fc9 (#94): free cooling needs the economizer open beyond its minimum ----
    "oa_damper_min": _P(
        "% of damper stroke (OA damper)",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (operating-state definitions: free cooling "
        "is the economizer modulating above its minimum position, and heating (0.99, #95) runs "
        "with the OA damper at it); the position itself is the unit's own, set by its minimum "
        "outdoor-air control",
        "Set it to the minimum position from the unit's sequence or balancing report, or read "
        "the OA damper command on mechanical-cooling hours with the economizer locked out (its "
        "median). The finding's oa_damper_min and oa_damper_min_source show what was used.",
        (0.0, 60.0),
        "None = learned: the median OA damper command over fan-on hours of mechanical cooling "
        "below econ_damper_open (the OS#4 position); with fewer than 24 such intervals, 0 % "
        "(closed). lbnl-sdahu learns 10 %, its documented fixed minimum; lbnl-ddahu learns 28 % "
        "(its summer position; its template sets the seasonal minimum, 45 % with "
        "oa_damper_min_by_month 28 % in Jun-Aug, since 0.101). "
        "The same minimum splits OS#2 (beyond it) and OS#1 (at it) since 0.99.",
    ),
    # ---- begin 0101-g36-seasonal (#105): a seasonal minimum damper position ----
    "oa_damper_min_by_month": _P(
        "% of damper stroke (OA damper), per month",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 (operating-state definitions, as for "
        "oa_damper_min); CAMBER judgment: only for a sequence whose minimum position changes "
        "with the season, as min_oa_pct_by_month does for FC6",
        "Enter the sequence's minimum position for each month that differs from oa_damper_min, "
        "e.g. oa_damper_min 45 with {6: 28, 7: 28, 8: 28} on lbnl-ddahu (inventory section "
        "1.2(ii)). Check it against the damper command on hours the unit holds its minimum.",
        (0.0, 60.0),
        "None = oa_damper_min all year. A {month (1-12): position} mapping; it needs "
        "oa_damper_min, the position in the other months, so it never combines with a learned "
        "minimum. Each value must lie in the range. The finding reports it as "
        "oa_damper_min_by_month.",
    ),
    # ---- end 0101-g36-seasonal (#105) ----
    "oa_damper_tol": _P(
        "percentage points of damper stroke",
        "CAMBER judgment (#94): a noise margin above the minimum position; G36 gives no damper "
        "tolerance. On fault-free lbnl-sdahu the idle economizer hours sit at 47 % or more and "
        "the minimum at 10 %, so any margin up to 30 points classifies them the same. Since 0.99 "
        "(#95) the same margin decides whether a heating hour is at minimum OA (OS#1).",
        "Set it above the scatter of the damper command while it holds its minimum (the spread "
        "of the command on mechanical-cooling hours at minimum OA); raise it for a G36 unit whose "
        "minimum position moves with airflow.",
        (0.0, 20.0),
    ),
    "occupancy_gate": _P(
        "choice",
        "standard: ASHRAE Guideline 36-2021 §5.16.14 suspends AFDD only while the AHU is not "
        "operating (and for ModeDelay after a zone-group mode change), so unoccupied operation "
        "is evaluated by default. On the fault-free lbnl-sdahu run the unoccupied FC9 false "
        "alarm came from the free-cooling misreading, not from evaluating unoccupied hours: "
        "with OS#2 fixed, 'trended' changes no verdict on any lbnl-sdahu or lbnl-ddahu run.",
        "Keep 'off'. Use 'trended' to screen occupied operation only, when the unit trends an "
        "occupied/unoccupied point and its unoccupied runs (setback, purge) are out of scope.",
        ("off", "trended"),
        "'off' (default): every fan-on hour outside ModeDelay. 'trended': only the hours the "
        "trended occupancy point marks occupied; with none trended, every fan-on hour (no "
        "assumed-schedule fallback). The finding's occupancy_gate metric reports 'off', "
        "'trended occupancy' or 'none trended (fan-on hours only)', and unoccupied_hours the "
        "fan-on hours the gate left out.",
    ),
    # ---- end 098-fc9 ----
    "warn_pct": _P(
        "% of an FC's applicable intervals",
        "CAMBER judgment: screening-grade severity, not from G36 (G36 alarms every confirmed "
        "fault)",
        "Read each FC's pct on units known to be healthy; set the warn level above the spread. "
        "Tuning it on the unit under test is circular.",
        (1.0, 30.0),
    ),
    "fault_pct": _P(
        "% of an FC's applicable intervals",
        "CAMBER judgment: screening-grade severity, not from G36",
        "Set it well above warn_pct.",
        (5.0, 60.0),
        "Must be at or above warn_pct.",
    ),
    "min_applicable_hours": _P(
        "h",
        "CAMBER judgment: screening-grade, not from G36",
        "Raise it for long histories so that a rare operating state with a few hours of "
        "applicability cannot set the severity; each FC's applicable_hours shows its base.",
        (1.0, 500.0),
    ),
}

PARAM_DOCS["airflow_tracking"] = {
    "tol_frac": _P(
        "fraction of the airflow setpoint",
        "CAMBER judgment",
        "On a known-good period take the 95th percentile of |flow - setpoint| / setpoint "
        "(mean_abs_rel_error summarises it) and set the tolerance just above it; flow sensors "
        "read poorly at low flow.",
        (0.05, 0.5),
    ),
    "min_sp": _P(
        "cfm (the airflow setpoint's unit)",
        "CAMBER judgment: a divide-by-zero guard",
        "Raise it to the box's minimum-flow setpoint (or a small fraction of design flow) to "
        "leave out samples where the relative error of a tiny setpoint is noise.",
        (0.0, 500.0),
    ),
    "warn_pct": _P(
        "% of active samples (setpoint above min_sp)",
        "CAMBER judgment",
        "Read off_setpoint_pct on boxes known to be healthy; set the warn level above their "
        "spread.",
        (2.0, 30.0),
    ),
    "fault_pct": _P(
        "% of active samples (setpoint above min_sp)",
        "CAMBER judgment",
        "Set it well above warn_pct.",
        (10.0, 60.0),
        "Must be at or above warn_pct.",
    ),
}

# ==== end air block ====


# ==== begin zones block (zones and terminal units, the cohort and T&R census instances) ====

# Zone / terminal-box rules and the fleet reset-request family (0.98, #90).

_ZONE_REHEAT_SAT = {
    "reheat_saturated_pct": _P(
        "% valve open",
        "CAMBER judgment: a valve at or above 90 % is treated as fully open (heating maxed out)",
        "Read the reheat valve command in known cold, fully-heating hours on healthy boxes; set "
        "this just below the level those valves actually reach (some controllers top out at "
        "95-98 %, not 100 %).",
        (75.0, 100.0),
        "Shared by overcooling_severity (sets these samples aside as a heating shortfall) and "
        "reheat_capacity_shortfall (reports them as its finding); keep the two equal.",
    ),
}

PARAM_DOCS["reheat_penalty"] = {**_SCHEDULE}
# ==== begin 098-terminal-reheat (#85 item 3) ====
PARAM_DOCS["reheat_penalty"]["fan_heat_f"] = _P(
    '°F, or "auto"',
    "CAMBER judgment (0.98, #85): a fan-powered box's own fan, and in a parallel box the plenum "
    "air it mixes in, lifts the discharge above the entering primary air with the valve shut; on "
    "the LBNL fan-powered boxes the discharge rose 6.0 °F (median, parallel box) and 8.7 °F "
    "(series box) over the entering air with the valve stuck shut, past the 5 °F no-rise bound, so "
    "a valve that delivered no heat looked corroborated",
    "Map the entering primary air (MIXED_AIR_TEMP) and read the discharge minus entering air on "
    "hours the valve is shut and the box moves air (fan on, where the fan status is trended). Set "
    'the typical lift, or use "auto" to estimate it per box from those hours. Single-duct '
    "boxes with no fan: leave it None.",
    ("auto", 0.0, 8.0),
    "None (default) adds nothing to the valve-vs-discharge bounds (5 °F no rise at full valve, "
    '10 °F big rise with the valve shut). A number raises both by that many °F. "auto" uses the '
    "median closed-valve, airflow-bearing lift over the entering air (>= 12 samples, fan-on only "
    "when SUPPLY_FAN_STATUS is mapped), clipped to 0-8 °F; without the entering air or enough "
    "samples it adds nothing. The finding reports the value used as fan_heat_f. box_type sets "
    'the "auto" caps per box type (0.100, #99).',
)
# ==== end 098-terminal-reheat ====
# ==== begin 0100-terminal (#99) ====
PARAM_DOCS["reheat_penalty"]["box_type"] = _P(
    "choice",
    "CAMBER judgment (0.100, #99), checked in-sample on the labelled LBNL fan-powered runs. A "
    "single-duct box has no fan, so its closed-valve lift is duct gains and sensor error (1-3 °F). "
    "A parallel box runs its fan in heating only, so its closed-valve lift was 0.0 °F on the "
    "fault-free run. A series box's fan always runs and mixes in plenum air, so its lift was "
    "13.7 °F. On the series runs, a 10 °F no-rise cap puts the 15 °F bound between the stuck "
    "valves' rise (12.9 °F at most) and the working full-valve rise (17.6 °F at least). Raising "
    "the big-rise cap past 8 °F lost the passing-valve caveat on the 50 % and 80 % leaks.",
    "Declare it from the box schedule or the submittals: series fan-powered (fan runs whenever "
    "the zone is occupied), parallel fan-powered (fan runs in heating), or single-duct (no "
    'fan). It only matters with fan_heat_f "auto". On a site with several box types, use a '
    "separate run for each type.",
    ("single_duct", "parallel", "series"),
    'None (default) caps the "auto" estimate at 8 °F on both bounds, as before 0.100. '
    '"single_duct" caps it at 3 °F and "parallel" at 8 °F, on both bounds. "series" caps the '
    "no-rise allowance (valve open) at 10 °F and keeps the big-rise allowance (valve shut) at "
    "8 °F: the estimate is learned from the closed-valve samples, so a higher cap there would "
    "absorb a passing valve's heat. A numeric fan_heat_f is used as given. With box_type set, "
    "the finding also reports box_type, fan_lift_f (the uncapped estimate) and "
    "fan_heat_closed_f.",
)
# ==== end 0100-terminal ====
PARAM_DOCS["overcooling_min_flow"] = {**_SCHEDULE}

PARAM_DOCS["overcooling_severity"] = {
    **_SCHEDULE,
    **_ZONE_REHEAT_SAT,
    "tiers": _P(
        "°F below the reference setpoint, per key ('info', 'warn', 'fault')",
        "CAMBER judgment: info 1 / warn 2 / fault 3 °F below the reference, described in code as "
        "Std-55-aligned (no section cited)",
        "Look at max_depth_f and median_depth_f on zones occupants do not complain about; set "
        "the warn tier above the depth those zones sustain. Calibrating on the zones you are "
        "scoring would hide a building-wide overcooling problem.",
        (0.5, 6.0),
        "None uses the defaults {'info': 1.0, 'warn': 2.0, 'fault': 3.0}. A dict with all three "
        "keys, each a depth in °F, mildest first; the info tier is never counted as a fault.",
    ),
    "window_min": _P(
        "min",
        "CAMBER judgment: an excursion must persist an hour to count",
        "Lengthen it if short excursions (door openings, load swings) raise findings on zones "
        "that are comfortable in practice; keep it at or above the trend interval, since one "
        "sample at a coarser interval already satisfies it.",
        (15.0, 240.0),
    ),
    "relative_to_deadband": _P(
        "flag",
        "CAMBER judgment: a space inside the heating-cooling deadband is operating as designed",
        "Leave it on when both setpoints are trended; turn it off only to measure depth below "
        "the cooling setpoint deliberately (it over-flags a healthy deadband).",
        (False, True),
        "With no heating setpoint the rule falls back to the cooling setpoint and caps the "
        "result at warn.",
    ),
    "recovery_hours": _P(
        "h",
        "CAMBER judgment: morning recovery from setback is not overcooling",
        "Measure how long zones take to reach setpoint after occupied mode starts on a cold "
        "morning (median across healthy zones) and use that. Used only when no WARMUP point is "
        "mapped.",
        (0.0, 4.0),
    ),
    "shortfall_share_pct": _P(
        "% of considered (occupied, fan-on) samples, per key ('warn', 'fault')",
        "CAMBER judgment: the same 5 % / 20 % warn / fault shares reheat_capacity_shortfall uses; "
        "a heating shortfall a few hours a year is a weather extreme, not a capacity fault (#85: "
        "a stuck-open damper's 0.81 % shortfall graded fault on depth alone)",
        "Run the rule on a period you know the zone was comfortable and look at "
        "shortfall_warn_pct (the share of samples in a sustained shortfall at least the warn "
        "depth deep) across your zones: set 'warn' above the healthy zones' spread (their p95) "
        "and 'fault' where a zone's cold hours become a standing complaint. "
        "shortfall_depth_severity shows the depth-only grade for comparison.",
        (0, 100),
        "The shortfall grade is the lesser of the depth tier and the share tier (share below "
        "'warn' -> info). None grades by depth alone (the pre-0.98 behaviour). Each key's range "
        "is 0-100 with warn <= fault.",
    ),
    "share_pct": _P(
        "% of considered (occupied, fan-on) samples, per key ('warn', 'fault')",
        "CAMBER judgment: opt-in; overcooling stays graded by depth x duration alone by default "
        "so existing verdicts don't move",
        "Set it when brief deep overcooling (a few cold mornings) should not rate warn/fault: "
        "look at warn_pct on zones you consider fine and set 'warn' above their spread; "
        "{'warn': 5, 'fault': 20} mirrors the shortfall gate.",
        (0, 100),
        "None (default) = depth alone. When set, the overcooling grade is the lesser of the depth "
        "tier and the share tier from warn_pct (share below 'warn' -> info). The no-heating-"
        "setpoint cap (fault -> warn) still applies after it.",
    ),
}

PARAM_DOCS["reheat_minimization_g36"] = {
    "valve_thr": _P(
        "% valve open",
        "CAMBER judgment: the dual-max logic follows ASHRAE Guideline 36 §5.6.5; this value "
        "is not from G36",
        "Set it just above the valve command a closed valve reads in trends (leak-by or offset "
        "readings of a few percent), so that only real reheat counts.",
        (1.0, 20.0),
        "The warn and fault levels (15 % and 40 % of reheating hours above minimum flow) are "
        "fixed in code.",
    ),
    "flow_margin": _P(
        "- (multiple of the airflow setpoint)",
        "CAMBER judgment: 20 % headroom over the minimum-flow setpoint before airflow counts as "
        "above minimum",
        "On healthy boxes at minimum flow, take the 95th percentile of airflow / airflow "
        "setpoint; set the margin just above it so sensor noise and tracking error do not count "
        "as violations.",
        (1.05, 2.0),
    ),
}

PARAM_DOCS["reheat_capacity_shortfall"] = {
    **_SCHEDULE,
    **_ZONE_REHEAT_SAT,
    "heat_sp_f": _P(
        "°F",
        "CAMBER judgment: no default; the zone heating setpoint must come from data or config",
        "Enter the zone heating setpoint from the BAS program or the sequence of operations; "
        "use a per-box mapping when boxes differ.",
        (60.0, 75.0),
        "None means use the trended HEAT_SP; without it the rule declines. One value for every "
        "box, or a {equip: °F} mapping. A trended HEAT_SP always wins.",
    ),
    "tol_f": _P(
        "°F",
        "CAMBER judgment: the unmet_setpoint_hours tolerance (1.5 °F)",
        "Set it above the normal control swing of healthy zones below their heating setpoint "
        "(a high percentile of setpoint minus zone temperature while heating). Keep it equal "
        "to unmet_setpoint_hours' tol_F.",
        (0.5, 4.0),
    ),
    "warn_pct": _P(
        "% of evaluated occupied samples",
        "CAMBER judgment: screening-grade, not a standard's (per the module docstring)",
        "Compare shortfall_pct across boxes known to heat adequately; set warn above the "
        "highest of those.",
        (1.0, 20.0),
        "Applies only once min_hours of shortfall have accumulated.",
    ),
    "fault_pct": _P(
        "% of evaluated occupied samples",
        "CAMBER judgment: screening-grade, not a standard's (per the module docstring)",
        "Keep it well above warn_pct; raise it if seasonal design-day hours alone push healthy "
        "boxes past it.",
        (5.0, 60.0),
        "Applies only once min_hours of shortfall have accumulated.",
    ),
    "min_hours": _P(
        "h",
        "CAMBER judgment: screening-grade, not a standard's (per the module docstring)",
        "Raise it for long records so a few cold mornings cannot grade a box; lower it for a "
        "short test window. Compare with the finding's shortfall_hours.",
        (1.0, 100.0),
    ),
    "recovery_hours": _P(
        "h",
        "CAMBER judgment: warm-up after occupancy starts or a setpoint step-up is expected to "
        "run the valve wide open below setpoint",
        "Measure how long healthy boxes take to reach the heating setpoint after occupancy "
        "starts on a cold morning (median across boxes) and use that.",
        (0.0, 4.0),
        "Applied after each occupied-period start and each heating-setpoint step up, in "
        "addition to trended WARMUP samples.",
    ),
}

PARAM_DOCS["night_weekend_setback"] = {
    **_SCHEDULE,
    "min_unoccupied_run_pct": _P(
        "% of unoccupied time",
        "CAMBER judgment: a unit running less than this unoccupied is not missing its setback "
        "(#57)",
        "Read fan_run_unoccupied_pct on units you know are scheduled off; set the floor just "
        "above the scattered runtime they show (freeze protection, morning starts).",
        (0.0, 20.0),
    ),
    "unoccupied_heat_sp_f": _P(
        "°F",
        "CAMBER judgment: no default; the unoccupied heating setpoint comes from data or config",
        "Enter the unoccupied (setback) heating setpoint from the BAS program.",
        (45.0, 68.0),
        "None means use HEAT_SP read in unoccupied hours; without either, the held-setback "
        "test compares the zone with its own occupied temperature.",
    ),
    "unoccupied_cool_sp_f": _P(
        "°F",
        "CAMBER judgment: no default; the unoccupied cooling setpoint comes from data or config",
        "Enter the unoccupied (set-up) cooling setpoint from the BAS program.",
        (78.0, 95.0),
        "None means use COOL_SP read in unoccupied hours; the cooling side of the held-setback "
        "test always needs a known unoccupied setpoint.",
    ),
    "min_setback_depth_f": _P(
        "°F",
        "CAMBER judgment: a held setback sits at least 3 °F from occupied comfort",
        "Take the difference between the occupied and unoccupied setpoints in the BAS program "
        "and set this somewhat below it.",
        (1.0, 10.0),
        "Also the minimum gap required between a known unoccupied setpoint and the occupied "
        "reference.",
    ),
    "max_hold_duty_pct": _P(
        "% of the unoccupied hours the fan runs",
        "CAMBER judgment: a fan at or above 90 % duty in the hours it runs is running through, "
        "not cycling",
        "On a unit known to hold a setback, read its duty in the night hours it runs on the "
        "coldest nights; set this above that.",
        (50.0, 100.0),
        "Needs sub-hourly fan status; a fan that runs whole hours never counts as cycling.",
    ),
}

PARAM_DOCS["zones_heat_cool_census"] = {**_SCHEDULE}

PARAM_DOCS["unmet_setpoint_hours"] = {
    "start_hour": _SCHEDULE["start_hour"],
    "end_hour": _SCHEDULE["end_hour"],
    "tol_F": _P(
        "°F",
        "CAMBER judgment",
        "Set it above the normal control swing of healthy zones around their setpoints (a high "
        "percentile of the excursion beyond setpoint in comfortable hours).",
        (0.5, 4.0),
        "Keep it equal to reheat_capacity_shortfall's tol_f.",
    ),
    "warn_pct": _P(
        "% of occupied samples",
        "CAMBER judgment",
        "Compare unmet_pct across zones without comfort complaints; set warn above the highest "
        "of those.",
        (1.0, 20.0),
    ),
    "fault_pct": _P(
        "% of occupied samples",
        "CAMBER judgment",
        "Keep it well above warn_pct; the rule counts a symptom, so raise it where design-day "
        "hours alone push healthy zones past it.",
        (5.0, 50.0),
    ),
}

PARAM_DOCS["damper_census"] = {
    "occupancy_gate": _P(
        "choice",
        "CAMBER judgment (#84): the census takes each box's median damper over occupied hours; "
        "an assumed weekday schedule returned 'no damper data' on the ornl-frp-vav weekend test "
        "days although every box trends the tests' 07:00-22:00 every-day occupancy. With the "
        "trended occupancy those days get a census (d3_stuck_000 36.8 %, d3_stuck_060 40.3 %, "
        "d3_stuck_100 41.8 % fleet median) and the fault-free day moves 39.3 -> 38.7 % (same "
        "verdict).",
        "Keep 'trended' when the boxes (or their zones) trend occupancy. Run once with "
        "'schedule' and compare median_damper_pct: a large difference means the building's "
        "real hours differ from the weekday office window, and the trended result is the one "
        "to trust. Use 'off' only for a 24/7 space whose boxes never shut off: night shut-off "
        "samples pull every median down toward 'throttling'.",
        ("trended", "schedule", "off"),
        "'trended' (default): each box's occupied samples from its own trended occupancy "
        "point (any non-null value), else the assumed weekday 07-18 schedule. 'schedule': "
        "always the schedule (the pre-0.98 behaviour). 'off': every sample. Trended warm-up / "
        "cool-down flags drop prep-mode samples under 'trended' and 'schedule'. The finding's "
        "occupancy_gate metric (and DamperCensusResult.occupancy_gate) reports 'trended "
        "occupancy', 'assumed schedule (weekdays 07-18)', 'mixed' (some boxes each way) or "
        "'off'.",
    ),
}
FIXED["damper_census"] = (
    "a box is throttling when its median occupied damper is < 50 % and starved when >= 90 %; "
    "fault when >= 60 % of boxes throttle or >= 25 % are starved, warn when < 50 % of boxes lie "
    "in band; the schedule fallback is the weekday 07:00-18:00 window; fixed in code "
    "(camber/rules/static_rule.py, camber/staticpressure.py), not yet constructor parameters"
)

_ZONE_COHORT = {
    "k": _P(
        "- (robust z, MAD-scaled)",
        "CAMBER judgment: the common 3.5 modified-z outlier cutoff (not cited in the cohort code)",
        "Run it on a cohort you believe healthy and read the largest |z|; set k above it. "
        "Lower k finds more outliers but flags normal spread in small cohorts.",
        (2.0, 6.0),
    ),
    "min_cohort": _P(
        "count",
        "CAMBER judgment: a median and MAD need at least three peers",
        "Raise it when the cohort mixes unlike units; small cohorts give unstable z-scores.",
        (3, 50),
    ),
    "summary": _P(
        "choice",
        "CAMBER judgment: the mean is the most stable summary",
        "Use peak to compare maxima (sizing), load_factor (mean / peak) to compare how units "
        "cycle, variability (the standard deviation; 0.98, #85) to find a unit that never moves "
        "(pair it with tail='low').",
        ("mean", "peak", "load_factor", "variability"),
    ),
}

PARAM_DOCS["cohort_airflow"] = {**_ZONE_COHORT}
PARAM_DOCS["cohort_space_temp"] = {**_ZONE_COHORT}

# The reset-effectiveness fractions are fractions of the T&R band (sp_max - sp_min of the preset:
# 10 °F for SAT, 1.4 in. w.c. for static), so their meaning and range are the same for both.
_ZONE_RESET_EFF = {
    "min_cycles": _P(
        "count (T&R cycles)",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Raise it for coarse trends; fewer usable rows than this and the rule declines.",
        (6, 500),
    ),
    "flat_frac": _P(
        "fraction of the reset band",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "On a reset known to work, take the setpoint's range in a quiet period; keep flat_frac "
        "well below that range divided by the band.",
        (0.02, 0.3),
        "stuck = actual range <= flat_frac x band while the expected range >= "
        "expected_move_frac x band.",
    ),
    "expected_move_frac": _P(
        "fraction of the reset band",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Keep it well above flat_frac so a flat setpoint is called stuck only when the requests "
        "clearly demanded movement.",
        (0.1, 0.8),
    ),
    "pinned_frac": _P(
        "fraction of the reset band",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Set it to cover the setpoint's normal jitter around an end of its range on a working "
        "reset.",
        (0.05, 0.3),
    ),
    "mode_frac": _P(
        "fraction of demand (or idle) cycles",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Read pct_unresponsive and pct_untrimmed on a reset known to work; set mode_frac "
        "above both.",
        (0.3, 0.95),
    ),
    "min_mode_cycles": _P(
        "count (T&R cycles)",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Raise it on long records to judge the not-responding and not-trimming modes on more "
        "evidence.",
        (5, 200),
    ),
    "wrong_dir_frac": _P(
        "fraction of cycles T&R commanded a move",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Raise it when a coarser trend cadence than the controller makes moves look reversed "
        "on a reset known to work.",
        (0.3, 0.9),
    ),
}

PARAM_DOCS["sat_reset_effectiveness"] = {**_ZONE_RESET_EFF}
PARAM_DOCS["static_reset_effectiveness"] = {**_ZONE_RESET_EFF}

_ZONE_GROUPS_EXEMPT = {
    "groups": "explicit zone -> air-handler membership ({zone: ahu} or a callable), a "
    "structural input; None uses the served-by topology or a building-wide pool"
}

# The census knobs act on per-zone request counts, whatever reset produces them (SAT requests
# from zone temperature vs cooling setpoint, static requests from airflow and damper), so they
# mean the same for both kinds. The request thresholds themselves are fixed in code.
_ZONE_REQ_GATE = {
    "min_active_cycles": _P(
        "count (request cycles)",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Raise it for long or fine-grained records; zones with fewer usable system-on cycles "
        "are reported unevaluable.",
        (5, 500),
        "The cohort-starvation rules also need this many active cycles in a group before "
        "calling it starved.",
    ),
}

_ZONE_ROGUE = {
    **_ZONE_REQ_GATE,
    "dominance_frac": _P(
        "fraction of active cycles",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "On a group with no known rogue, read the highest zone binding fraction; set this "
        "above it.",
        (0.2, 0.9),
    ),
    "share_mult": _P(
        "- (multiple of an equal share)",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "A rogue needs share >= max(min_share, share_mult / n_zones); raise it to demand a "
        "more lopsided share in groups with many zones.",
        (1.2, 5.0),
    ),
    "min_share": _P(
        "fraction of the group's requests",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "On a healthy group, read the largest zone share of requests; set this above it.",
        (0.1, 0.8),
    ),
    "min_zones_per_group": _P(
        "count",
        "CAMBER judgment: a rogue needs at least one sibling to be compared with",
        "Raise it when small groups flag zones that merely serve a hotter space.",
        (2, 20),
    ),
}

PARAM_DOCS["sat_rogue_zone_census"] = {**_ZONE_ROGUE}
PARAM_DOCS["static_rogue_zone_census"] = {**_ZONE_ROGUE}
EXEMPT["sat_rogue_zone_census"] = {**_ZONE_GROUPS_EXEMPT}
EXEMPT["static_rogue_zone_census"] = {**_ZONE_GROUPS_EXEMPT}

_ZONE_STARVE = {
    **_ZONE_REQ_GATE,
    "cohort_frac": _P(
        "fraction of the group's zones",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "On a healthy air handler at peak load, read the largest share of its zones requesting "
        "at once; set this above it.",
        (0.5, 1.0),
    ),
    "sustained_frac": _P(
        "fraction of active cycles",
        "CAMBER judgment: screening-grade (provisional-untuned)",
        "Read group_sustained_frac on healthy air handlers over a hot week; set this above it. "
        "For SAT, corroborate with OAT: a hot design day can starve a healthy cohort.",
        (0.2, 0.9),
    ),
    "min_zones_per_group": _P(
        "count",
        "CAMBER judgment: a common-mode pattern needs a cohort, not a pair",
        "Raise it so that a few zones requesting together are not read as a whole starved "
        "air handler.",
        (3, 20),
    ),
}

PARAM_DOCS["sat_cohort_starvation"] = {**_ZONE_STARVE}
PARAM_DOCS["static_cohort_starvation"] = {**_ZONE_STARVE}
EXEMPT["sat_cohort_starvation"] = {**_ZONE_GROUPS_EXEMPT}
EXEMPT["static_cohort_starvation"] = {**_ZONE_GROUPS_EXEMPT}

# ==== begin 098-terminal-stuck (#85 items 1-2) ====
# The cohort rules' opt-in options (every default reproduces the pre-0.98 result).
_ZONE_COHORT_OPTIONS = {
    "group_by_topology": _P(
        "flag",
        "CAMBER judgment (0.98, #85): boxes behind different air handlers (or, in a test "
        "dataset, on different days) are not peers",
        "Turn it on when the building has more than one air handler, or when the equipment ids "
        "mix runs or days. A served-by model (Brick/Haystack) groups exactly; otherwise the "
        "naming heuristic groups by id and the finding says so.",
        (False, True),
        "Groups smaller than min_cohort are left unscored (unscored_small_groups).",
    ),
    "normalise": _P(
        "choice",
        "CAMBER judgment (0.98, #85): measured on the ORNL test building (one stuck box among "
        "ten, one day per position): the share of design airflow flagged one stuck day of six "
        "and six healthy box-days; each box against its own fault-free day flagged all six",
        "Use 'reference' when every unit has a known-good period or twin (declare it in "
        "reference); use 'design_max' to even out box sizes (declare design_max, or map "
        "AIRFLOW_SP). Size normalisation alone cannot isolate a stuck box.",
        ("reference", "design_max"),
        "None (default) compares the raw summary. 'reference' divides each unit's summary by "
        "its reference unit's (reference units are not scored); 'design_max' divides by the "
        "unit's design airflow (design_max, else the peak AIRFLOW_SP). Units with no "
        "denominator are left out (left_out).",
    ),
    "tail": _P(
        "choice",
        "CAMBER judgment (0.98, #85): a stuck damper's variability is only ever low, while a "
        "healthy box on a busy day is high",
        "Use 'low' with summary='variability' to look for units that never move, 'high' for "
        "units that move or run more than their peers.",
        ("both", "low", "high"),
    ),
}
for _name in ("cohort_airflow", "cohort_space_temp"):
    PARAM_DOCS[_name].update(_ZONE_COHORT_OPTIONS)
    EXEMPT[_name] = {
        "reference": "structured input: {equip: reference_equip} for normalise='reference', "
        "one entry per unit (described under normalise)",
        "design_max": "structured input: {equip: design airflow} for normalise='design_max', "
        "one entry per unit (described under normalise)",
    }

PARAM_DOCS["actuator_stuck"] = {
    **_SCHEDULE,
    "roles": _P(
        "role names (a list)",
        "CAMBER judgment (0.98, #85): the box and fan-coil actuators a zone's demand drives",
        "Leave out a role whose point is a command echo rather than a position (a flat echo says "
        "nothing about the actuator). heat_valve_position falls back to heat_valve when no "
        "position is mapped.",
        ("damper", "heat_valve_position", "heat_valve", "cool_valve"),
        "A list; each must be one of the range values. An air handler's outdoor-air damper is "
        "out of scope.",
    ),
    "tol_pct": _P(
        "%",
        "CAMBER judgment (0.98, #85): rounds away the sub-percent jitter of a 0.1 % resolution "
        "position trend",
        "Read a known-stuck or manually held actuator's trend: set this above the jitter it "
        "shows while still. Larger values join slow real movement into one run.",
        (0.0, 5.0),
    ),
    "min_flat_hours": _P(
        "h",
        "calibrated on ornl-frp-vav (default subset, 15-minute data): the box under test's longest "
        "flat run on its fault-free day is under 3 h, while healthy neighbours hold one "
        "mid-stroke position (26-41 %) for 4-10.5 h on 13 box-days -- so length alone does not "
        "decide, and 4 h is the shortest run judged",
        "Read the longest flat runs of healthy boxes in a typical week; set it at or above the "
        "run length you are willing to call 'held'.",
        (1.0, 24.0),
    ),
    "whole_day_share": _P(
        "fraction of a day's active samples",
        "calibrated on ornl-frp-vav: the healthy boxes' longest flat runs cover at most 70 % of a "
        "day's occupied samples on the default subset and 95 % on the full one (room 102 on an "
        "airflow-test day); a stuck box covers 100 %",
        "Read the share of each day's occupied samples that healthy boxes' longest run covers, "
        "and set it above the largest.",
        (0.8, 1.0),
        "Only the unexplained-flat tier (warn at most) uses it.",
    ),
    "limit_pct": _P(
        "% (from either end of the stroke)",
        "CAMBER judgment (0.98, #85): a position within 2 % of 0 or 100 reads as at its limit",
        "Read where healthy actuators sit when fully shut or fully open (some never read exactly "
        "0 or 100); set it just beyond that offset.",
        (0.0, 10.0),
    ),
    "min_driver_span_f": _P(
        "°F",
        "CAMBER judgment (0.98, #85): one degree of zone-temperature or setpoint movement over a "
        "day is a demand a modulating actuator should answer",
        "Raise it in a zone with a very stable load, so a held mid-stroke position on a calm day "
        "is not called unexplained.",
        (0.5, 5.0),
    ),
    "warm_margin_f": _P(
        "°F",
        "CAMBER judgment (0.98, #85): the zone over its cooling setpoint (or, for a heating "
        "valve, under its heating setpoint) by more than 1 °F is a demand the actuator ignored",
        "Set it to the zone loop's normal overshoot: read how far healthy zones run over their "
        "cooling setpoint in a hot afternoon.",
        (0.5, 5.0),
        "The zone must be out by this much for at least 25 % of the run (fixed in code).",
    ),
    "satisfied_margin_f": _P(
        "°F",
        "CAMBER judgment (0.98, #85): a fully open damper with the zone 2 °F below its cooling "
        "setpoint is delivering cooling nobody asked for",
        "Read how far below the cooling setpoint healthy zones sit while their boxes are fully "
        "open (normally they do not); set it above that.",
        (1.0, 6.0),
        "The zone must be this far inside for at least 50 % of the run (fixed in code).",
    ),
    "min_airflow": _P(
        "cfm (the trended airflow's unit)",
        "CAMBER judgment (0.98, #85): with no airflow setpoint trended, a closed damper is judged "
        "against the box's minimum airflow when one is given",
        "Set the box's scheduled minimum (occupied) airflow from the design or the controller. "
        "A closed damper is contradicted when the airflow is at or below 5 % of it.",
        (0.0, 5000.0),
        "None (default): AIRFLOW_SP when mapped; with neither, a damper shut through occupied "
        "hours is judged against the occupied mode alone, and the finding carries a caveat.",
    ),
    "warn_pct": _P(
        "% of active samples",
        "CAMBER judgment (0.98, #85): a tenth of the occupied samples held against demand",
        "Lower it to catch one stuck day in a long window; raise it to report only persistent "
        "faults.",
        (0.0, 100.0),
        "warn_pct <= fault_pct. Flagged runs of either tier count.",
    ),
    "fault_pct": _P(
        "% of active samples",
        "CAMBER judgment (0.98, #85): half the occupied samples held against the zone's demand",
        "As warn_pct. Only contradicted runs reach fault; an unexplained flat run is warn at most.",
        (0.0, 100.0),
    ),
}
# ==== end 098-terminal-stuck ====

# ==== end zones block ====


# ==== begin vent block (ventilation and IAQ) ====

# ---- ventilation: co2_ventilation, co2_ventilation_system, dcv_verification,
# ---- dcv_system_verification (camber/rules/iaq_rule.py, camber/rules/ventilation_rule.py)

PARAM_DOCS["co2_ventilation"] = {
    "exclude_economizer": _P(
        "flag",
        "CAMBER judgment: economizer outdoor air is free cooling, not over-ventilation (0.93, #38)",
        "Leave it on wherever the unit economizes. Turn it off only to see the all-hours "
        "over-ventilation share, which is also reported as over_vent_all_pct when it is on.",
        (False, True),
        "Affects only the over-ventilation verdict; under-ventilation is judged on every "
        "occupied hour either way. It has no effect on a frame with no ECON_CMD and no OAT with "
        "an OA damper or mixed/return temperatures.",
    ),
    "oa_damper_min_pct": _P(
        "% open (OA damper position)",
        "CAMBER judgment: None derives the minimum from the damper's own trend",
        "Set it to the minimum-OA damper position from the BAS sequence or the TAB report. To "
        "read it from trends, take the 5th percentile of the damper while the fan runs and the "
        "damper is open, over a known-good period with no economizing (hot or cold weather).",
        (0.0, 60.0),
        "None: the 5th percentile of the damper while open (and the fan on, when trended). An "
        "hour counts as economizing only when the damper sits more than 5 points above this "
        "minimum and the OAT is below econ_high_limit_f. Used only without an ECON_CMD point.",
    ),
    "econ_high_limit_f": _P(
        "°F (outdoor air temperature)",
        "standard: ASHRAE 90.1 -- the top of its fixed dry-bulb economizer high-limit range, as "
        "the code cites it (no section cited)",
        "Set it to the high-limit setpoint programmed in the economizer sequence. From trends, "
        "take the highest OAT at which the OA damper still opens beyond its minimum over a "
        "summer of known-good operation.",
        (55.0, 80.0),
        "Used only when economizer mode is inferred from OAT and the damper; a trended ECON_CMD "
        "overrides it. A lower value counts fewer hours as economizing.",
    ),
}

DELEGATES["co2_ventilation_system"] = "co2_ventilation"


# Occupied-hours schedule for dcv_verification. Unlike the shared _SCHEDULE wording, the
# fallback here is only a trended OCCUPANCY point (it replaces the schedule), and the
# schedule also gates the unoccupied-offset guard of dcv_system_verification.
_VENT_SCHEDULE_NOTE = (
    "Used only when the equipment frame carries no OCCUPANCY point: a trended OCCUPANCY point "
    "replaces the schedule (WARMUP / COOLDOWN are excluded either way). Ignored for the verdict "
    "with occupied_only=False. dcv_system_verification also uses it on each zone frame to find "
    "the unoccupied hours of its CO2-offset guard."
)

_VENT_SCHEDULE = {
    "start_hour": _P(
        "hour of day (0-23)",
        "CAMBER judgment: a typical weekday office schedule (07:00-18:00)",
        "Set it to the start of the building's occupied mode, read from the BAS schedule or "
        "from the hour the supply fan starts in a typical week of trends. Better still, map "
        "the OCCUPANCY point, which replaces the schedule.",
        (0, 23),
        _VENT_SCHEDULE_NOTE,
    ),
    "end_hour": _P(
        "hour of day (1-24)",
        "CAMBER judgment: a typical weekday office schedule (07:00-18:00)",
        "Set it to the end of occupied mode (exclusive), read from the BAS schedule or trends.",
        (1, 24),
        _VENT_SCHEDULE_NOTE,
    ),
    "occupied_days": _P(
        "weekday numbers (Mon=0 ... Sun=6)",
        "CAMBER judgment: a Monday-Friday schedule",
        "List the days the building runs occupied mode, e.g. [0, 1, 2, 3, 4, 5] for a Saturday "
        "schedule.",
        (0, 6),
        "A list of integers; each must lie in the range. " + _VENT_SCHEDULE_NOTE,
    ),
}

PARAM_DOCS["dcv_verification"] = {
    "min_modulation": _P(
        "fraction of the OA signal's p95 ((p95 - p5) / p95)",
        "CAMBER judgment",
        "On a unit with a known-fixed OA minimum, the robust range reported as `modulation` "
        "should sit below it; on one with working DCV, well above. Set it between the two. "
        "Damper position and cfm give different ranges, so tune per OA signal.",
        (0.02, 0.5),
        "Below it, with demand in the DCV range, the verdict is 'static' (warn).",
    ),
    "co2_setpoint": _P(
        "ppm (CO2)",
        "CAMBER judgment: a site input with no default -- it is the BAS DCV setpoint",
        "Read it from the DCV sequence (the CO2 setpoint of the reset loop or the top of its "
        "proportional band). Do not fit it from the CO2 trend.",
        (600.0, 1500.0),
        "None: the CO2-above-setpoint-at-minimum-OA sub-check is not evaluated, the engage "
        "level falls back to 800 ppm, and unventilated_co2_ppm falls back to 1100 ppm. When "
        "set, the engage level defaults to co2_setpoint - 200.",
    ),
    "occupied_only": _P(
        "flag",
        "CAMBER judgment: DCV is only responsible for OA while the space is occupied",
        "Leave it on. Turn it off only for a space occupied around the clock with no OCCUPANCY "
        "point, where the schedule would cut real occupied hours.",
        (False, True),
        "Off: every sample is judged (still only with the fan on and not economizing), and the "
        "unventilated-while-occupied check runs on all hours.",
    ),
    "dcv_engage_ppm": _P(
        "ppm (CO2)",
        "CAMBER judgment: DEFAULT_DCV_ENGAGE_PPM (800 ppm) when no setpoint is known",
        "Set it to the CO2 at which the sequence starts raising OA: the bottom of the "
        "proportional band, or about the setpoint for an integral loop.",
        (500.0, 1200.0),
        "None: co2_setpoint - 200 when co2_setpoint is set, else 800 ppm. Too few judged "
        "samples at or above it gives 'insufficient' (demand_below_engage).",
    ),
    "min_demand_span": _P(
        "ppm (CO2, p90 - p10 of the judged samples)",
        "CAMBER judgment",
        "Look at `demand_span` on units whose zones clearly fill and empty; it should sit above "
        "this. Lower it for lightly occupied spaces only if you accept a noisier verdict.",
        (50.0, 400.0),
        "Below it the verdict is 'insufficient' (no_demand_variation), never a fault.",
    ),
    "min_lift_ppm": _P(
        "ppm (CO2 with OA raised minus CO2 with OA at its floor)",
        "CAMBER judgment",
        "On a unit with DCV verified by a functional test, `demand_lift` is the CO2 response; "
        "set this well under it and well above the lift of a unit with fixed OA. Calibrating it "
        "on the unit you are judging is circular.",
        (20.0, 200.0),
        "Taken within the hour of day when stratify_hour is on and enough same-hour pairs exist.",
    ),
    "econ_high_limit_f": _P(
        "°F (outdoor air temperature)",
        "standard: ASHRAE 90.1 -- the top of its fixed dry-bulb high-limit range, as the code "
        "cites it (no section cited); chosen high so that more hours are excluded when unsure",
        "Set it to the high-limit setpoint in the economizer sequence. Better, map ECON_CMD so "
        "the economizer need not be inferred.",
        (55.0, 80.0),
        "Used only without ECON_CMD: samples with OAT at or below it are treated as possibly "
        "economizing and excluded (unless the heating valve is above 5 %). CAMBER temperatures "
        "are °F; an OAT trended in °C excludes nearly every sample.",
    ),
    "oa_floor_cfm": _P(
        "cfm (outdoor airflow)",
        "standard: ASHRAE 62.1 §6.2.7 (dynamic reset: the area component Ra·Az; the code notes "
        "section numbering varies by edition)",
        "Compute Ra·Az for the zones the unit serves from the design documents, or take the "
        "design minimum OA from the TAB report. For a multiple-zone system the true intake floor "
        "is higher than the sum of Ra·Az, so Ra·Az under-flags (the safe direction).",
        (0.0, 50000.0),
        "None: the below-floor and excess-at-low-demand sub-checks are not evaluated. A number "
        "or a mapping {equip: cfm}. Checked only on OA_AIRFLOW or AIRFLOW, not on a damper "
        "position or fan speed.",
    ),
    "breach_fault_pct": _P(
        "% of judged samples (occupied, fan on, not economizing, OA not closed)",
        "CAMBER judgment",
        "Raise it if short CO2 excursions at minimum OA are accepted by the sequence (e.g. a "
        "slow integral loop). Look at `co2_breach_at_min_pct` on a known-good period and set it "
        "above that.",
        (1.0, 50.0),
        "Needs co2_setpoint; share of samples with CO2 above the setpoint while OA sits at its "
        "floor (oa_floor_cfm + 10 %, else within 5 % of its range above its p5).",
    ),
    "below_floor_fault_pct": _P(
        "% of occupied fan-on samples (before the economizer and closed-OA exclusions)",
        "CAMBER judgment",
        "Look at `below_floor_total_pct` on a known-good period; set this above it. Keep it "
        "low: OA below the 62.1 floor while occupied is an under-ventilation fault.",
        (1.0, 50.0),
        "Needs oa_floor_cfm. A sample counts when OA is more than 10 % below the floor. The test "
        "reads below_floor_total_pct, which includes hours with the supply fan off; since 0.98 "
        "(#93) those are also reported apart (fan_off_occupied_pct) and below_floor_pct is the "
        "shortfall with the fan running.",
    ),
    "excess_warn_pct": _P(
        "% of low-demand samples (CO2 at or below its p25 and below the engage level)",
        "CAMBER judgment: DCV that holds OA above its floor at low demand saves no energy "
        "(the docs cite ASHRAE 90.1 §6.4.3.8 for why DCV exists, not for this value)",
        "Check `excess_at_low_demand_pct` on a unit whose DCV is known to reach its floor when "
        "zones are lightly occupied, and set this above it.",
        (10.0, 90.0),
        "Needs oa_floor_cfm. Only lifts an otherwise 'ok' verdict to 'warn'.",
    ),
    "min_samples": _P(
        "count (judged samples)",
        "CAMBER judgment: one day of hourly data",
        "Raise it for short-interval data (e.g. 96 for a day of 15-minute samples) so the "
        "verdict rests on more than a few hours.",
        (12, 500),
        "Also gates whether a fallback OA-signal segment gets its own line and whether the "
        "unventilated-while-occupied check runs.",
    ),
    **_VENT_SCHEDULE,
    "unventilated_co2_ppm": _P(
        "ppm (CO2)",
        "CAMBER judgment: 1100 ppm, the ~700 ppm-above-outdoor level co2_ventilation uses",
        "Set it to the CO2 level at which the space must be ventilated, typically the DCV "
        "setpoint.",
        (800.0, 2000.0),
        "None: co2_setpoint when set, else 1100 ppm. A sample counts when the space is "
        "occupied, CO2 is at or above it, and the fan is off or OA is at or below 2 % of its "
        "p95.",
    ),
    "unventilated_fault_hours": _P(
        "h (total over the dataset)",
        "CAMBER judgment: an outage is judged by duration, not share of the dataset",
        "Set it to how long an occupied space may go without outdoor air before it is a "
        "fault; half a working day is a common tolerance.",
        (1.0, 24.0),
        "Hours are the count of qualifying samples times the median sample step.",
    ),
    "full_outdoor_air": _P(
        "flag",
        "CAMBER judgment: a site fact, not a threshold (0.93, #37)",
        "Set it True only for a 100 % outdoor-air unit (no return air, no economizer damper), "
        "from the mechanical schedule.",
        (False, True),
        "True: the supply AIRFLOW, then SUPPLY_FAN_SPEED, serve as the OA signal when OA flow "
        "and damper are absent, and no economizer exclusion applies.",
    ),
    "stratify_hour": _P(
        "flag",
        "CAMBER judgment: a valve on a time clock and CO2 mixes the two in a pooled lift "
        "(0.93, #37)",
        "Leave it on. Turn it off only to reproduce pre-0.93 pooled verdicts; the pooled lift "
        "is always reported as demand_lift_pooled.",
        (False, True),
        "Falls back to the pooled lift when the same-hour strata hold too few pairs (lift_basis).",
    ),
    # ---- begin 098-followups (#93): below-floor duration fault, fan-off hours named ----
    "below_floor_fault_hours": _P(
        "h (one contiguous run of occupied samples)",
        "CAMBER judgment: a concentrated outage should not vanish in a long record; 4 h mirrors "
        "unventilated_fault_hours (half a working day)",
        "Set it to how long an occupied space may stay below its area-based floor before it is a "
        "fault. Read below_floor_longest_h on a known-good period and set it well above that.",
        (1.0, 24.0),
        "None (the default) = off: only the share test (below_floor_fault_pct) faults. A run is "
        "consecutive occupied samples below the floor, so it continues across the unoccupied "
        "night between two days; it includes hours with the supply fan off, and the finding says "
        "so when they make up most of the run. Needs oa_floor_cfm and an OA flow signal.",
    ),
    "fan_off_speed_pct": _P(
        "% supply fan speed",
        "CAMBER judgment: a VFD at a few percent moves essentially no air (the tower rules read "
        "5 % as off too); on lbnl-b59 the fan-off days read 1.2-2.6 % while running hours read "
        "far above it",
        "Take the speed the drive reports with the fan stopped (its 99th percentile on known "
        "off hours) and set this just above it, below the lowest running speed.",
        (0.0, 20.0),
        "Used only without a supply-fan status point and only where the OA floor is checked: an "
        "occupied sample below the floor with the fan at or below this speed is counted as "
        "fan_off_occupied_pct / _hours, not in below_floor_pct. Severity is unchanged: the share "
        "test reads below_floor_total_pct.",
    ),
    # ---- end 098-followups (#93) ----
}

EXEMPT["dcv_verification"] = {
    "min_corr": "deprecated since 0.82 and ignored (warns; removal in 1.0): the verdict no "
    "longer uses Pearson correlation -- use min_lift_ppm",
}

DELEGATES["dcv_system_verification"] = "dcv_verification"

PARAM_DOCS["dcv_system_verification"] = {
    "agg": _P(
        "choice",
        "CAMBER judgment: the critical zone should drive the reset",
        "Keep 'max' when the sequence resets on the highest zone CO2 (critical-zone reset); "
        "use 'mean' when it resets on an average or on return-air CO2.",
        ("max", "mean"),
        "How the trusted zone CO2 series joined to one air handler are combined per timestamp. "
        "'max' follows the worst trusted sensor.",
    ),
    "stuck_std_ppm": _P(
        "ppm (standard deviation of a zone's plausible CO2)",
        "CAMBER judgment",
        "Look at the standard deviation of zone CO2 sensors known to be working over the "
        "dataset; set this well below the smallest. A sensor under it is excluded as flat.",
        (1.0, 30.0),
        "Taken over the whole series of 250-5000 ppm samples, occupied or not.",
    ),
    "unoccupied_excess_ppm": _P(
        "ppm (unoccupied zone CO2 median above outdoor)",
        "CAMBER judgment",
        "Over nights and weekends a healthy zone CO2 decays toward outdoor. Take the "
        "unoccupied median minus outdoor on trusted sensors and set this well above it.",
        (100.0, 600.0),
        "A zone is excluded as offset / miscalibrated when its unoccupied median exceeds "
        "outdoor by more than this (needs 24 unoccupied samples). Unoccupied hours come from "
        "the zone's OCCUPANCY point, else the delegated schedule.",
    ),
    "assumed_outdoor_ppm": _P(
        "ppm (CO2)",
        "CAMBER judgment: roughly the present outdoor background, the same value "
        "camber.iaq assumes",
        "Map an OUTDOOR_CO2 sensor if there is one; otherwise use the unoccupied overnight "
        "minimum of trusted zone sensors as a site estimate.",
        (380.0, 500.0),
        "Used only when the zone frame has no OUTDOOR_CO2 point; only feeds the "
        "unoccupied-offset guard.",
    ),
}

# ==== end vent block ====


# ==== begin plant block (central plant: boilers, chilled and hot water, towers) ====

# Plant rules: boilers, hot-water and chilled-water loops, chillers, towers, condenser water.
# Executed inside camber.rules.param_docs (P, ParamDoc, _SCHEDULE, PARAM_DOCS, EXEMPT, FIXED
# are in scope).

# 0.98 (#86 item 4a, 098-plant-boiler) begin: the gas-input firing fallback of the three rules
_GAS_FIRING = (
    " Firing comes from the boiler run status (boiler_status); without one, from the gas input "
    "(gas_input_rate) above 5 % of its own 95th percentile (fixed in code), reported as "
    "run_source gas with a caveat."
)
# 0.98 (#86 item 4a, 098-plant-boiler) end

_PLANT_MAX_STARTS = (
    "CAMBER judgment: a generic cycling limit; the right value depends on the manufacturer's "
    "minimum cycle time (the code says to confirm it against the equipment's controls)"
)

PARAM_DOCS["boiler_summer_lockout"] = {
    "summer_lockout_oat_f": _P(
        "°F",
        "CAMBER judgment: a generic mild-climate lockout; the check itself follows PNNL "
        "Re-tuning Ch.8 (boiler lockout in summer), which gives no single value",
        "Use the heating lockout the building's sequence of operations specifies, or the outdoor "
        "temperature above which no zone has called for heat in a known-good year of trends. "
        "Set it per climate zone: higher in a hot-desert zone, lower in a cool one.",
        (50.0, 80.0),
        "Severity is fixed in code: warn when the boiler runs at OAT above this for >= 5 % of "
        "its running hours, fault at >= 20 %. Without an OAT point the check is not evaluated."
        + _GAS_FIRING,  # 0.98 (#86 item 4a, 098-plant-boiler)
    ),
}

PARAM_DOCS["boiler_short_cycle"] = {
    "max_starts_per_day": _P(
        "per day",
        _PLANT_MAX_STARTS,
        "Take the boiler's minimum on/off cycle time from its manual or burner control and "
        "divide the day by it with margin; or use the 95th-percentile daily starts of a "
        "known-good heating season. Coarse trends undercount starts, so the count is a floor.",
        (2.0, 24.0),
        "Warn at this many firing starts per day, fault at twice it."
        + _GAS_FIRING,  # 0.98 (#86 item 4a, 098-plant-boiler)
    ),
}

PARAM_DOCS["hw_plant_deltat"] = {
    "design_deltaT_min_f": _P(
        "°F",
        "CAMBER judgment: a typical hot-water loop design delta-T; the code says to confirm it "
        "against the loop's own design",
        "Read the design supply-minus-return delta-T from the boiler or coil schedules and set "
        "this a little below it. A low-temperature (condensing) loop is often designed for a "
        "smaller delta-T than a 180 °F loop.",
        (5.0, 40.0),
        "Severity is fixed in code: warn when >= 20 % of boiler-running hours sit below this, "
        "fault at >= 50 %." + _GAS_FIRING,  # 0.98 (#86 item 4a, 098-plant-boiler)
    ),
}

# ---- begin 098-plant-chw (#86 items 2 and 3): chw_plant_reset, chw/hw_pump_dp_reset ----

_NEAR_MIN_PCT_CALIBRATE = (
    "Read the drive's minimum speed from the VFD parameters (or the sequence of operations) and "
    "set this a point or two above it; or plot a histogram of the running speed and find the "
    'pile-up at the bottom. "auto" learns it: the 2nd percentile of the running speed counts '
    "as the floor when >= 10 % of running samples sit within floor_tol_pct of it and the 90th "
    "percentile is >= 20 points above it (else the band falls back to 25 %)."
)
_NEAR_MIN_PCT_NOTE = (
    'The band used is max(25, learned floor + floor_tol_pct) under "auto", else the number '
    "given. Severity is fixed in code: warn when >= 50 % of running time is at or below the "
    "band. The metrics vfd_floor_pct, near_min_band_pct and near_min_source say what was used."
)
_FLOOR_TOL = _P(
    "% speed",
    "CAMBER judgment: a VFD parked at its minimum reads within about a point of it",
    "Look at the spread of the speed readings while the pump sits at its minimum (trend "
    "resolution and rounding); set this just wider than that spread.",
    (0.1, 5.0),
    'Used only when near_min_pct is "auto".',
)

PARAM_DOCS["hw_pump_dp_reset"] = {
    "near_min_pct": _P(
        '% speed, or "auto"',
        "CAMBER judgment: 25 % is a common VFD minimum; hot-water pumps keep the fixed band by "
        'default (0.98, #86), with "auto" as an opt-in',
        _NEAR_MIN_PCT_CALIBRATE,
        ("auto", 10.0, 60.0),
        _NEAR_MIN_PCT_NOTE,
    ),
    "floor_tol_pct": _FLOOR_TOL,
}

FIXED["hw_pump_dp_reset"] = (
    "fault when the pump runs near full speed (>= 90 % speed) for >= 60 % of its running time; "
    "warn at >= 30 % near full, or near minimum (at or below near_min_pct) for >= 50 %; running "
    "means speed > 5 %; a DP setpoint with a standard deviation under 0.5 (its own units) counts "
    "as flat (no reset); fixed in code (camber/rules/hwpump_rule.py, camber/chwpump.py)"
)

PARAM_DOCS["chw_plant_reset"] = {
    "design_deltaT_min_f": _P(
        "°F",
        "public source: PNNL Building Re-tuning Ch.8 (the low-delta-T threshold, ~8 °F)",
        "Read the loop's design delta-T from the chiller or coil schedules and set this a little "
        "below it; or take the 10th percentile of the loop delta-T over a known-good, loaded "
        "cooling season. Do not tune it on the period you score.",
        (3.0, 20.0),
        "Severity is fixed in code: warn when >= 20 % of running hours sit below this, fault at "
        ">= 50 %; not judged on a constant-flow plant (see flow_mode).",
    ),
    "expected_reset_sign": _P(
        "choice",
        "CAMBER judgment: an outdoor-air CHWST reset lowers the supply temperature as OAT "
        "rises, a negative slope on OAT",
        'Read the reset schedule in the sequence of operations. "positive" fits a plant whose '
        'supply is deliberately raised in hot weather; "any" accepts either direction (the '
        "pre-0.98 behaviour).",
        ("negative", "positive", "any"),
        'A clear slope the other way is reported as chwst_reset_direction "reverse", counts as '
        "no reset, and warns.",
    ),
    "flow_mode": _P(
        "choice",
        "CAMBER judgment: a constant-primary-flow plant has a low loop delta-T at part load by "
        "design, so delta-T is judged only on variable flow",
        'Declare "constant" or "variable" from the plant\'s pumping design; "auto" reads the '
        "mapped CHW flow (chw_flow) over the running hours and needs >= 24 of them.",
        ("auto", "constant", "variable"),
        'Without a flow point, "auto" reports flow_mode "unknown" and judges delta-T.',
    ),
    "constant_flow_cv": _P(
        "fraction (std / mean)",
        "CAMBER judgment: a constant-speed primary pump varies well under 5 %; the lbnl-chiller "
        "plant's chiller-1 flow varies <= 0.11 % on every run, a variable-flow loop by tens of %",
        "Compute the coefficient of variation of the CHW flow over the running hours of a known "
        "period; a constant-flow plant sits near zero, a variable-flow one well above this.",
        (0.01, 0.2),
        'Used only when flow_mode is "auto".',
    ),
}

FIXED["chw_plant_reset"] = (
    "warn when CHWST vs OAT is flat (|slope| < 0.05 °F/°F) or reversed; occupied hours only; "
    "without a run status or power, running means CHWST in 38-58 °F; CHWST <= 46 °F is reported "
    "as held low (metric only); fixed in code (camber/rules/chwplant_rule.py, camber/chwplant.py)"
)

PARAM_DOCS["chw_pump_dp_reset"] = {
    "near_min_pct": _P(
        '% speed, or "auto"',
        "CAMBER judgment: learn the drive's own minimum (0.98, #86); a fixed 25 % missed the "
        "lbnl-chiller secondary pump, whose floor is 34.5 %",
        _NEAR_MIN_PCT_CALIBRATE,
        ("auto", 10.0, 60.0),
        _NEAR_MIN_PCT_NOTE,
    ),
    "floor_tol_pct": _FLOOR_TOL,
}

FIXED["chw_pump_dp_reset"] = (
    "fault when the pump runs near full speed (>= 90 % speed) for >= 60 % of its running time; "
    "warn at >= 30 % near full, or near minimum (at or below the near_min_pct band) for >= 50 %; "
    "running means speed > 5 %; a DP setpoint with a standard deviation under 0.5 (its own "
    "units) counts as flat (no reset, reported but not in severity); fixed in code "
    "(camber/rules/chwpump_rule.py, camber/chwpump.py)"
)

# ---- end 098-plant-chw ----

PARAM_DOCS["chw_supply_tracking"] = {
    "above_f": _P(
        "°F",
        "CAMBER judgment: outside a healthy loop's ~1 °F control band, ~0.5 °F sensor accuracy "
        "and hourly staging transients together",
        "In a known-good period with the chiller running, take the 95th or 99th percentile of "
        "CHWST minus its setpoint and set this just above it. Do not tune it on the period you "
        "are scoring: a plant that never makes setpoint would set its own excuse.",
        (1.0, 8.0),
    ),
    "warn_pct": _P(
        "% of running samples",
        "CAMBER judgment",
        "Look at the share of running time above setpoint + above_f on a healthy plant and set "
        "the warn well above it; a summer design-day peak can briefly exceed capacity.",
        (1.0, 50.0),
        "Must be below fault_pct.",
    ),
    "fault_pct": _P(
        "% of running samples",
        "CAMBER judgment",
        "Set it at a share of running time you would dispatch a technician for; keep it above "
        "warn_pct. The rule caps at warn when runtime was inferred from temperature.",
        (5.0, 80.0),
    ),
    "settle_intervals": _P(
        "count (trend intervals)",
        "CAMBER judgment: the first interval after each start is pull-down, not tracking",
        "Measure how long CHWST takes to reach setpoint after a start in trends, and divide by "
        "the trend interval (round up). With 15-minute trends this is often 2-4.",
        (0, 12),
        "Applies only when a run status or power gate is available.",
    ),
    "min_running": _P(
        "count (running samples)",
        "CAMBER judgment: a day of hourly samples before judging",
        "Raise it for a short-sample trend (e.g. 96 for a day of 15-minute data) so the rule "
        "judges at least a day of running time.",
        (6, 500),
    ),
}

PARAM_DOCS["chiller_efficiency"] = {
    "design_kw_per_ton": _P(
        "kW/ton",
        "CAMBER judgment: a generic ceiling between water-cooled centrifugal (~0.5-0.6) and "
        "air-cooled (~1.0-1.2) machines; the code says to set it from the chiller schedule",
        "Use the chiller schedule or manufacturer selection (kW/ton at the loads it actually "
        "runs, e.g. IPLV/NPLV points), or the median kW/ton of a known-good season after a tube "
        "cleaning. Calibrating on the period you score hides a degradation already present.",
        (0.4, 1.6),
        "Severity is fixed in code on the median kW/ton: warn at >= 1.2x this, fault at >= 1.5x. "
        "pct_hours_inefficient counts hours above 1.15x (metric only).",
    ),
}

PARAM_DOCS["chiller_staging"] = {
    "max_starts_per_day": _P(
        "per day",
        _PLANT_MAX_STARTS,
        "Take the compressor's minimum start-to-start time (anti-recycle timer) from the "
        "chiller controls and allow margin; or use the 95th-percentile daily starts of a "
        "known-good cooling season. Coarse trends undercount starts, so the count is a floor.",
        (2.0, 24.0),
        "Warn at this many starts per day, fault at twice it.",
    ),
    "low_load_pct": _P(
        "% of the chiller's observed peak tons",
        "CAMBER judgment: the 'idling' cut-off for the optional load-factor metric",
        "Set it near the load below which the chiller's part-load curve (from the manufacturer) "
        "turns sharply less efficient, commonly 30-40 % of capacity.",
        (10.0, 70.0),
        "Needs CHW flow and temperatures. Severity is fixed in code: warn when >= 25 % of running "
        "hours sit below this load, fault at >= 50 %. Peak is the observed peak, not nameplate.",
    ),
}

PARAM_DOCS["chiller_staging_fleet"] = {
    "redundancy_ceiling": _P(
        "fraction of one chiller's capacity",
        "CAMBER judgment: how fully one chiller can be loaded before the next must stage on",
        "Use the plant's stage-up load from its staging sequence (e.g. 0.85-0.95 of capacity). "
        "Capacity is each chiller's 95th-percentile power, so a plant that never reaches full "
        "load reads a low capacity; lower this if the flagged hours look like correct staging.",
        (0.5, 1.0),
        "Severity is fixed in code: warn when >= 20 % of multi-chiller hours are over-staged, "
        "fault at >= 50 %.",
    ),
}

PARAM_DOCS["chiller_approach_fouling"] = {
    "cond_design_f": _P(
        "°F",
        "CAMBER judgment: a typical clean-tube condenser approach",
        "Use the condenser approach from the chiller's selection report or start-up log, or the "
        "median of a period just after a tube cleaning at similar load.",
        (1.0, 10.0),
        "Severity is fixed in code on the worse leg's median / design: warn at >= 1.5x, fault "
        "at >= 2x.",
    ),
    "evap_design_f": _P(
        "°F",
        "CAMBER judgment: a typical clean-tube evaporator approach",
        "Use the evaporator approach from the chiller's selection report or start-up log, or the "
        "median of a known-good period with correct charge and flow.",
        (0.5, 8.0),
        "Same warn (1.5x) / fault (2x) ratios as the condenser leg.",
    ),
}

# ---- begin 098-followups (#92): the site elevation for a derived wet-bulb ----
_SITE_ELEVATION = {
    "elevation_ft": _P(
        "ft",
        "CAMBER judgment: None assumes sea level; a site input, not a threshold",
        "Enter the site elevation above sea level from a survey or map. It corrects a wet-bulb "
        "derived from OAT + RH; a measured wet-bulb point ignores it. Set it once for the site "
        "with the config's top-level site_elevation_ft, which reaches cooling_tower_approach, "
        "condenser_water_reset and the tower drift detectors.",
        (-300.0, 10000.0),
        "None = sea-level Stull wet-bulb, which reads high at altitude (about +1.4 °F at 500 m, "
        "+2.6 °F at 1,600 m in hot, dry air). pressure_psia takes precedence when both are "
        "given; a rule's own value wins over the config's site_elevation_ft.",
    ),
    "pressure_psia": _P(
        "psia",
        "CAMBER judgment: None assumes sea level; a site input, not a threshold",
        "Enter a typical measured barometric pressure (absolute, not sea-level corrected) at the "
        "site, or leave None and give elevation_ft.",
        (10.0, 15.5),
        "None = use elevation_ft, or sea level when that is also None.",
    ),
}
# ---- end 098-followups (#92) ----

PARAM_DOCS["cooling_tower_approach"] = {
    "design_approach_f": _P(
        "°F",
        "CAMBER judgment: a well-sized, clean tower achieves ~3-7 °F; the code says to set it "
        "from the tower schedule",
        "Use the design approach from the tower selection (leaving water minus design wet-bulb). "
        "Without one, the median approach at high fan in a known-good period is a calibration, "
        "not a design value; a fouled tower calibrated this way will look healthy.",
        (2.0, 15.0),
        "Severity is fixed in code on the median approach: warn at >= 1.3x this, fault at "
        ">= 1.7x. pct_hours_high_approach counts hours above design + 3 °F (metric only).",
    ),
    "min_effort_pct": _P(
        "% tower fan speed",
        "CAMBER judgment: a tower's capability is its approach at full fan (CTI rating practice); "
        "at part fan the approach is the controller's choice",
        "Set it just below the fan speed the tower reaches on a hot afternoon. If the rule "
        "declines because the fan never reaches it, lower it, knowing part-fan hours judge "
        "control, not the tower.",
        (50.0, 100.0),
        "None restores the old 'fan running' gate, which judged cold-weather hours held above a "
        "minimum condenser-water temperature. Used only when a fan speed is trended.",
    ),
    # 0.98 (#92, 098-followups): shared with condenser_water_reset, see _SITE_ELEVATION
    **_SITE_ELEVATION,
}

PARAM_DOCS["condenser_water_reset"] = {
    "reset_slope_flat": _P(
        "°F CW supply per °F wet-bulb",
        "CAMBER judgment: an ideal reset tracks wet-bulb about 1:1, so below this it is "
        "effectively flat",
        "Check the reset schedule in the sequence of operations: a reset limited by a minimum "
        "condenser-water temperature has a lower slope over the year; fit the slope over a "
        "known-good period with the reset working and set this well below it.",
        (0.05, 0.8),
        "No reset is reported as warn (an efficiency opportunity), never fault. A wet-bulb "
        "derived from OAT + RH at altitude reads high, more so in dry air, so the slope moves a "
        "little: give elevation_ft near this threshold.",
    ),
    # 0.98 (#92, 098-followups): the derived wet-bulb takes the site elevation
    **_SITE_ELEVATION,
}

PARAM_DOCS["condenser_bypass_leak"] = {
    "closed_pct": _P(
        "% valve command",
        "CAMBER judgment: a command at or below this is 'shut'",
        "Set it to the bypass command the controller writes when fully closed, plus a little "
        "for analog noise; a controller that parks at a few percent needs a higher value.",
        (0.0, 10.0),
    ),
    "warn_f": _P(
        "°F",
        "CAMBER judgment: twice the combined ±0.5 °F accuracy of two plant sensors "
        "(screening-grade)",
        "Compare the two sensors in a known-good period with the bypass shut (e.g. after a "
        "valve rebuild) and set this above their median difference plus the sensors' combined "
        "accuracy.",
        (1.0, 5.0),
        "It also gates both sensor-offset tests (a difference of at least +warn_f or -warn_f).",
    ),
    "fault_f": _P(
        "°F",
        "CAMBER judgment: roughly a 5-8 % rise in chiller kW/ton (screening-grade)",
        "Set it at the entering-water rise worth a valve repair on this plant, from the "
        "chillers' kW/ton sensitivity to condenser-water temperature (often 1-2 % per °F).",
        (2.0, 15.0),
        "Must be above warn_f.",
    ),
    "min_samples": _P(
        "count (samples)",
        "CAMBER judgment: a day of hourly samples before judging",
        "Raise it for short-interval trends (e.g. 96 for a day of 15-minute data) or when the "
        "bypass is rarely shut while a chiller runs.",
        (6, 500),
        "Also the minimum count for the bypassed-fraction estimate and the offset regression.",
    ),
}

# ==== end plant block ====


# ==== begin dx block (DX and heat pumps) ====

# --------------------------------------------------------------------------- DX / heat pump
# compressor_short_cycle, compressor_staging, heatpump_defrost, dx_refrigerant_charge,
# dx_indoor_airflow, hp_mode_vs_need, hp_capacity_shortfall, hp_room_imbalance,
# source_loop_deltat

PARAM_DOCS["compressor_short_cycle"] = {
    "max_starts_per_day": _P(
        "compressor starts per day (off-to-on transitions over the trend's span)",
        "CAMBER judgment: a screening ceiling of about one start every 2 h; it is not derived "
        "from a DX minimum off-time (a ~5 min timer alone would permit ~12 starts an hour)",
        "Take the manufacturer's minimum on/off timers and the unit's staging hysteresis; better, "
        "count the starts per day in a known-good week of similar weather and set the ceiling "
        "somewhat above its highest day. Calibrating on the period you are scoring would hide a "
        "short-cycling fault.",
        (6.0, 72.0),
        "Warn at the ceiling, fault at twice it. The span includes days the unit is off, so a "
        "trend with long idle stretches lowers the rate.",
    ),
}

PARAM_DOCS["compressor_staging"] = {
    "max_changes_per_day": _P(
        "compressor-stage changes per day",
        "CAMBER judgment: depends on the staging hysteresis; no source is cited",
        "Count the stage changes per day in a known-good period with a similar load and set the "
        "ceiling above its busiest day; the controller's stage-up / stage-down delays bound what "
        "a stable sequence can do.",
        (8.0, 96.0),
        "Warn at the ceiling, fault at twice it.",
    ),
}

PARAM_DOCS["heatpump_defrost"] = {
    "max_reversals_per_day": _P(
        "reversing-valve transitions per day",
        "CAMBER judgment: a screening ceiling of about 12 defrosts a day (two transitions each); "
        "defrost at worst about once an hour in cold weather would reach ~48 transitions a day",
        "Read the defrost-control interval (time- or demand-based) from the unit's manual, or "
        "count the reversals per day in a known-good cold spell and set the ceiling above that. "
        "Each defrost counts two transitions (into cooling and back), so allow for that.",
        (12.0, 96.0),
        "Warn at the ceiling, fault at twice it. Heating/cooling changeovers count too.",
    ),
}

# The storage / identity handles the refrigerant-side DX rules share.
_DX_EXEMPT = {
    "store": "a frozen-baseline store handle (in-memory when omitted), not a threshold",
    "site": "the site id the frozen baseline is stored under, not a threshold",
    "run_id": "the run id recorded when a baseline is frozen, not a threshold",
}

# The frozen-baseline plumbing shared by dx_refrigerant_charge and dx_indoor_airflow.
_DX_BASELINE = {
    "freeze_if_missing": _P(
        "flag",
        "CAMBER judgment: a first baseline-mode run freezes its fault-free period",
        "Leave True for a first run on a known-good period; set False once baselines are frozen "
        "so a later run cannot silently freeze a faulty period as the reference.",
        (False, True),
        "Baseline mode (analyze_periods) only. False with no stored baseline declines the unit.",
    ),
    "min_samples": _P(
        "count (cooling-mode rows)",
        "CAMBER judgment: the smallest sample whose median is worth reporting",
        "Raise it for minute data, where 10 rows is a few minutes of running; with hourly or "
        "averaged test points keep it low.",
        (5, 200),
        "Target mode: rows needed in the frame. Baseline mode: scoreable rows in the current "
        "period.",
    ),
    "min_baseline_samples": _P(
        "count (cooling-mode rows)",
        "CAMBER judgment: the minimum for the regression on outdoor and return air (shared "
        "_dxfit default)",
        "Make the fault-free period long enough to span the weather the unit will be scored in; "
        "raise this if the fitted slopes look unstable.",
        (20, 1000),
        "Baseline mode only.",
    ),
}

PARAM_DOCS["dx_refrigerant_charge"] = {
    **_DX_BASELINE,
    "targets": _P(
        "°F (a per-unit mapping)",
        "public source: the unit's nameplate / installation-manual charging target",
        "Copy the target subcooling (and superheat, for corroboration) from the manufacturer's "
        "charging chart or nameplate, at the conditions the unit mostly runs in. Taking it from "
        "the trend you score is circular.",
        (5.0, 20.0),
        "None: no target, so only min_subcooling_f / max_subcooling_f apply (each a warn), and "
        "metric='superheat' declines. Shape: {'subcooling_f': 10, 'superheat_f': 8, "
        "'tolerance_f': 3} for every unit, or {'RTU-*': {...}} keyed by fnmatch patterns on the "
        "equipment id. The range is for subcooling_f.",
    ),
    "tolerance_f": _P(
        "°F (either side of the target)",
        "public source: manufacturer charging instructions (the common ±3 °F on target "
        "subcooling, as the rule module cites)",
        "Use the tolerance printed in the unit's charging instructions; a per-unit 'tolerance_f' "
        "in targets overrides it.",
        (1.0, 5.0),
        "Target mode only: warn at the tolerance, fault at twice it.",
    ),
    "metric": _P(
        "choice",
        "CAMBER judgment: TXV / EEV units are charged by subcooling, fixed-orifice units by "
        "superheat",
        "Choose 'superheat' for a fixed-orifice (piston) unit, whose charging chart is by "
        "superheat; otherwise keep 'subcooling'.",
        ("subcooling", "superheat"),
    ),
    "warn_f": _P(
        "°F (median residual against the baseline)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd, where "
        "max(3 °F, 1.5σ) caught 91% of charge-fault files at 8% of fault-free files",
        "Score a known-good period against its own frozen baseline (a held-out part of it, not "
        "the fitted rows) and set the floor above its largest residual.",
        (1.5, 6.0),
        "Baseline mode only: a warn needs |drift| >= max(warn_f, warn_sigma × σ).",
    ),
    "fault_f": _P(
        "°F (median residual against the baseline)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Keep it clear of warn_f; about the residual a 10-15% charge loss gives on the unit, if "
        "a charging test or chart tells you.",
        (3.0, 10.0),
        "Baseline mode only: a fault needs |drift| >= max(fault_f, fault_sigma × σ).",
    ),
    "warn_sigma": _P(
        "σ (standard deviations of the baseline residual)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Raise it if a held-out part of the fault-free period trips the warn; the °F floor "
        "governs a very steady baseline.",
        (1.0, 4.0),
        "Baseline mode only.",
    ),
    "fault_sigma": _P(
        "σ (standard deviations of the baseline residual)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Keep it about twice warn_sigma.",
        (2.0, 6.0),
        "Baseline mode only.",
    ),
    "min_subcooling_f": _P(
        "°F",
        "CAMBER judgment: below about 2 °F of subcooling a TXV is fed flash gas (screening-grade "
        "universal limit)",
        "Only for units with no target: set a target instead where the nameplate gives one.",
        (0.0, 5.0),
        "Limits mode only (no target, metric='subcooling'); below it is a low-charge warn.",
    ),
    "max_subcooling_f": _P(
        "°F",
        "CAMBER judgment: no charging target sits near 25 °F (screening-grade universal limit)",
        "Only for units with no target; a unit with an unusually high design subcooling needs a "
        "target instead.",
        (15.0, 35.0),
        "Limits mode only (no target, metric='subcooling'); above it is an overcharge warn.",
    ),
}
EXEMPT["dx_refrigerant_charge"] = dict(_DX_EXEMPT)

PARAM_DOCS["dx_indoor_airflow"] = {
    **_DX_BASELINE,
    "targets": _P(
        "°F (a per-unit mapping)",
        "public source: the installer's target temperature split for the unit",
        "Take the target split from a chart that uses the return wet bulb and dry bulb where you "
        "have one (a fixed number ignores latent load), or from commissioning measurements at "
        "rated airflow. Deriving it from the scored trend is circular.",
        (15.0, 22.0),
        "None: no target, so only min_split_f / max_split_f apply (each a warn). Shape: "
        "{'temp_split_f': 20, 'tolerance_f': 3} for every unit, or {'RTU-*': {...}} keyed by "
        "fnmatch patterns on the equipment id. The range is for temp_split_f.",
    ),
    "tolerance_f": _P(
        "°F (either side of the target split)",
        "CAMBER judgment: ±3 °F; no source is cited",
        "Widen it where the return humidity swings widely, since the fixed target ignores "
        "latent load; a per-unit 'tolerance_f' in targets overrides it.",
        (1.5, 5.0),
        "Target mode only: warn at the tolerance, fault at twice it.",
    ),
    "warn_f": _P(
        "°F (median split residual against the baseline)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd, where "
        "max(1.5 °F, 2σ) flagged 2 of 90 fault-free files",
        "Score a held-out part of a known-good period against its frozen baseline and set the "
        "floor above its largest residual.",
        (1.0, 4.0),
        "Baseline mode only: a warn needs |drift| >= max(warn_f, warn_sigma × σ). Airflow "
        "faults under about 15% sit inside the fault-free scatter.",
    ),
    "fault_f": _P(
        "°F (median split residual against the baseline)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Keep it about twice warn_f.",
        (2.0, 8.0),
        "Baseline mode only.",
    ),
    "warn_sigma": _P(
        "σ (standard deviations of the baseline residual)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Raise it if a held-out known-good period trips the warn, e.g. with no return dew point "
        "to absorb latent-load swings.",
        (1.0, 4.0),
        "Baseline mode only.",
    ),
    "fault_sigma": _P(
        "σ (standard deviations of the baseline residual)",
        "CAMBER judgment: screening-grade; characterized on nist-heatpump-fdd",
        "Keep it about twice warn_sigma.",
        (2.0, 8.0),
        "Baseline mode only.",
    ),
    "min_split_f": _P(
        "°F (return minus supply air while cooling)",
        "CAMBER judgment: a DX coil at rated airflow drops roughly 15-22 °F; the 8-28 °F band is "
        "wide on purpose (screening-grade)",
        "Only for units with no target: set a target split instead where one is known.",
        (5.0, 12.0),
        "Limits mode only; below it is a warn read as high airflow or lost capacity (charge).",
    ),
    "max_split_f": _P(
        "°F (return minus supply air while cooling)",
        "CAMBER judgment: a DX coil at rated airflow drops roughly 15-22 °F; the 8-28 °F band is "
        "wide on purpose (screening-grade)",
        "Only for units with no target: set a target split instead where one is known.",
        (24.0, 35.0),
        "Limits mode only; above it is a low-airflow warn.",
    ),
}
EXEMPT["dx_indoor_airflow"] = dict(_DX_EXEMPT)

# The mode inference and comfort band shared by the three heat-pump operations rules (_HPBase).
_HP_BASE = {
    "heat_sp_f": _P(
        "°F",
        "CAMBER judgment: a stated default comfort band (68-76 °F)",
        "Set it to the site's occupied heating setpoint from the BAS or the thermostats; better, "
        "map the heat_sp point so the rule reads it.",
        (60.0, 74.0),
        "Used only when no heat_sp point is mapped (the finding then carries a caveat).",
    ),
    "cool_sp_f": _P(
        "°F",
        "CAMBER judgment: a stated default comfort band (68-76 °F)",
        "Set it to the site's occupied cooling setpoint; better, map the cool_sp point.",
        (70.0, 82.0),
        "Used only when no cool_sp point is mapped (the finding then carries a caveat).",
    ),
    "band_tol_f": _P(
        "°F (past the setpoint)",
        "CAMBER judgment: the zone must be clearly past a setpoint to count as outside the band",
        "Set it to about the thermostat's deadband or the zone sensor's accuracy, whichever is "
        "larger.",
        (0.0, 3.0),
    ),
    "heat_rise_f": _P(
        "°F (discharge air above the zone)",
        "CAMBER judgment: a water-to-air heat pump delivers about 85-105 °F air in heating "
        "against a ~70 °F room",
        "Look at a known-good week: the discharge-minus-zone difference while the unit heats "
        "clusters well above zero; set this below that cluster and above the fan-only rise.",
        (8.0, 30.0),
        "Ignored when compressor status and the reversing-valve command are both mapped.",
    ),
    "cool_drop_f": _P(
        "°F (discharge air below the zone)",
        "CAMBER judgment: a heat pump in cooling delivers about 52-60 °F air",
        "Look at a known-good week: set it below the zone-minus-discharge difference seen while "
        "the unit cools and above the fan-only difference.",
        (5.0, 20.0),
        "Ignored when compressor status and the reversing-valve command are both mapped.",
    ),
}

PARAM_DOCS["hp_mode_vs_need"] = {
    **_HP_BASE,
    "warn_hours": _P(
        "h (occupied hours in the wrong mode)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Scale it with the length of the trend you score: a couple of hours over a month is "
        "noise, over a day it is not.",
        (0.5, 24.0),
        "A warn needs both warn_hours and warn_pct.",
    ),
    "fault_hours": _P(
        "h (occupied hours in the wrong mode)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Scale it with the trend's length, as warn_hours.",
        (2.0, 100.0),
        "A fault needs both fault_hours and fault_pct.",
    ),
    "warn_pct": _P(
        "% of occupied running samples (heating or cooling)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Check a known-good period: brief wrong-mode samples at changeover set the floor.",
        (0.5, 10.0),
    ),
    "fault_pct": _P(
        "% of occupied running samples (heating or cooling)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Keep it well above warn_pct.",
        (5.0, 50.0),
    ),
}

PARAM_DOCS["hp_capacity_shortfall"] = {
    **_HP_BASE,
    "warn_outside_pct": _P(
        "% of occupied samples with a mode and zone temperature (per side)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Check the share of occupied time out of band in a known-good season; set the warn "
        "above it.",
        (2.0, 30.0),
    ),
    "fault_outside_pct": _P(
        "% of occupied samples with a mode and zone temperature (per side)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Keep it well above warn_outside_pct.",
        (10.0, 60.0),
        "A 'mixed' verdict (working_pct between idle_pct and saturated_pct) is capped at warn.",
    ),
    "saturated_pct": _P(
        "% of the out-of-band samples with the unit running in the needed mode",
        "CAMBER judgment: mostly working while out of band reads as capacity, not control",
        "Lower it for units that cycle on a thermostat (they run part of every hour even when "
        "short of capacity).",
        (50.0, 95.0),
        "At or above: a 'capacity' verdict. Must stay above idle_pct.",
    ),
    "idle_pct": _P(
        "% of the out-of-band samples with the unit running in the needed mode",
        "CAMBER judgment: mostly idle while out of band reads as control, not capacity",
        "Keep it well below saturated_pct.",
        (5.0, 50.0),
        "At or below: a 'control' verdict.",
    ),
    "min_hours": _P(
        "h (occupied hours out of band, per side)",
        "CAMBER judgment: ignore a side with only a few hours out of band",
        "Raise it for long trends so a single cold morning cannot trigger the rule.",
        (1.0, 24.0),
    ),
}

PARAM_DOCS["hp_room_imbalance"] = {
    **_HP_BASE,
    "warn_fight_pct": _P(
        "% of co-occupied samples (one unit heating while another cools)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Check a known-good season: brief overlap at changeover sets the floor.",
        (0.5, 10.0),
    ),
    "fault_fight_pct": _P(
        "% of co-occupied samples (one unit heating while another cools)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Keep it well above warn_fight_pct.",
        (5.0, 40.0),
    ),
    "warn_duty_spread_pct": _P(
        "percentage points (difference between the units' active shares)",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Raise it where the units in a room are deliberately unequal (a lead unit plus a "
        "smaller helper).",
        (15.0, 80.0),
        "Warn only.",
    ),
    "warn_sensor_spread_f": _P(
        "°F (median spread between the room's zone sensors)",
        "CAMBER judgment: one room's sensors should agree within a few degrees",
        "Set it from the sensors' accuracy plus the room's normal stratification, e.g. from a "
        "spot check with a reference thermometer.",
        (1.0, 6.0),
        "Warn only.",
    ),
}
EXEMPT["hp_room_imbalance"] = {
    "rooms": "a structural mapping {room: [unit, ...]} that declares which units share a room "
    "(else the topology), not a threshold",
}

PARAM_DOCS["source_loop_deltat"] = {
    "design_deltat_f": _P(
        "°F (loop return minus supply, magnitude)",
        "public source: ASHRAE / IGSHPA closed-loop heat-pump design practice (about 8-12 °F at "
        "roughly 3 gpm per ton), as the rule module cites; not a standard requirement",
        "Use the loop's design ΔT from the mechanical schedules or the design flow and block load.",
        (6.0, 15.0),
        "Warn needs the 90th-percentile |ΔT| below half of it; fault, below a quarter.",
    ),
    "flat_deltat_f": _P(
        "°F (loop return minus supply, magnitude)",
        "CAMBER judgment: under 1 °F the loop carries next to no heat",
        "Set it a little above the combined error of the two loop sensors; check with the pumps "
        "running and every heat pump off, where |ΔT| should read near zero.",
        (0.3, 3.0),
    ),
    "warn_flat_pct": _P(
        "% of pumping samples",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Check the flat share in a known-good season with a working DP reset; set the warn "
        "above it.",
        (20.0, 80.0),
    ),
    "fault_flat_pct": _P(
        "% of pumping samples",
        "CAMBER judgment: screening-grade (provisional, 0.93)",
        "Keep it well above warn_flat_pct.",
        (50.0, 95.0),
    ),
    "min_pump_speed_pct": _P(
        "% of full pump speed",
        "CAMBER judgment: any speed above a near-zero command counts as pumping",
        "Set it just above the speed signal the drive reports when stopped.",
        (0.0, 30.0),
        "Used only when no pump status is mapped.",
    ),
    "min_hours": _P(
        "h (pumping hours with both loop temperatures)",
        "CAMBER judgment: at least a day of pumping",
        "Raise it to a week where the loop load swings with occupancy.",
        (6.0, 168.0),
    ),
}

# ==== end dx block ====


# ==== begin fixed-in-code notes for rules that also have tunables ====

FIXED["co2_ventilation"] = (
    "under-ventilated when CO2 is > 700 ppm above outdoor, over-ventilated when < 150 ppm above "
    "(camber/iaq.py); fault when under-ventilated >= 20 % of occupied hours, warn at >= 5 %, or "
    "when over-ventilated >= 60 % (camber/rules/iaq_rule.py); the occupied schedule is the fixed "
    "weekday 07:00-18:00"
)
FIXED["reheat_minimization_g36"] = (
    "warn when >= 15 % and fault when >= 40 % of reheating hours run above minimum flow "
    "(camber/rules/reheat_min_rule.py)"
)
FIXED["dcv_system_verification"] = (
    "a zone needs >= 24 CO2 samples to take part, and >= 24 unoccupied samples for the "
    "unoccupied CO2-offset guard, whatever min_samples says (camber/rules/ventilation_rule.py)"
)
for _rule, _knob in (
    ("compressor_short_cycle", "max_starts_per_day"),
    ("compressor_staging", "max_changes_per_day"),
    ("heatpump_defrost", "max_reversals_per_day"),
):
    FIXED[_rule] = f"warn at {_knob} or more; fault at twice it (the factor 2 is fixed in code)"
del _rule, _knob

# ==== end fixed-in-code notes ====


# --------------------------------------------------------------------------- introspection


@dataclass(frozen=True)
class RuleParam:
    """One constructor parameter of a registered rule, with its default and its documentation."""

    rule: str
    name: str
    default: object
    doc: ParamDoc | None
    delegated_from: str = ""


_IDENTITY = {"self"}


def _signature_params(cls) -> list:
    """The keyword parameters of ``cls.__init__``, following ``**kw`` up the MRO."""
    out: list = []
    seen: set = set()
    for klass in cls.__mro__:
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        if klass is object:
            break
        var_kw = False
        for p in inspect.signature(init).parameters.values():
            if p.name in _IDENTITY or p.kind is p.VAR_POSITIONAL:
                continue
            if p.kind is p.VAR_KEYWORD:
                var_kw = True
                continue
            if p.name not in seen:
                seen.add(p.name)
                out.append(p)
        if not var_kw:
            break
    return out


def _rule_factories() -> dict:
    from .builtin import rule_factories

    return rule_factories()


def rule_params(rule: str) -> list[RuleParam]:
    """Every tunable parameter of the registered rule ``rule``, with its default and its doc.

    Defaults come from the constructor signature, never from this module. Parameters listed in
    :data:`EXEMPT` are left out; a rule in :data:`DELEGATES` lists the delegate rule's tunables.
    Raises ``KeyError`` for an unknown rule.
    """
    factories = _rule_factories()
    if rule not in factories:
        raise KeyError(rule)
    cls, fixed = factories[rule]
    exempt = EXEMPT.get(rule, {})
    docs = {**_PENDING.get(rule, {}), **PARAM_DOCS.get(rule, {})}
    out = []
    for p in _signature_params(cls):
        if p.name in fixed or p.name in exempt:
            continue
        default = None if p.default is inspect.Parameter.empty else p.default
        out.append(RuleParam(rule, p.name, default, docs.get(p.name)))
    target = DELEGATES.get(rule)
    if target:
        have = {rp.name for rp in out}
        out += [
            RuleParam(rule, rp.name, rp.default, rp.doc, delegated_from=target)
            for rp in rule_params(target)
            if rp.name not in have
        ]
    return out


def documented_rules() -> list[str]:
    """The sorted names of every registered rule (each has an entry, or no tunables)."""
    return sorted(_rule_factories())


# ==== begin 0100-leak-drift (#100): opt-in drift detectors ====
#
# The drift detectors are not registered rules (each needs an injected BaselineStore), so they are
# not in PARAM_DOCS. The opt-in ones a config can tune are documented here, keyed by rule name, and
# rendered into docs/THRESHOLDS.md under "Opt-in drift detectors"; tests/test_coil_leak_drift.py
# checks the entries against the constructors exactly as tests/test_param_docs.py does for rules.

#: Opt-in drift detector -> parameter -> documentation (provisional, 0.100).
DRIFT_PARAM_DOCS: dict[str, dict[str, ParamDoc]] = {}
#: Opt-in drift detector -> constructor parameters that are not thresholds, with the reason.
DRIFT_EXEMPT: dict[str, dict[str, str]] = {}

_CUSUM_BASIS = "CAMBER judgment: provisional and untuned, shared by every drift detector"
_CUSUM_CAL = (
    "Leave it until labelled fault onsets exist to time against; then calibrate it with "
    "camber.driftvalidation.sweep."
)

DRIFT_PARAM_DOCS["coil_leak_drift"] = {
    "warn_f": _P(
        "°F",
        "CAMBER judgment: screening-grade; above the repeatability of a pair of duct temperature "
        "sensors read against their own past (a fixed calibration offset cancels in a drift)",
        "Freeze a baseline on one known-good period and score another known-good period; set the "
        "floor above the |coil_leak_drift_f| it reads (lbnl-sdahu: 0.0-0.1 °F; lbnl-fcu: 0.0-0.4 "
        "°F).",
        (0.25, 3.0),
        "A warn needs both warn_f and warn_sigma, in the leak's direction.",
    ),
    "fault_f": _P(
        "°F",
        "CAMBER judgment: screening-grade",
        "Keep it well above warn_f.",
        (0.5, 6.0),
    ),
    "warn_sigma": _P(
        "σ (baseline residual standard deviations)",
        "CAMBER judgment: screening-grade, the same sigma floor as coil_valve_drift",
        "Score known-good periods against each other and set it above the largest "
        "|coil_leak_drift_sigma| they read. The published 10 % leak on lbnl-sdahu reads 3.5-3.7σ, "
        "so a floor above about 3.4 misses it; on lbnl-fcu floors from 2.0 to 3.0 change the "
        "false alarms from 2/33 to 0/33 and miss no leak.",
        (1.5, 6.0),
    ),
    "fault_sigma": _P(
        "σ",
        "CAMBER judgment: screening-grade, the same sigma floor as coil_valve_drift",
        "Keep it above warn_sigma.",
        (2.5, 10.0),
    ),
    "valve_closed_thr": _P(
        "% of valve stroke",
        "public source: PNNL Re-tuning Ch.5 with CAMBER judgment (leaking_valve's deadband)",
        "Set it just above the position the valve commands read when the BAS commands them closed "
        "(their 95th percentile on off hours).",
        (0.0, 15.0),
    ),
    "fan_on_min": _P(
        "fraction (mean fan status over a sample)",
        "CAMBER judgment: a resampled hour counts as fan-on when the fan ran at least half of it, "
        "as in the other drift detectors",
        "Raise it towards 1.0 on a unit whose fan cycles within the hour, so only hours with air "
        "moving throughout are judged (a cycling fan-coil's leak hours can then all drop out).",
        (0.1, 1.0),
    ),
    "fan_speed_thr": _P(
        "% of full fan speed",
        "CAMBER judgment: any speed above a near-zero command counts as running",
        "Set it just above the speed signal the drive reports when stopped.",
        (0.0, 30.0),
        "Used only when no fan status is mapped.",
    ),
    "occupied_only": _P(
        "flag",
        "CAMBER judgment: a leak shows whenever the unit runs (leaking_valve's default); the "
        "baseline already holds the unit's own unoccupied behaviour",
        "Turn it on when unoccupied fan-on hours (night cycling, warm-up) are too few or too "
        "erratic to fit; compare coil_leak_baseline_sigma_f with it on and off on a known-good "
        "period.",
        (False, True),
        "Reads the trended occupancy (the OCCUPANCY role) when it has values, else assumes "
        "weekdays 07-18; the coil_leak_occupancy_gate metric says which.",
    ),
    "use_coil_leaving": _P(
        "flag",
        "CAMBER judgment: a coil's own leaving-air sensor isolates the coil from the fan and the "
        "supply-air sensor",
        "Turn it off only to compare with the supply-air path; the baseline must be fitted on the "
        "same sensor it scores (the detector declines otherwise).",
        (False, True),
    ),
    "judge_heating_on_supply_air": _P(
        "flag",
        "CAMBER judgment: on a single-duct unit the supply air leaves the heating coil",
        "Set it false when the mapped supply air does not pass the heating coil (a dual-duct unit "
        "whose supply_air_temp is the cold deck); map the heating coil's leaving air instead.",
        (False, True),
        "Heating coil only. False with no HEAT_COIL_LEAVING_TEMP declines the heating instance.",
    ),
    "min_mat_span_f": _P(
        "°F (mixed-air range of the baseline)",
        "CAMBER judgment: a narrower range does not identify a slope",
        "Leave it; below it the baseline is a flat level that scores only current hours inside its "
        "mixed-air band (coil_leak_n_out_of_scope counts the rest).",
        (5.0, 30.0),
    ),
    "slack_sigma": _P("σ", _CUSUM_BASIS, _CUSUM_CAL, (0.25, 2.0)),
    "limit_sigma": _P("σ", _CUSUM_BASIS, _CUSUM_CAL, (4.0, 20.0)),
    "clip_sigma": _P("σ", _CUSUM_BASIS, _CUSUM_CAL, (2.0, 8.0)),
    "min_consecutive": _P("count (samples)", _CUSUM_BASIS, _CUSUM_CAL, (1, 48)),
}
DRIFT_EXEMPT["coil_leak_drift"] = {
    "store": "the injected baseline store",
    "site": "identity",
    "run_id": "identity",
    "coil": "identity: set by the family entry's coil_leak list",
    "freeze_if_missing": "set by the drift run (never from a config)",
}


def _drift_factories() -> dict:
    from .coil_leak_rule import CoilLeakDrift

    return {"coil_leak_drift": CoilLeakDrift}


def documented_drift_rules() -> list[str]:
    """The sorted names of the opt-in drift detectors documented in :data:`DRIFT_PARAM_DOCS`."""
    return sorted(_drift_factories())


def drift_rule_params(rule: str) -> list[RuleParam]:
    """Every tunable parameter of the opt-in drift detector ``rule``, with its default and doc.

    Defaults come from the constructor signature; :data:`DRIFT_EXEMPT` parameters are left out.
    Raises ``KeyError`` for an unknown detector.
    """
    cls = _drift_factories()[rule]
    exempt = DRIFT_EXEMPT.get(rule, {})
    docs = DRIFT_PARAM_DOCS.get(rule, {})
    out = []
    for p in _signature_params(cls):
        if p.name in exempt:
            continue
        default = None if p.default is inspect.Parameter.empty else p.default
        out.append(RuleParam(rule, p.name, default, docs.get(p.name)))
    return out


# ==== end 0100-leak-drift ====


# --------------------------------------------------------------------------- rendering


def _plain(v):
    """A default as JSON-shaped data (tuples -> lists, enums -> their value)."""
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if hasattr(v, "value") and not isinstance(v, (int, float, str, bool)):
        return v.value
    return v


def describe(rule: str) -> dict:
    """One rule's tunables as JSON-shaped data: name, parameters with default and docs, notes."""
    params = []
    for rp in rule_params(rule):
        d = rp.doc
        entry = {"name": rp.name, "default": _plain(rp.default)}
        if d is not None:
            entry.update(
                unit=d.unit,
                basis=d.basis,
                calibrate=d.calibrate,
                range=_plain(d.range),
            )
            if d.note:
                entry["note"] = d.note
        if rp.delegated_from:
            entry["passed_to"] = rp.delegated_from
        params.append(entry)
    out: dict = {"rule": rule, "params": params}
    if rule in FIXED:
        out["fixed_in_code"] = FIXED[rule]
    return out


def config_snippet(rules: list[str]) -> dict:
    """A ready-to-paste ``"rules"`` section: each rule with its params at their defaults."""
    entries: list = []
    for rule in rules:
        params = {rp.name: _plain(rp.default) for rp in rule_params(rule)}
        entries.append({"name": rule, "params": params} if params else rule)
    return {"rules": entries}


def _per_key(unit: str, default=None) -> bool:
    """True for a dict-valued (tier map) parameter, whose range applies to each value."""
    return isinstance(default, dict) or "per key" in unit


def _keywords(r: tuple) -> tuple:
    """The leading keywords of a keyword-or-number range (``("auto", 25.0, 60.0)`` -> "auto")."""
    if all(isinstance(v, (str, bool)) for v in r):
        return ()
    return tuple(v for v in r if isinstance(v, str))


def _fmt_range(r: tuple, default=None, unit: str = "") -> str:
    if all(isinstance(v, (str, bool)) for v in r):
        return "one of " + ", ".join(json_value(v) for v in r)
    each = "each value: " if _per_key(unit, default) else ""
    words = "".join(f"{json_value(v)}, or " for v in _keywords(r))
    return f"{words}{each}{r[-2] if words else r[0]} to {r[-1]}"


def json_value(v) -> str:
    """A default as it would be written in a JSON config."""
    import json

    return json.dumps(_plain(v))


def snippet_yaml(rules: list[str]) -> str:
    """:func:`config_snippet` as YAML, each parameter preceded by its basis and calibration."""
    from .._yaml import dump_yaml

    snippet = config_snippet(rules)
    notes: dict = {}
    for i, rule in enumerate(rules):
        for rp in rule_params(rule):
            d = rp.doc
            if d is None:
                continue
            rng = _fmt_range(d.range, rp.default, d.unit)
            text = f"{d.unit}; {rng}. Basis: {d.basis}. Calibrate: {d.calibrate}"
            if d.note:
                text += f" Note: {d.note}"
            notes[("rules", i, "params", rp.name)] = text
    header = (
        "Tunable parameters at their defaults (camber rules params). Keep only the ones you "
        "change, and record how you calibrated each one in a comment beside it: the data, the "
        "period, and that you scored on different data (docs/TUNING.md)."
    )
    return dump_yaml(snippet, notes=notes, header=header)


def render_text(rules: list[str], width: int = 100) -> str:
    """The human-readable listing printed by ``camber rules params``."""
    import textwrap

    def field(label: str, text: str) -> list:
        return textwrap.wrap(
            f"{label}: {text}",
            width=width,
            initial_indent="      ",
            subsequent_indent="        ",
            break_on_hyphens=False,
        )

    lines: list = []
    for rule in rules:
        info = describe(rule)
        lines.append(rule)
        if not info["params"]:
            lines.append("  no tunable parameters")
        for p in info["params"]:
            unit = p.get("unit", "undocumented")
            lines.append(f"  {p['name']} = {json_value(p['default'])}  [{unit}]")
            if "passed_to" in p:
                lines += field("passed through to", p["passed_to"])
            if "range" in p:
                lines += field("range", _fmt_range(tuple(p["range"]), p["default"], p["unit"]))
                lines += field("basis", p["basis"])
                lines += field("calibrate", p["calibrate"])
            if p.get("note"):
                lines += field("note", p["note"])
        if info.get("fixed_in_code"):
            lines += field("fixed in code (not yet tunable)", info["fixed_in_code"])
        lines.append("")
    return "\n".join(lines)
