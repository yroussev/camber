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


@dataclass
class G36Thresholds:
    """Tunable tolerances; defaults are the G36 Table 5.16.14.7 initial values
    (converted to degF where the standard gives degC), derived from NISTIR 7365."""
    dT_sf: float = 2.0        # fan-heat temperature rise (degF)
    dT_min: float = 10.0      # min |OAT-RAT| to evaluate economizer faults (degF)
    e_sat: float = 2.0        # SAT sensor tolerance
    e_rat: float = 2.0        # RAT sensor tolerance
    e_mat: float = 5.0        # MAT sensor tolerance
    e_oat: float = 5.0        # OAT sensor tolerance (5 global / 2 if local)
    e_flow: float = 0.30      # airflow / OA-fraction tolerance (fraction)
    e_vfdspd: float = 0.05    # fan-speed tolerance (fraction)
    e_dsp: float = 0.1        # duct-static tolerance (in. w.c.)
    e_ccet: float = 5.0
    e_cclt: float = 2.0
    e_hcet: float = 5.0
    e_hclt: float = 2.0
    valve_on: float = 99.0    # valve commanded "fully open" threshold (%)
    fan_full: float = 99.0    # fan "full speed" threshold (%)


# Operating states. Classification keys off the heating- and cooling-valve
# commands (G36 Table 5.16.14.2-.4); the OA/return damper distinguishes OS#3 from
# OS#4. HC>0 AND CC>0 simultaneously is OS#5 (no normal OS applies) -- the
# simultaneous-heating/cooling signature.
OS_HEATING = 1
OS_FREECOOL = 2          # modulating economizer, no mechanical cooling
OS_MECH_ECON = 3         # mechanical + 100% economizer
OS_MECH_MINOA = 4        # mechanical cooling, minimum OA
OS_UNKNOWN = 5           # simultaneous heat/cool, dehumidification, or fault


def classify_os(hc, cc, oa_damper=None, valve_thr=5.0, econ_damper_open=80.0):
    """Operating state for one interval from valve commands (+ OA damper).

    hc, cc, oa_damper are % (0-100). Returns an int OS code 1-5.
    """
    heating = hc is not None and hc > valve_thr
    cooling = cc is not None and cc > valve_thr
    if heating and cooling:
        return OS_UNKNOWN
    if heating:
        return OS_HEATING
    if not cooling:
        return OS_FREECOOL
    # cooling on: economizer (high OA damper) -> OS#3, else minimum OA -> OS#4
    if oa_damper is not None and oa_damper >= econ_damper_open:
        return OS_MECH_ECON
    return OS_MECH_MINOA


# Which fault conditions are evaluated in each operating state (G36 §5.16.14.9).
OS_FAULTS = {
    OS_HEATING:    [1, 2, 3, 4, 5, 6, 7, 14],
    OS_FREECOOL:   [1, 2, 3, 4, 8, 9, 12, 14, 15],
    OS_MECH_ECON:  [1, 2, 3, 4, 10, 11, 12, 13, 15],
    OS_MECH_MINOA: [1, 2, 3, 4, 6, 12, 13, 15],
    OS_UNKNOWN:    [1, 2, 3, 4],
}


# Each FC is a predicate on a row dict of averaged values. Returns True if the
# fault equation is satisfied (fault present). Equations per G36 Table 5.16.14.8.
def _fc1(r, k):   # DSP too low with fan at full speed
    return (r.get("DSP") is not None and r.get("DSPSP") is not None
            and r.get("FS") is not None
            and r["DSP"] < r["DSPSP"] - k.e_dsp
            and r["FS"] >= k.fan_full * (1 - k.e_vfdspd))


def _fc2(r, k):   # MAT too low; should be between OAT and RAT
    if any(r.get(x) is None for x in ("MAT", "RAT", "OAT")):
        return False
    return r["MAT"] + k.e_mat < min(r["RAT"] - k.e_rat, r["OAT"] - k.e_oat)


def _fc3(r, k):   # MAT too high
    if any(r.get(x) is None for x in ("MAT", "RAT", "OAT")):
        return False
    return r["MAT"] - k.e_mat > max(r["RAT"] + k.e_rat, r["OAT"] + k.e_oat)


def _fc4(r, k):   # too many operating-state changes (instability)
    return r.get("dOS") is not None and r["dOS"] > k.os_max


def _fc5(r, k):   # SAT too low; should be higher than MAT (in heating)
    if any(r.get(x) is None for x in ("SAT", "MAT")):
        return False
    return r["SAT"] + k.e_sat <= r["MAT"] - k.e_mat + k.dT_sf


def _fc6(r, k):   # OA fraction off (too low/high vs minimum)
    if any(r.get(x) is None for x in ("RAT", "OAT", "pct_oa", "pct_oa_min")):
        return False
    return (abs(r["RAT"] - r["OAT"]) >= k.dT_min
            and abs(r["pct_oa"] - r["pct_oa_min"]) > k.e_flow * 100.0)


def _fc7(r, k):   # SAT too low in full heating
    if any(r.get(x) is None for x in ("SAT", "SATSP", "HC")):
        return False
    return r["SAT"] < r["SATSP"] - k.e_sat and r["HC"] >= k.valve_on


def _fc8(r, k):   # SAT and MAT should be ~equal (free cooling)
    if any(r.get(x) is None for x in ("SAT", "MAT")):
        return False
    return abs(r["SAT"] - k.dT_sf - r["MAT"]) > np.hypot(k.e_sat, k.e_mat)


def _fc9(r, k):   # OAT too high for free cooling
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
    return cet - clt >= np.hypot(k.e_ccet, k.e_cclt) + k.dT_sf


def _fc15(r, k):  # temperature rise across an inactive heating coil (leak/stuck)
    het, hlt = r.get("HCET"), r.get("HCLT")
    if het is None or hlt is None:
        return False
    return hlt - het >= np.hypot(k.e_hcet, k.e_hclt) + k.dT_sf


_FCS = {1: _fc1, 2: _fc2, 3: _fc3, 4: _fc4, 5: _fc5, 6: _fc6, 7: _fc7, 8: _fc8,
        9: _fc9, 10: _fc10, 11: _fc11, 12: _fc12, 13: _fc13, 14: _fc14, 15: _fc15}


# --------------------------------------------------------------------------- #
# Vectorized engine. These reproduce the scalar predicates above element-wise
# over whole columns; a missing column yields an all-False array, and any NaN in
# an input makes that interval's comparisons evaluate False -- exactly the scalar
# guard (``any(... is None) -> return False``). Used by run_g36_afdd; the scalar
# classify_os / _fc* remain the readable reference (and public API).
# --------------------------------------------------------------------------- #
def _classify_os_vec(hc, cc, oa, valve_thr, econ_damper_open):
    """Operating state per interval over arrays (NaN -> not heating/cooling/open)."""
    heating = hc > valve_thr
    cooling = cc > valve_thr
    oa_open = oa >= econ_damper_open
    return np.select(
        [heating & cooling, heating, ~cooling, oa_open],
        [OS_UNKNOWN, OS_HEATING, OS_FREECOOL, OS_MECH_ECON],
        default=OS_MECH_MINOA,
    ).astype(int)


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
    return dos > k.os_max          # NaN > thr -> False


def _vfc5(c, dos, k, n):
    if c["SAT"] is None or c["MAT"] is None:
        return _false(n)
    return c["SAT"] + k.e_sat <= c["MAT"] - k.e_mat + k.dT_sf


def _vfc6(c, dos, k, n):
    if any(c[x] is None for x in ("RAT", "OAT", "pct_oa", "pct_oa_min")):
        return _false(n)
    return ((np.abs(c["RAT"] - c["OAT"]) >= k.dT_min)
            & (np.abs(c["pct_oa"] - c["pct_oa_min"]) > k.e_flow * 100.0))


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
    return c["CCET"] - c["CCLT"] >= np.hypot(k.e_ccet, k.e_cclt) + k.dT_sf


def _vfc15(c, dos, k, n):
    if c["HCET"] is None or c["HCLT"] is None:
        return _false(n)
    return c["HCLT"] - c["HCET"] >= np.hypot(k.e_hcet, k.e_hclt) + k.dT_sf


_VFCS = {1: _vfc1, 2: _vfc2, 3: _vfc3, 4: _vfc4, 5: _vfc5, 6: _vfc6, 7: _vfc7,
         8: _vfc8, 9: _vfc9, 10: _vfc10, 11: _vfc11, 12: _vfc12, 13: _vfc13,
         14: _vfc14, 15: _vfc15}

# OS codes in which each FC is evaluated (inverse of OS_FAULTS).
_FC_STATES = {fc: [os for os, fcs in OS_FAULTS.items() if fc in fcs] for fc in _FCS}

# Measure columns each FC needs. Used only by the opt-in single-signal
# comparability denominator (the "input-validity" gate): the set of hours over
# which the fault *could* be computed at all, regardless of operating state. FC4
# keys off dOS, which is always derivable, so it has no column requirement.
_FC_INPUTS = {
    1: ("DSP", "DSPSP", "FS"), 2: ("MAT", "RAT", "OAT"), 3: ("MAT", "RAT", "OAT"),
    4: (), 5: ("SAT", "MAT"), 6: ("RAT", "OAT", "pct_oa", "pct_oa_min"),
    7: ("SAT", "SATSP", "HC"), 8: ("SAT", "MAT"), 9: ("OAT", "SATSP"),
    10: ("MAT", "OAT"), 11: ("OAT", "SATSP"), 12: ("SAT", "MAT"),
    13: ("SAT", "SATSP", "CC"), 14: ("CCET", "CCLT"), 15: ("HCET", "HCLT"),
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
    1: "duct static too low at full fan", 2: "mixed-air temp too low",
    3: "mixed-air temp too high", 4: "unstable control (too many OS changes)",
    5: "supply-air too low in heating", 6: "outdoor-air fraction off",
    7: "supply-air too low in full heating", 8: "SAT/MAT mismatch (free cooling)",
    9: "OAT too high for free cooling", 10: "OAT/MAT mismatch (economizer)",
    11: "OAT too low for mechanical cooling", 12: "supply-air too high (cooling)",
    13: "supply-air too high in full cooling",
    14: "temp drop across inactive cooling coil (leak/stuck)",
    15: "temp rise across inactive heating coil (leak/stuck)",
}


# add os_max to thresholds (kept here so the dataclass stays focused on tolerances)
G36Thresholds.os_max = 7


@dataclass
class G36Result:
    """ASHRAE Guideline 36 fault-condition results (per-FC trip rates) for one AHU."""

    equip: str
    n_intervals: int
    os_distribution: dict          # OS code -> count
    fault_pct: dict                # FC number -> % of *applicable* intervals tripped
    fault_n_applicable: dict       # FC number -> intervals where its OS applied
    coverage_start: str
    coverage_end: str
    # Opt-in comparability output (None unless run_g36_afdd(comparability=True)):
    # FC number -> % over the single-signal (input-validity) denominator, for
    # cross-tool reconciliation (e.g. open-fdd). Same fault equation/fires as
    # fault_pct, different denominator. See docs/ECOSYSTEM.md.
    fault_pct_singlesignal: dict | None = None

    def as_dict(self):
        """Return the result as a plain dict (faults flattened to FC<n>_pct keys).

        When the comparability output is present, also emits FC<n>_pct_singlesignal
        keys; otherwise the dict is identical to the default-mode output.
        """
        d = {"equip": self.equip, "n_intervals": self.n_intervals,
             "os_distribution": self.os_distribution,
             "coverage_start": self.coverage_start, "coverage_end": self.coverage_end}
        d.update({f"FC{n}_pct": self.fault_pct.get(n) for n in _FCS})
        if self.fault_pct_singlesignal is not None:
            d.update({f"FC{n}_pct_singlesignal": self.fault_pct_singlesignal.get(n)
                      for n in _FCS})
        return d


def run_g36_afdd(df: pd.DataFrame, equip: str, *, thr: G36Thresholds | None = None,
                 econ_damper_open: float = 80.0, valve_thr: float = 5.0,
                 comparability: bool = False) -> G36Result | None:
    """Run the G36 AFDD fault set over an AHU frame.

    ``df`` columns (any subset; faults needing missing inputs are skipped):
    HC, CC, SAT, MAT, RAT, OAT, SATSP, FS, DSP, DSPSP, OA_Damper, pct_oa,
    pct_oa_min, CCET, CCLT, HCET, HCLT. Index is time. Each FC is evaluated only in
    intervals whose operating state lists it (G36 §5.16.14.9) -- the operating-state
    denominator convention (see the module docstring).

    ``comparability``: when True, ALSO compute ``fault_pct_singlesignal`` -- the same
    fault equations scored over a single-signal (input-validity) denominator instead
    of the operating-state one, for cross-tool reconciliation (e.g. open-fdd). The
    default operating-state-gated ``fault_pct`` output is unchanged either way; this
    only populates an additional field.
    """
    if df.empty or "HC" not in df.columns or "CC" not in df.columns:
        return None
    k = thr or G36Thresholds()
    n = len(df)

    # operating state per interval (vectorized over the whole frame)
    hc = df["HC"].to_numpy(dtype=float)
    cc = df["CC"].to_numpy(dtype=float)
    oa = (df["OA_Damper"].to_numpy(dtype=float) if "OA_Damper" in df.columns
          else np.full(n, np.nan))
    os_codes = _classify_os_vec(hc, cc, oa, valve_thr, econ_damper_open)
    # dOS: operating-state changes in the trailing 60 min (time-based 1h window)
    os_ser = pd.Series(os_codes, index=df.index)
    changes = (os_ser != os_ser.shift()).astype(float)
    dos = changes.rolling("60min").sum().to_numpy(dtype=float)

    # Pull every measure column once (None when absent) and evaluate each FC over
    # the whole column; a missing column / NaN value yields False for that interval.
    cols = {col: (df[col].to_numpy(dtype=float) if col in df.columns else None)
            for col in ("HC", "CC", "SAT", "MAT", "RAT", "OAT", "SATSP", "FS",
                        "DSP", "DSPSP", "pct_oa", "pct_oa_min",
                        "CCET", "CCLT", "HCET", "HCLT")}

    fault_pct, fault_n = {}, {}
    fault_pct_ss = {} if comparability else None
    for fc in _FCS:
        fired = _VFCS[fc](cols, dos, k, n)          # the fault equation, once
        # default: operating-state-gated denominator (the G36-faithful convention)
        applicable = np.isin(os_codes, _FC_STATES[fc])
        n_app = int(applicable.sum())
        fault_n[fc] = n_app
        fault_pct[fc] = (None if n_app == 0
                         else round(100.0 * int((fired & applicable).sum()) / n_app, 2))
        # opt-in: single-signal (input-validity) denominator, same fires
        if comparability:
            valid = _input_valid_mask(cols, fc, n)
            n_valid = int(valid.sum())
            fault_pct_ss[fc] = (None if n_valid == 0
                                else round(100.0 * int((fired & valid).sum()) / n_valid, 2))

    os_dist = {int(o): int((os_codes == o).sum()) for o in range(1, 6)}
    return G36Result(equip=equip, n_intervals=n, os_distribution=os_dist,
                     fault_pct=fault_pct, fault_n_applicable=fault_n,
                     coverage_start=str(df.index.min()), coverage_end=str(df.index.max()),
                     fault_pct_singlesignal=fault_pct_ss)
