"""Standards-grounded AHU fault detection per ASHRAE Guideline 36-2021 §5.16.14.

This is a clean-room implementation of the G36 automatic fault-detection logic:
an operating-state classifier (OS#1-5) followed by fault conditions FC#1-FC#15,
each evaluated only in the operating states where it applies. The equations are
energy/mass balances (facts, not copyrightable); section numbers are cited for
provenance. No G36 text or tables are reproduced verbatim.

Why this matters: our other diagnostics use heuristic thresholds we chose; this
engine flags *deviation from the G36-required sequence* using the standard's own
equations and default tolerances. Per G36, FC#2, #3, and #5-#13 satisfy the
California Title 24 §120.2(i)7 economizer fault-detection requirement.

Applicability (denominator) convention: each fault is evaluated **only in the
operating states G36 §5.16.14.9 lists for it** (see ``OS_FAULTS``); the reported
``fault_pct`` is the trip rate over *those* hours. This operating-state gating is
the deliberate, more G36-faithful definition of "when a fault applies" -- the
standard ties each fault to the operating state(s) in which it is meaningful. The
tradeoff is a narrower applicable set than a single-signal gate (an FC counted over
fewer hours), so percentages are computed over a stricter population. A separate
tool that gates on a single signal will report different *magnitudes* for the same
faults firing at the same hours -- a denominator difference, not an equation
difference. This implementation was cross-validated against open-fdd on real AHU
data and matches to 0.00 pts on a common denominator; see ``docs/ECOSYSTEM.md``.
``run_g36_afdd(..., comparability=True)`` additionally emits a single-signal-gated
(input-validity) fault % for cross-tool reconciliation, without changing the default
operating-state-gated output.

G36 filters (0.91): before any FC is scored, ``run_g36_afdd`` applies the §5.16.14 time filters --
evaluation only while the AHU runs (a fan status / speed / airflow gate; no fan signal declines the
run), ModeDelay after a fan start or a zone-group mode change, AlarmDelay persistence, and the
5-minute rolling averages. An AHU without a heating (cooling) valve is scored as having no such
coil, with the tests of that coil omitted. See the constants below for what was verified in the
public G36 text.

Free cooling (0.98, #94): OS#2 needs the OA damper open beyond its minimum position, per the
§5.16.14 operating-state definitions -- both valves shut at minimum OA (a deadband hour, an
unoccupied recirculation run) is OS#5. See :func:`classify_os` and ``run_g36_afdd(oa_damper_min=)``.

Heating at minimum OA (0.99, #95): by the same definitions OS#1 also needs the OA damper at its
minimum position. A heating hour with the damper open beyond it (plus the same tolerance) is OS#5.

Variable conventions (all temperatures degF here):
  SAT/MAT/RAT/OAT supply/mixed/return/outdoor air temps; SATSP supply-air-temp
  setpoint; HC/CC heating/cooling valve command %; FS supply-fan speed %; DSP/
  DSPSP duct static pressure + setpoint; pct_oa actual outdoor-air fraction %;
  pct_oa_min the minimum-OA setpoint %. CCET/CCLT, HCET/HCLT coil entering/leaving
  temps (often MAT/SAT depending on AHU configuration).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "G36Thresholds",
    "OS_HEATING",
    "OS_FREECOOL",
    "OS_MECH_ECON",
    "OS_MECH_MINOA",
    "OS_UNKNOWN",
    "OS_UNCLASSIFIED",
    "classify_os",
    "OS_FAULTS",
    "FC_DESC",
    "G36Result",
    "run_g36_afdd",
    "MODE_DELAY_MIN",
    "ALARM_DELAY_MIN",
    "AVG_WINDOW_MIN",
    "FC_OMIT_NO_HEATING",
    "FC_OMIT_NO_COOLING",
    "OA_DAMPER_TOL",
    "OA_MIN_LEARN_N",
]

# --------------------------------------------------------------------------- #
# G36 §5.16.14 time delays and averaging. Verified against the public text of Addendum p to
# Guideline 36-2021 (approved 2024-02-29), which restates §5.16.14 in full:
#   * AFDD internal-variables table: ModeDelay 30 min ("suspend fault-condition evaluation after a
#     change in mode"), AlarmDelay 30 min ("a fault condition must persist" that long before it is
#     reported), TestModeDelay 120 min (not modelled: test mode is a commissioning action);
#   * suspension clause: evaluation is suspended while the AHU is not operating, and for ModeDelay
#     after a change in the mode (e.g. warm-up to occupied) of any zone group the AHU serves;
#   * persistence clause: a fault condition must be TRUE continuously for AlarmDelay before it is
#     reported;
#   * averaging clause: five-minute rolling averages, one-minute sampling, of SAT, MAT, RAT, OAT,
#     DSP and the coil entering/leaving temperatures.
# The same values appear in the first-public-review Addendum u to G36-2018. G36 keys ModeDelay to
# the zone-group *mode*, which it states is distinct from the AHU operating state; an OS change is
# counted by FC#4, and a transient at an OS change is filtered by AlarmDelay, not by ModeDelay.
# --------------------------------------------------------------------------- #
MODE_DELAY_MIN = 30.0
ALARM_DELAY_MIN = 30.0
AVG_WINDOW_MIN = 5.0

#: fault conditions that test a heating coil: omitted on an AHU without one (G36 omits FC#7 when
#: there is no heating coil, and tests of absent components generally). FC#5 applies only in OS#1,
#: which cannot occur without a heating valve, so it needs no entry.
FC_OMIT_NO_HEATING = (7, 15)
#: fault conditions that test a cooling coil: omitted on an AHU without one.
FC_OMIT_NO_COOLING = (13, 14)

# the measured points G36 averages (§5.16.14 averaging clause); commands and setpoints are not
_AVERAGED = ("SAT", "MAT", "RAT", "OAT", "DSP", "CCET", "CCLT", "HCET", "HCLT")


@dataclass
class G36Thresholds:
    """Tunable tolerances; defaults are the G36 Table 5.16.14.7 initial values
    (converted to degF where the standard gives degC), derived from NISTIR 7365."""

    dT_sf: float = 2.0  # fan-heat temperature rise (degF)
    dT_min: float = 10.0  # min |OAT-RAT| to evaluate economizer faults (degF)
    e_sat: float = 2.0  # SAT sensor tolerance
    e_rat: float = 2.0  # RAT sensor tolerance
    e_mat: float = 5.0  # MAT sensor tolerance
    e_oat: float = 5.0  # OAT sensor tolerance (5 global / 2 if local)
    e_flow: float = 0.30  # airflow / OA-fraction tolerance (fraction)
    e_vfdspd: float = 0.05  # fan-speed tolerance (fraction)
    e_dsp: float = 0.1  # duct-static tolerance (in. w.c.)
    e_ccet: float = 5.0
    e_cclt: float = 2.0
    e_hcet: float = 5.0
    e_hclt: float = 2.0
    valve_on: float = 99.0  # valve commanded "fully open" threshold (%)
    fan_full: float = 99.0  # fan "full speed" threshold (%)
    # Fan-heat term in FC#14 (degF). G36 prints "+ dT_sf" with a footnote that the fan-heat factor
    # is included or not depending on where the coil sensors sit; None keeps the printed +dT_sf.
    # When SAT (downstream of the supply fan) stands in for the cooling-coil leaving temperature,
    # the air is dT_sf warmer than it left the coil, so the correct term is -dT_sf.
    fc14_fan_heat: float | None = None


# Operating states (G36 §5.16.14 operating-state definitions). Classification keys off the heating-
# and cooling-valve commands and the OA damper command:
#   OS#1 heating coil active, cooling coil not, OA damper at its minimum position (0.99, #95: at or
#        below the minimum plus ``oa_damper_tol``, the same test OS#2 makes in reverse);
#   OS#2 both coils inactive with the economizer modulating, i.e. the OA damper open beyond its
#        minimum position (0.98, #94: shut valves alone are not free cooling);
#   OS#3 mechanical cooling on 100 % OA (damper at/above ``econ_damper_open``);
#   OS#4 mechanical cooling on minimum OA;
#   OS#5 none of #1-#4 applies -- simultaneous heating and cooling, (0.98) a running fan with
#        both coils inactive and the OA damper at or below its minimum (deadband, unoccupied
#        recirculation), and (0.99) heating with the OA damper open beyond its minimum. Only the
#        state-independent FC#1-#4 apply there.
# Without an OA damper point OS#1 and OS#2 are read from the valves alone (the pre-0.98 reading),
# and the result says so in a caveat.
OS_HEATING = 1  # heating coil active at minimum OA (the OA damper at its minimum, 0.99)
OS_FREECOOL = 2  # modulating economizer (damper beyond its minimum), no mechanical cooling
OS_MECH_ECON = 3  # mechanical + 100% economizer
OS_MECH_MINOA = 4  # mechanical cooling, minimum OA
OS_UNKNOWN = 5  # no other OS applies: simultaneous heat/cool, idle or heating at the wrong OA
# Not a G36 state: a valve command is missing (NaN) so the interval cannot be classified. No FC is
# evaluated there -- reading a NaN valve as "closed" would call the interval free cooling (OS#2)
# and dilute every OS#2 fault rate with intervals nobody observed. Likewise (0.98) a missing OA
# damper reading with both coils inactive, and (0.99) with the heating coil alone active, when the
# frame has an OA damper point.
OS_UNCLASSIFIED = 0

#: OA damper tolerance (percentage points of stroke) above the minimum position before an interval
#: with both coils inactive counts as economizing (OS#2) -- CAMBER judgment (0.98, #94) -- and
#: before a heating interval counts as off its minimum (not OS#1; 0.99, #95)
OA_DAMPER_TOL = 5.0
#: fan-on intervals of mechanical cooling below ``econ_damper_open`` needed to learn the minimum OA
#: damper position from the data (0.98, #94); fewer, and the minimum is taken as 0 % (closed)
OA_MIN_LEARN_N = 24


def classify_os(
    hc,
    cc,
    oa_damper=None,
    valve_thr=5.0,
    econ_damper_open=80.0,
    oa_damper_min=0.0,
    oa_damper_tol=OA_DAMPER_TOL,
):
    """Operating state for one interval from valve commands (+ OA damper).

    hc, cc, oa_damper are % (0-100). Returns an int OS code 1-5, or :data:`OS_UNCLASSIFIED` (0)
    when either valve command is missing (``None``/NaN).

    With both coils inactive the interval is OS#2 (free cooling) only when the OA damper is open
    beyond ``oa_damper_min + oa_damper_tol``; at or below that it is OS#5 (0.98, #94), and a NaN
    damper reading leaves it unclassified. With the heating coil alone active it is OS#1 only when
    the damper is at its minimum (at or below ``oa_damper_min + oa_damper_tol``); beyond it the
    interval is OS#5 (0.99, #95), and a NaN damper reading leaves it unclassified.
    ``oa_damper=None`` (no damper point) keeps the valves-only reading: OS#1 / OS#2.
    """
    if hc is None or cc is None or hc != hc or cc != cc:  # x != x -> NaN
        return OS_UNCLASSIFIED
    heating = hc > valve_thr
    cooling = cc > valve_thr
    if heating and cooling:
        return OS_UNKNOWN
    if heating:
        if oa_damper is None:
            return OS_HEATING
        if oa_damper != oa_damper:
            return OS_UNCLASSIFIED
        return OS_HEATING if oa_damper <= oa_damper_min + oa_damper_tol else OS_UNKNOWN
    if not cooling:
        if oa_damper is None:
            return OS_FREECOOL
        if oa_damper != oa_damper:
            return OS_UNCLASSIFIED
        return OS_FREECOOL if oa_damper > oa_damper_min + oa_damper_tol else OS_UNKNOWN
    # cooling on: economizer (high OA damper) -> OS#3, else minimum OA -> OS#4
    if oa_damper is not None and oa_damper >= econ_damper_open:
        return OS_MECH_ECON
    return OS_MECH_MINOA


# Which fault conditions are evaluated in each operating state (G36 §5.16.14.9).
OS_FAULTS = {
    OS_HEATING: [1, 2, 3, 4, 5, 6, 7, 14],
    OS_FREECOOL: [1, 2, 3, 4, 8, 9, 12, 14, 15],
    OS_MECH_ECON: [1, 2, 3, 4, 10, 11, 12, 13, 15],
    OS_MECH_MINOA: [1, 2, 3, 4, 6, 12, 13, 15],
    OS_UNKNOWN: [1, 2, 3, 4],
}


# Each FC is a predicate on a row dict of averaged values. Returns True if the
# fault equation is satisfied (fault present). Equations per G36 Table 5.16.14.8.
def _fc1(r, k):  # DSP too low with fan at full speed
    return (
        r.get("DSP") is not None
        and r.get("DSPSP") is not None
        and r.get("FS") is not None
        and r["DSP"] < r["DSPSP"] - k.e_dsp
        and r["FS"] >= k.fan_full * (1 - k.e_vfdspd)
    )


def _fc2(r, k):  # MAT too low; should be between OAT and RAT
    if any(r.get(x) is None for x in ("MAT", "RAT", "OAT")):
        return False
    return r["MAT"] + k.e_mat < min(r["RAT"] - k.e_rat, r["OAT"] - k.e_oat)


def _fc3(r, k):  # MAT too high
    if any(r.get(x) is None for x in ("MAT", "RAT", "OAT")):
        return False
    return r["MAT"] - k.e_mat > max(r["RAT"] + k.e_rat, r["OAT"] + k.e_oat)


def _fc4(r, k):  # too many operating-state changes (instability)
    return r.get("dOS") is not None and r["dOS"] > k.os_max


def _fc5(r, k):  # SAT too low; should be higher than MAT (in heating)
    if any(r.get(x) is None for x in ("SAT", "MAT")):
        return False
    return r["SAT"] + k.e_sat <= r["MAT"] - k.e_mat + k.dT_sf


def _fc6(r, k):  # OA fraction off (too low/high vs minimum)
    if any(r.get(x) is None for x in ("RAT", "OAT", "pct_oa", "pct_oa_min")):
        return False
    return (
        abs(r["RAT"] - r["OAT"]) >= k.dT_min
        and abs(r["pct_oa"] - r["pct_oa_min"]) > k.e_flow * 100.0
    )


def _fc7(r, k):  # SAT too low in full heating
    if any(r.get(x) is None for x in ("SAT", "SATSP", "HC")):
        return False
    return r["SAT"] < r["SATSP"] - k.e_sat and r["HC"] >= k.valve_on


def _fc8(r, k):  # SAT and MAT should be ~equal (free cooling)
    if any(r.get(x) is None for x in ("SAT", "MAT")):
        return False
    return abs(r["SAT"] - k.dT_sf - r["MAT"]) > np.hypot(k.e_sat, k.e_mat)


def _fc9(r, k):  # OAT too high for free cooling
    if any(r.get(x) is None for x in ("OAT", "SATSP")):
        return False
    return r["OAT"] - k.e_oat > r["SATSP"] - k.dT_sf + k.e_sat


def _fc10(r, k):  # OAT and MAT should be ~equal (100% economizer)
    if any(r.get(x) is None for x in ("MAT", "OAT")):
        return False
    return abs(r["MAT"] - r["OAT"]) > np.hypot(k.e_mat, k.e_oat)


def _fc11(r, k):  # OAT too low for mechanical cooling
    if any(r.get(x) is None for x in ("OAT", "SATSP")):
        return False
    return r["OAT"] + k.e_oat < r["SATSP"] - k.dT_sf - k.e_sat


def _fc12(r, k):  # SAT too high; should be less than MAT (cooling)
    if any(r.get(x) is None for x in ("SAT", "MAT")):
        return False
    return r["SAT"] - k.e_sat - k.dT_sf >= r["MAT"] + k.e_mat


def _fc13(r, k):  # SAT too high in full cooling
    if any(r.get(x) is None for x in ("SAT", "SATSP", "CC")):
        return False
    return r["SAT"] > r["SATSP"] + k.e_sat and r["CC"] >= k.valve_on


def _fc14(r, k):  # temperature drop across an inactive cooling coil (leak/stuck)
    cet, clt = r.get("CCET"), r.get("CCLT")
    if cet is None or clt is None:
        return False
    fan_heat = k.dT_sf if getattr(k, "fc14_fan_heat", None) is None else k.fc14_fan_heat
    return cet - clt >= np.hypot(k.e_ccet, k.e_cclt) + fan_heat


def _fc15(r, k):  # temperature rise across an inactive heating coil (leak/stuck)
    het, hlt = r.get("HCET"), r.get("HCLT")
    if het is None or hlt is None:
        return False
    return hlt - het >= np.hypot(k.e_hcet, k.e_hclt) + k.dT_sf


_FCS = {
    1: _fc1,
    2: _fc2,
    3: _fc3,
    4: _fc4,
    5: _fc5,
    6: _fc6,
    7: _fc7,
    8: _fc8,
    9: _fc9,
    10: _fc10,
    11: _fc11,
    12: _fc12,
    13: _fc13,
    14: _fc14,
    15: _fc15,
}


# --------------------------------------------------------------------------- #
# Vectorized engine. These reproduce the scalar predicates above element-wise
# over whole columns; a missing column yields an all-False array, and any NaN in
# an input makes that interval's comparisons evaluate False -- exactly the scalar
# guard (``any(... is None) -> return False``). Used by run_g36_afdd; the scalar
# classify_os / _fc* remain the readable reference (and public API).
# --------------------------------------------------------------------------- #
def _classify_os_vec(hc, cc, oa, valve_thr, econ_damper_open, oa_econ=None):
    """Operating state per interval over arrays.

    A NaN valve command -> :data:`OS_UNCLASSIFIED` (no FC applies); a NaN OA damper with cooling
    on -> OS#4 (minimum OA), as in the scalar. ``oa_econ`` is the OA damper position above which
    an interval with both coils inactive is economizing (OS#2): at or below it the interval is
    OS#5, and a NaN damper there is unclassified. A heating interval is OS#1 only at or below it
    (the damper at its minimum, 0.99 #95), OS#5 beyond it, and unclassified on a NaN damper.
    ``None`` (no damper point) keeps the valves-only reading. It is a scalar, or an array of one
    position per interval for a seasonal minimum (0.101, #105).
    """
    missing = np.isnan(hc) | np.isnan(cc)
    heating = hc > valve_thr
    cooling = cc > valve_thr
    oa_open = oa >= econ_damper_open
    idle = ~heating & ~cooling
    heat_only = heating & ~cooling
    if oa_econ is None:
        idle_unclassified = np.zeros(len(hc), dtype=bool)
        idle_at_min = np.zeros(len(hc), dtype=bool)
        heat_unclassified = np.zeros(len(hc), dtype=bool)
        heat_above_min = np.zeros(len(hc), dtype=bool)
    else:
        idle_unclassified = idle & np.isnan(oa)
        heat_unclassified = heat_only & np.isnan(oa)
        with np.errstate(invalid="ignore"):
            idle_at_min = idle & (oa <= oa_econ)
            heat_above_min = heat_only & (oa > oa_econ)
    return np.select(
        [
            missing,
            heating & cooling,
            heat_unclassified,
            heat_above_min,
            heating,
            idle_unclassified,
            idle_at_min,
            ~cooling,
            oa_open,
        ],
        [
            OS_UNCLASSIFIED,
            OS_UNKNOWN,
            OS_UNCLASSIFIED,
            OS_UNKNOWN,
            OS_HEATING,
            OS_UNCLASSIFIED,
            OS_UNKNOWN,
            OS_FREECOOL,
            OS_MECH_ECON,
        ],
        default=OS_MECH_MINOA,
    ).astype(int)


def _learn_oa_min(hc, cc, oa, on, valve_thr, econ_damper_open):
    """``(position, n)``: the OA damper's minimum position learned from the frame.

    The median damper command over fan-on intervals of mechanical cooling with the damper below
    ``econ_damper_open`` -- the G36 OS#4 position, where the sequence holds the damper at its
    minimum. ``(None, n)`` with fewer than :data:`OA_MIN_LEARN_N` such intervals.
    """
    with np.errstate(invalid="ignore"):
        sel = on & (cc > valve_thr) & ~(hc > valve_thr) & (oa < econ_damper_open) & ~np.isnan(oa)
    n = int(sel.sum())
    if n < OA_MIN_LEARN_N:
        return None, n
    return float(np.median(oa[sel])), n


def _oa_min_by_month(oa_damper_min, by_month) -> dict | None:
    """The validated ``{month: position}`` seasonal OA damper minimum (0.101, #105), or None."""
    if not by_month:
        return None
    out = {int(k): float(v) for k, v in by_month.items()}
    if any(not 1 <= m <= 12 for m in out):
        raise ValueError("oa_damper_min_by_month keys must be months 1-12")
    if oa_damper_min is None:
        raise ValueError(
            "oa_damper_min_by_month needs oa_damper_min (the minimum position in the other months)"
        )
    return out


def _false(n):
    return np.zeros(n, dtype=bool)


def _vfc1(c, dos, k, n):
    if c["DSP"] is None or c["DSPSP"] is None or c["FS"] is None:
        return _false(n)
    return (c["DSP"] < c["DSPSP"] - k.e_dsp) & (c["FS"] >= k.fan_full * (1 - k.e_vfdspd))


def _vfc2(c, dos, k, n):
    if c["MAT"] is None or c["RAT"] is None or c["OAT"] is None:
        return _false(n)
    return c["MAT"] + k.e_mat < np.minimum(c["RAT"] - k.e_rat, c["OAT"] - k.e_oat)


def _vfc3(c, dos, k, n):
    if c["MAT"] is None or c["RAT"] is None or c["OAT"] is None:
        return _false(n)
    return c["MAT"] - k.e_mat > np.maximum(c["RAT"] + k.e_rat, c["OAT"] + k.e_oat)


def _vfc4(c, dos, k, n):
    return dos > k.os_max  # NaN > thr -> False


def _vfc5(c, dos, k, n):
    if c["SAT"] is None or c["MAT"] is None:
        return _false(n)
    return c["SAT"] + k.e_sat <= c["MAT"] - k.e_mat + k.dT_sf


def _vfc6(c, dos, k, n):
    if any(c[x] is None for x in ("RAT", "OAT", "pct_oa", "pct_oa_min")):
        return _false(n)
    return (np.abs(c["RAT"] - c["OAT"]) >= k.dT_min) & (
        np.abs(c["pct_oa"] - c["pct_oa_min"]) > k.e_flow * 100.0
    )


def _vfc7(c, dos, k, n):
    if c["SAT"] is None or c["SATSP"] is None or c["HC"] is None:
        return _false(n)
    return (c["SAT"] < c["SATSP"] - k.e_sat) & (c["HC"] >= k.valve_on)


def _vfc8(c, dos, k, n):
    if c["SAT"] is None or c["MAT"] is None:
        return _false(n)
    return np.abs(c["SAT"] - k.dT_sf - c["MAT"]) > np.hypot(k.e_sat, k.e_mat)


def _vfc9(c, dos, k, n):
    if c["OAT"] is None or c["SATSP"] is None:
        return _false(n)
    return c["OAT"] - k.e_oat > c["SATSP"] - k.dT_sf + k.e_sat


def _vfc10(c, dos, k, n):
    if c["MAT"] is None or c["OAT"] is None:
        return _false(n)
    return np.abs(c["MAT"] - c["OAT"]) > np.hypot(k.e_mat, k.e_oat)


def _vfc11(c, dos, k, n):
    if c["OAT"] is None or c["SATSP"] is None:
        return _false(n)
    return c["OAT"] + k.e_oat < c["SATSP"] - k.dT_sf - k.e_sat


def _vfc12(c, dos, k, n):
    if c["SAT"] is None or c["MAT"] is None:
        return _false(n)
    return c["SAT"] - k.e_sat - k.dT_sf >= c["MAT"] + k.e_mat


def _vfc13(c, dos, k, n):
    if c["SAT"] is None or c["SATSP"] is None or c["CC"] is None:
        return _false(n)
    return (c["SAT"] > c["SATSP"] + k.e_sat) & (c["CC"] >= k.valve_on)


def _vfc14(c, dos, k, n):
    if c["CCET"] is None or c["CCLT"] is None:
        return _false(n)
    fan_heat = k.dT_sf if k.fc14_fan_heat is None else k.fc14_fan_heat
    return c["CCET"] - c["CCLT"] >= np.hypot(k.e_ccet, k.e_cclt) + fan_heat


def _vfc15(c, dos, k, n):
    if c["HCET"] is None or c["HCLT"] is None:
        return _false(n)
    return c["HCLT"] - c["HCET"] >= np.hypot(k.e_hcet, k.e_hclt) + k.dT_sf


_VFCS = {
    1: _vfc1,
    2: _vfc2,
    3: _vfc3,
    4: _vfc4,
    5: _vfc5,
    6: _vfc6,
    7: _vfc7,
    8: _vfc8,
    9: _vfc9,
    10: _vfc10,
    11: _vfc11,
    12: _vfc12,
    13: _vfc13,
    14: _vfc14,
    15: _vfc15,
}

# OS codes in which each FC is evaluated (inverse of OS_FAULTS).
_FC_STATES = {fc: [os for os, fcs in OS_FAULTS.items() if fc in fcs] for fc in _FCS}

# Measure columns each FC needs. Used only by the opt-in single-signal
# comparability denominator (the "input-validity" gate): the set of hours over
# which the fault *could* be computed at all, regardless of operating state. FC4
# keys off dOS, which is always derivable, so it has no column requirement.
_FC_INPUTS = {
    1: ("DSP", "DSPSP", "FS"),
    2: ("MAT", "RAT", "OAT"),
    3: ("MAT", "RAT", "OAT"),
    4: (),
    5: ("SAT", "MAT"),
    6: ("RAT", "OAT", "pct_oa", "pct_oa_min"),
    7: ("SAT", "SATSP", "HC"),
    8: ("SAT", "MAT"),
    9: ("OAT", "SATSP"),
    10: ("MAT", "OAT"),
    11: ("OAT", "SATSP"),
    12: ("SAT", "MAT"),
    13: ("SAT", "SATSP", "CC"),
    14: ("CCET", "CCLT"),
    15: ("HCET", "HCLT"),
}


def _input_valid_mask(cols, fc, n):
    """Rows where every input FC ``fc`` needs is present and non-NaN.

    This is the single-signal (input-validity) denominator used by the
    comparability mode: a fault is "applicable" wherever its inputs exist, without
    the operating-state gating Camber applies by default. A required column missing
    entirely yields an all-False mask (the fault is unrunnable, denominator 0).
    """
    reqs = _FC_INPUTS[fc]
    if not reqs:
        return np.ones(n, dtype=bool)
    mask = np.ones(n, dtype=bool)
    for name in reqs:
        arr = cols.get(name)
        if arr is None:
            return np.zeros(n, dtype=bool)
        mask &= ~np.isnan(arr)
    return mask


FC_DESC = {
    1: "duct static too low at full fan",
    2: "mixed-air temp too low",
    3: "mixed-air temp too high",
    4: "unstable control (too many OS changes)",
    5: "supply-air too low in heating",
    6: "outdoor-air fraction off",
    7: "supply-air too low in full heating",
    8: "SAT/MAT mismatch (free cooling)",
    9: "OAT too high for free cooling",
    10: "OAT/MAT mismatch (economizer)",
    11: "OAT too low for mechanical cooling",
    12: "supply-air too high (cooling)",
    13: "supply-air too high in full cooling",
    14: "temp drop across inactive cooling coil (leak/stuck)",
    15: "temp rise across inactive heating coil (leak/stuck)",
}


# add os_max to thresholds (kept here so the dataclass stays focused on tolerances)
G36Thresholds.os_max = 7  # type: ignore[attr-defined]  # attached outside the dataclass body


@dataclass
class G36Result:
    """ASHRAE Guideline 36 fault-condition results (per-FC trip rates) for one AHU."""

    equip: str
    n_intervals: int
    os_distribution: dict  # OS code -> count (fan-on intervals only)
    fault_pct: dict  # FC number -> % of *applicable* intervals tripped
    fault_n_applicable: dict  # FC number -> intervals where its OS applied
    coverage_start: str
    coverage_end: str
    # Opt-in comparability output (None unless run_g36_afdd(comparability=True)):
    # FC number -> % over the single-signal (input-validity) denominator, for
    # cross-tool reconciliation (e.g. open-fdd). Same fault equation/fires as
    # fault_pct, different denominator. See docs/ECOSYSTEM.md.
    fault_pct_singlesignal: dict | None = None
    # fan-on intervals with a missing valve command: no operating state, so no FC was evaluated
    n_unclassified: int = 0
    # --- provisional (0.91) -------------------------------------------------------------------
    #: what the unit-running gate was read from ("fan status", "fan speed proxy", "airflow proxy",
    #: or "ungated (caller asserts the fan ran)"); "" on a declined result
    fan_gate: str = ""
    #: intervals the gate classed as fan off (never evaluated: G36 suspends AFDD then)
    n_fan_off: int = 0
    #: fan-on intervals inside ModeDelay after a fan start or a zone-group mode change
    n_suspended: int = 0
    #: FC number -> hours reported (after AlarmDelay), from the median sampling interval
    fault_hours: dict = field(default_factory=dict)
    #: FC number -> why it was not evaluated at all (e.g. no heating coil for FC#7)
    omitted: dict = field(default_factory=dict)
    #: FC number -> input columns absent from the frame (the FC can never fire; its fault_pct
    #: still reads 0.0 over the OS-gated denominator -- callers decide whether to decline it)
    missing_inputs: dict = field(default_factory=dict)
    #: "could not evaluate X" notes (see camber.rules.base, honesty convention)
    caveats: list = field(default_factory=list)
    #: why the whole run was declined (no fan signal, no valve commands); None when it ran
    declined: str | None = None
    #: ModeDelay / AlarmDelay / averaging window actually applied, in minutes
    delays: dict = field(default_factory=dict)
    # --- provisional (0.98, #94) ----------------------------------------------------------------
    #: the OA damper minimum position (%) OS#2 was judged against; None without an OA damper point
    oa_damper_min: float | None = None
    #: where it came from: "caller", "learned (median of N OS#4 intervals)", "assumed 0 % (...)",
    #: or "" without an OA damper point
    oa_damper_min_source: str = ""
    #: fan-on intervals with both coils inactive and the OA damper at or below its minimum (plus the
    #: tolerance): OS#5, not free cooling
    n_idle_at_min_oa: int = 0
    #: fan-on intervals excluded by the caller's ``occupied`` mask (never evaluated)
    n_unoccupied: int = 0
    # --- provisional (0.99, #95) ----------------------------------------------------------------
    #: fan-on intervals with the heating coil alone active and the OA damper open beyond its
    #: minimum (plus the tolerance): OS#5, not OS#1
    n_heating_above_min_oa: int = 0
    #: per-interval masks (``keep_masks=True`` only): ``FC<n>`` reported, ``FC<n>_app`` evaluated
    #: and applicable, plus ``fan_on``, ``suspended``, ``os`` and (0.98) ``evaluable``; indexed like
    #: the cleaned frame
    masks: pd.DataFrame | None = None
    # --- provisional (0.101, #105) ---------------------------------------------------------------
    #: the caller's seasonal ``{month: position %}`` override of ``oa_damper_min`` (0.101, #105);
    #: None when not given or without an OA damper point
    oa_damper_min_by_month: dict | None = None

    def as_dict(self):
        """Return the result as a plain dict (faults flattened to FC<n>_pct keys).

        When the comparability output is present, also emits FC<n>_pct_singlesignal
        keys; otherwise the dict is identical to the default-mode output.
        """
        d = {
            "equip": self.equip,
            "n_intervals": self.n_intervals,
            "os_distribution": self.os_distribution,
            "n_unclassified": self.n_unclassified,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "fan_gate": self.fan_gate,
            "n_fan_off": self.n_fan_off,
            "n_suspended": self.n_suspended,
            "declined": self.declined,
            "caveats": list(self.caveats),
        }
        d.update({f"FC{n}_pct": self.fault_pct.get(n) for n in _FCS})
        if self.fault_pct_singlesignal is not None:
            d.update({f"FC{n}_pct_singlesignal": self.fault_pct_singlesignal.get(n) for n in _FCS})
        return d


def _median_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    """The frame's typical sampling interval (1 min when it cannot be inferred)."""
    if len(index) < 2:
        return pd.Timedelta(minutes=1)
    d = pd.Series(index).diff().dropna()
    d = d[d > pd.Timedelta(0)]
    return d.median() if len(d) else pd.Timedelta(minutes=1)


def _persistent(mask: np.ndarray, index, step: pd.Timedelta, alarm_delay_min: float) -> np.ndarray:
    """Keep only episodes that stayed TRUE for at least AlarmDelay (G36 persistence clause).

    Each sample stands for one sampling interval, so an episode's duration is ``last - first +
    step``: at 5-minute data a single excursion lasts 5 minutes and is dropped, at hourly data one
    hourly sample already spans 60 minutes. A timestamp gap wider than 1.5 sampling intervals ends
    an episode (missing data is not evidence of persistence). A confirmed episode counts in full --
    the fault existed from its first interval; it was only *reported* AlarmDelay later.
    """
    m = np.asarray(mask, dtype=bool)
    if alarm_delay_min <= 0 or not m.any():
        return m
    t = index.asi8
    starts = m.copy()
    starts[1:] = m[1:] & (~m[:-1] | ((t[1:] - t[:-1]) > 1.5 * step.value))
    run_id = np.cumsum(starts)[m]
    ts = pd.Series(t[m])
    grp = ts.groupby(run_id)
    dur = grp.transform("max").to_numpy() - grp.transform("min").to_numpy() + step.value
    out = np.zeros(len(m), dtype=bool)
    out[m] = dur >= pd.Timedelta(minutes=alarm_delay_min).value
    return out


def _mode_suspension(on: np.ndarray, index, step, mode, mode_delay_min: float) -> np.ndarray:
    """Fan-on intervals within ModeDelay of a fan start or a zone-group mode change.

    A fan start is an off->on transition inside the data; a frame that *begins* with the fan on
    has no observed start and is not suspended (the start time is unknown). A timestamp gap wider
    than 1.5 sampling intervals hides what the fan did, so the first fan-on row after a gap is
    treated as a start too.
    """
    n = len(on)
    if mode_delay_min <= 0 or n == 0:
        return np.zeros(n, dtype=bool)
    t = index.asi8
    event = np.zeros(n, dtype=bool)
    prev_on = np.zeros(n, dtype=bool)
    prev_on[1:] = on[:-1]
    gap = np.zeros(n, dtype=bool)
    gap[1:] = (t[1:] - t[:-1]) > 1.5 * step.value
    event[1:] = on[1:] & (~prev_on[1:] | gap[1:])
    if mode is not None:
        m = pd.Series(mode)
        changed = (m != m.shift()) & m.notna() & m.shift().notna()
        event |= changed.to_numpy() & on
    if not event.any():
        return np.zeros(n, dtype=bool)
    last = pd.Series(np.where(event, t, np.nan)).ffill().to_numpy()
    since = t - last  # NaN before the first event -> comparisons False
    with np.errstate(invalid="ignore"):
        return on & (since < pd.Timedelta(minutes=mode_delay_min).value)


def _fan_gate(df: pd.DataFrame):
    """``(mask, source)`` from the frame's fan signals, in the :func:`camber.schedules.fan_on_mask`
    precedence: ``FAN_STATUS``, else ``FS`` (speed) > 0, else ``AIRFLOW`` > 0."""
    from .model.roles import Role
    from .schedules import fan_on_mask

    sig = {}
    for col, role in (
        ("FAN_STATUS", Role.SUPPLY_FAN_STATUS),
        ("FS", Role.SUPPLY_FAN_SPEED),
        ("AIRFLOW", Role.AIRFLOW),
    ):
        if col in df.columns:
            sig[role] = df[col]
    mask, source = fan_on_mask(pd.DataFrame(sig, index=df.index))
    if mask is None:
        return None, source
    return mask.to_numpy(dtype=bool), source


def _declined_result(df, equip, reason, caveats, delays) -> G36Result:
    n = len(df)
    return G36Result(
        equip=equip,
        n_intervals=n,
        os_distribution={o: 0 for o in range(1, 6)},
        fault_pct={fc: None for fc in _FCS},
        fault_n_applicable={fc: 0 for fc in _FCS},
        coverage_start=str(df.index.min()) if n else "",
        coverage_end=str(df.index.max()) if n else "",
        declined=reason,
        caveats=list(caveats) + [f"G36 AFDD not evaluated: {reason}"],
        delays=delays,
    )


def run_g36_afdd(
    df: pd.DataFrame,
    equip: str,
    *,
    thr: G36Thresholds | None = None,
    econ_damper_open: float = 80.0,
    valve_thr: float = 5.0,
    comparability: bool = False,
    fan_gate: str = "auto",
    mode_delay_min: float = MODE_DELAY_MIN,
    alarm_delay_min: float = ALARM_DELAY_MIN,
    avg_window_min: float = AVG_WINDOW_MIN,
    keep_masks: bool = False,
    oa_damper_min: float | None = None,
    oa_damper_tol: float = OA_DAMPER_TOL,
    occupied=None,
    oa_damper_min_by_month: dict | None = None,
) -> G36Result | None:
    """Run the G36 AFDD fault set over an AHU frame.

    ``df`` columns (any subset; faults needing missing inputs are skipped):
    HC, CC, SAT, MAT, RAT, OAT, SATSP, FS, DSP, DSPSP, OA_Damper, pct_oa,
    pct_oa_min, CCET, CCLT, HCET, HCLT, plus the gating inputs FAN_STATUS, AIRFLOW and MODE.
    Index is time. Each FC is evaluated only in intervals whose operating state lists it (G36
    §5.16.14 applicability lists) -- the operating-state denominator convention (see the module
    docstring).

    G36 §5.16.14 filters, applied before any FC is scored (see the module constants for what was
    verified in the public text):

    * **Unit-running gate** (``fan_gate="auto"``): evaluation is suspended while the AHU is not
      operating. The fan is read from ``FAN_STATUS`` (> 0.5), else ``FS`` (speed > 1 %), else
      ``AIRFLOW`` (> 5 % of its 95th percentile) -- :func:`camber.schedules.fan_on_mask`. With none
      of the three the run is **declined** (``declined`` + a caveat), because a fan-off interval
      with both valves shut reads as OS#2 free cooling and trips FC#8/FC#9 on stagnant air.
      ``fan_gate="none"`` evaluates every row (the caller asserts the fan ran) and says so.
    * **ModeDelay** (``mode_delay_min``, default 30): suspended after a fan start (an off->on
      transition in the data) and after any change of the optional ``MODE`` column (a zone-group
      mode code, e.g. unoccupied / warm-up / occupied).
    * **AlarmDelay** (``alarm_delay_min``, default 30): an FC counts only in episodes that stayed
      TRUE continuously for that long (a confirmed episode counts in full).
    * **Averaging** (``avg_window_min``, default 5): rolling time-window means of the measured
      temperatures and duct static over fan-on rows; a no-op at 5-minute or coarser data.

    Set the delays and the window to 0 to score raw per-interval equations.

    **Free cooling needs an open economizer** (0.98, #94). With both coils inactive an interval is
    OS#2 only when the OA damper (``OA_Damper``) is open beyond its minimum position plus
    ``oa_damper_tol`` (default 5 points); at or below it the unit is recirculating at minimum OA
    (a deadband hour, an unoccupied fan run) and the interval is OS#5, where only FC#1-#4 apply.
    ``oa_damper_min`` is the minimum position in %; ``None`` learns it as the median damper
    command over fan-on intervals of mechanical cooling below ``econ_damper_open`` (the OS#4
    position), or takes 0 % (closed) when there are fewer than :data:`OA_MIN_LEARN_N` of them.
    **Heating at minimum OA** (0.99, #95): with the heating coil alone active an interval is OS#1
    only when the OA damper is at its minimum (at or below ``oa_damper_min + oa_damper_tol``, the
    same learned or given minimum); with the damper open beyond it the interval is OS#5, and a
    missing damper reading leaves it unclassified. Without an ``OA_Damper`` column OS#1 and OS#2
    are read from the valves alone, with a caveat.

    **Seasonal minimum position** (0.101, #105): ``oa_damper_min_by_month`` (``{month (1-12):
    position %}``) overrides ``oa_damper_min`` in the months it names, so a sequence with a summer
    and a winter minimum judges each interval against its own month's position (by the index's
    month). It needs ``oa_damper_min``, the position in the other months; the result's
    ``oa_damper_min`` stays that position and ``oa_damper_min_by_month`` echoes the override.

    **occupied** (0.98, #94): an optional boolean mask (array or Series aligned to ``df``) of the
    intervals the caller wants evaluated, e.g. a trended occupancy point. Fan-on intervals outside
    it are never evaluated and are counted in ``n_unoccupied``. G36 itself suspends AFDD only when
    the AHU is not operating, so the default (``None``) evaluates unoccupied fan operation too.

    **Missing coil.** A frame without ``HC`` is an AHU without a heating coil: HC is taken as 0 %
    (explicitly, with a caveat) and the heating-coil tests (:data:`FC_OMIT_NO_HEATING`) are
    omitted; likewise a frame without ``CC`` (:data:`FC_OMIT_NO_COOLING`). With neither valve
    the run is declined.

    ``comparability``: when True, ALSO compute ``fault_pct_singlesignal`` -- the same
    fault equations scored over a single-signal (input-validity) denominator instead
    of the operating-state one, for cross-tool reconciliation (e.g. open-fdd). The
    default operating-state-gated ``fault_pct`` output is unchanged either way; this
    only populates an additional field.

    Returns None for an empty frame.
    """
    if df.empty:
        return None
    # The trailing-60-min dOS window needs a sorted, unique time index (rolling on an unsorted one
    # raises); a DST fall-back / re-export duplicate keeps its last row. Timestamp strings are
    # parsed; any other index is rejected with a clear message (not pandas' "window must be an
    # integer").
    if not isinstance(df.index, pd.DatetimeIndex):
        if df.index.dtype != object:
            raise TypeError(
                f"run_g36_afdd needs a time (DatetimeIndex) index, got {type(df.index).__name__}"
            )
        df = df.set_axis(pd.to_datetime(df.index))
    if not (df.index.is_monotonic_increasing and df.index.is_unique):
        df = df[~df.index.duplicated(keep="last")].sort_index()
    if fan_gate not in ("auto", "none"):
        raise ValueError(f"fan_gate must be 'auto' or 'none', got {fan_gate!r}")
    oa_by_month = _oa_min_by_month(oa_damper_min, oa_damper_min_by_month)
    k = thr or G36Thresholds()
    n = len(df)
    delays = {
        "mode_delay_min": float(mode_delay_min),
        "alarm_delay_min": float(alarm_delay_min),
        "avg_window_min": float(avg_window_min),
    }
    caveats: list = []
    omitted: dict = {}

    # ---- coils: a missing valve column is an absent coil (valve = 0), said out loud
    has_hc, has_cc = "HC" in df.columns, "CC" in df.columns
    if not has_hc and not has_cc:
        return _declined_result(
            df, equip, "no heating- or cooling-valve command (HC/CC)", caveats, delays
        )
    if not has_hc:
        caveats.append(
            "no heating-valve command: treated as an AHU without a heating coil (HC = 0 %); "
            "FC7 and FC15 not evaluated"
        )
        omitted.update({fc: "no heating coil" for fc in FC_OMIT_NO_HEATING})
    if not has_cc:
        caveats.append(
            "no cooling-valve command: treated as an AHU without a cooling coil (CC = 0 %); "
            "FC13 and FC14 not evaluated"
        )
        omitted.update({fc: "no cooling coil" for fc in FC_OMIT_NO_COOLING})

    # ---- unit-running gate (G36: evaluation is suspended when the AHU is not operating)
    if fan_gate == "none":
        on = np.ones(n, dtype=bool)
        gate_src = "ungated (caller asserts the fan ran)"
        caveats.append(
            "fan gate disabled by the caller: every interval is assumed to have the fan running"
        )
    else:
        on, gate_src = _fan_gate(df)
        if on is None:
            return _declined_result(
                df,
                equip,
                "no supply-fan status, speed or airflow to tell when the AHU was operating",
                caveats,
                delays,
            )

    step = _median_step(df.index)
    mode = df["MODE"].to_numpy() if "MODE" in df.columns else None
    suspended = _mode_suspension(on, df.index, step, mode, float(mode_delay_min))
    evaluable = on & ~suspended
    n_unocc = 0
    if occupied is not None:
        occ_arr = (
            occupied.reindex(df.index, fill_value=False).to_numpy(dtype=bool)
            if isinstance(occupied, pd.Series)
            else np.asarray(occupied, dtype=bool)
        )
        if len(occ_arr) != n:
            raise ValueError(f"occupied has {len(occ_arr)} values for a {n}-row frame")
        n_unocc = int((evaluable & ~occ_arr).sum())
        evaluable = evaluable & occ_arr

    # operating state per interval (vectorized over the whole frame)
    hc = df["HC"].to_numpy(dtype=float) if has_hc else np.zeros(n)
    cc = df["CC"].to_numpy(dtype=float) if has_cc else np.zeros(n)
    has_oa = "OA_Damper" in df.columns
    oa = (
        pd.to_numeric(df["OA_Damper"], errors="coerce").to_numpy(dtype=float)
        if has_oa
        else np.full(n, np.nan)
    )
    # OS#2 needs the economizer open beyond its minimum position (0.98, #94)
    oa_min: float | None = None
    oa_min_src = ""
    if has_oa:
        if oa_damper_min is not None:
            oa_min, oa_min_src = float(oa_damper_min), "caller"
        else:
            learned, n_learn = _learn_oa_min(hc, cc, oa, on, valve_thr, econ_damper_open)
            if learned is not None:
                oa_min = learned
                oa_min_src = f"learned (median of {n_learn} fan-on mechanical-cooling intervals)"
            else:
                oa_min = 0.0
                oa_min_src = (
                    f"assumed 0 % (closed): only {n_learn} fan-on mechanical-cooling intervals "
                    "below the economizer threshold to learn it from"
                )
                caveats.append(
                    "OA damper minimum position not learnable from the data: free cooling (OS#2) "
                    "needs the damper open beyond 0 %, and heating (OS#1) the damper at 0 % "
                    "(set oa_damper_min to the unit's minimum)"
                )
    else:
        caveats.append(
            "no OA damper point: heating (OS#1) and free cooling (OS#2) are read from the coil "
            "valves alone, so a fan-on interval at minimum OA with both coils inactive counts as "
            "free cooling, and a heating interval counts as heating at minimum OA"
        )
    oa_econ = None if oa_min is None else oa_min + float(oa_damper_tol)
    if oa_econ is not None and oa_by_month:
        # a seasonal minimum (0.101, #105): each interval's own month's position
        month_min = pd.Series(df.index.month).map(oa_by_month).fillna(oa_min)
        oa_econ = month_min.to_numpy(dtype=float) + float(oa_damper_tol)
    os_codes = _classify_os_vec(hc, cc, oa, valve_thr, econ_damper_open, oa_econ)
    if oa_econ is not None:
        with np.errstate(invalid="ignore"):
            idle_at_min = on & ~(hc > valve_thr) & ~(cc > valve_thr) & (oa <= oa_econ)
            heat_above_min = on & (hc > valve_thr) & ~(cc > valve_thr) & (oa > oa_econ)
        n_idle_min = int(idle_at_min.sum())
        n_heat_open = int(heat_above_min.sum())  # 0.99 (#95)
    else:
        n_idle_min = n_heat_open = 0
    # dOS: operating-state changes between consecutive fan-on intervals in the trailing 60 min
    os_ser = pd.Series(os_codes, index=df.index)
    prev_on = np.zeros(n, dtype=bool)
    prev_on[1:] = on[:-1]
    changes = ((os_ser != os_ser.shift()).to_numpy() & on & prev_on).astype(float)
    dos = pd.Series(changes, index=df.index).rolling("60min").sum().to_numpy(dtype=float)

    # Pull every measure column once (None when absent); the measured points G36 averages are
    # rolling time-window means over fan-on rows (a fan-off tail does not bleed into a start).
    window = pd.Timedelta(minutes=avg_window_min) if avg_window_min and avg_window_min > 0 else None
    cols: dict = {}
    for col in (
        "HC",
        "CC",
        "SAT",
        "MAT",
        "RAT",
        "OAT",
        "SATSP",
        "FS",
        "DSP",
        "DSPSP",
        "pct_oa",
        "pct_oa_min",
        "CCET",
        "CCLT",
        "HCET",
        "HCLT",
    ):
        if col not in df.columns:
            cols[col] = None
            continue
        s = pd.to_numeric(df[col], errors="coerce").astype(float)
        if window is not None and col in _AVERAGED:
            s = s.where(on).rolling(window, min_periods=1).mean()
        cols[col] = s.to_numpy(dtype=float)
    if not has_hc:
        cols["HC"] = hc
    if not has_cc:
        cols["CC"] = cc

    missing = {
        fc: [c for c in _FC_INPUTS[fc] if cols.get(c) is None]
        for fc in _FCS
        if any(cols.get(c) is None for c in _FC_INPUTS[fc])
    }

    fault_pct: dict = {}
    fault_n: dict = {}
    fault_h: dict = {}
    fault_pct_ss: dict | None = {} if comparability else None
    hours_per_row = step / pd.Timedelta(hours=1)
    masks: dict | None = {} if keep_masks else None
    for fc in _FCS:
        if fc in omitted:
            fault_pct[fc], fault_n[fc], fault_h[fc] = None, 0, None
            if comparability:
                assert fault_pct_ss is not None
                fault_pct_ss[fc] = None
            if masks is not None:
                masks[f"FC{fc}"] = np.zeros(n, dtype=bool)
                masks[f"FC{fc}_app"] = np.zeros(n, dtype=bool)
            continue
        fired = _VFCS[fc](cols, dos, k, n)  # the fault equation, once
        # default: operating-state-gated denominator (the G36-faithful convention), fan-on and
        # outside ModeDelay only
        applicable = np.isin(os_codes, _FC_STATES[fc]) & evaluable
        n_app = int(applicable.sum())
        reported = _persistent(fired & applicable, df.index, step, float(alarm_delay_min))
        fault_n[fc] = n_app
        fault_pct[fc] = None if n_app == 0 else round(100.0 * int(reported.sum()) / n_app, 2)
        fault_h[fc] = round(float(reported.sum()) * hours_per_row, 2)
        if masks is not None:
            masks[f"FC{fc}"] = reported
            masks[f"FC{fc}_app"] = applicable
        # opt-in: single-signal (input-validity) denominator, same equation and filters
        if comparability:
            assert fault_pct_ss is not None  # non-None exactly when comparability
            valid = _input_valid_mask(cols, fc, n) & evaluable
            n_valid = int(valid.sum())
            ss = _persistent(fired & valid, df.index, step, float(alarm_delay_min))
            fault_pct_ss[fc] = None if n_valid == 0 else round(100.0 * int(ss.sum()) / n_valid, 2)

    os_on = os_codes[on]
    if masks is not None:
        masks.update({"fan_on": on, "suspended": suspended, "os": os_codes, "evaluable": evaluable})
    return G36Result(
        equip=equip,
        n_intervals=n,
        os_distribution={int(o): int((os_on == o).sum()) for o in range(1, 6)},
        fault_pct=fault_pct,
        fault_n_applicable=fault_n,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        fault_pct_singlesignal=fault_pct_ss,
        n_unclassified=int((os_on == OS_UNCLASSIFIED).sum()),
        fan_gate=gate_src,
        n_fan_off=int((~on).sum()),
        n_suspended=int(suspended.sum()),
        fault_hours=fault_h,
        omitted=omitted,
        missing_inputs=missing,
        caveats=caveats,
        delays=delays,
        oa_damper_min=None if oa_min is None else round(oa_min, 2),
        oa_damper_min_source=oa_min_src,
        oa_damper_min_by_month=oa_by_month if has_oa else None,
        n_idle_at_min_oa=n_idle_min,
        n_unoccupied=n_unocc,
        n_heating_above_min_oa=n_heat_open,
        masks=None if masks is None else pd.DataFrame(masks, index=df.index),
    )
