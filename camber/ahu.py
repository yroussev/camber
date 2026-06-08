"""AHU-level diagnostics: simultaneous heating/cooling, economizer, SA behavior.

An AHU that carries both a chilled-water coil (``CHW_Valve`` %) and a hot-water
coil (``HHW_Valve`` %), plus mixed/return/supply air temps, OA damper, and an
economizer command, supports these checks:

1. **Simultaneous H/C at the AHU** -- CHW and HHW valves both open at once. This
   is the central-plant analogue of the terminal-box reheat penalty and the most
   direct read on coil-against-coil "fighting".
2. **Economizer faults** -- when outdoor air is cooler than return air and within
   the economizer high-limit, the OA damper should modulate open for free
   cooling; flag intervals where it stays shut while the CHW valve is cooling.
3. **Supply-air / mixed-air sanity** -- basic coverage + ranges so downstream
   reset diagnostics have a vetted input.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask

AHU_MEASURES = [
    "CHW_Valve", "HHW_Valve", "SupplyAir", "MixedAir", "ReturnAir",
    "OSA", "OA_Damper", "EconoCmd", "DuctStatic", "DuctStaticSP",
    "Occupancy", "WarmUp", "CoolDown",
]


@dataclass
class AHUResult:
    """AHU simultaneous heating/cooling and economizer diagnostics for one unit."""

    equip: str
    n_intervals: int
    n_considered: int
    chw_open_pct: float
    hhw_open_pct: float
    simultaneous_hc_pct: float          # both valves open
    mean_overlap_when_simul: float      # mean min(CHW,HHW) during overlap
    econ_opportunity_pct: float         # intervals economizer SHOULD help
    econ_missed_pct: float              # of opportunity, damper stayed shut while cooling
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _pct(mask, n):
    return round(100.0 * int(mask.sum()) / n, 2) if n else 0.0


def _populated(df, col):
    """Return df[col] only if present and not entirely null, else None.

    Lets occupied_mask AND in a real occupancy point when one exists, while
    falling back to the weekday window when the BAS point is empty/absent.
    """
    if col in df.columns and df[col].notna().any():
        return df[col]
    return None


def analyze_ahu(df, equip, *, valve_thr=5.0, econ_high_limit_f=70.0,
                damper_min_open=20.0, occupied_only=True):
    """Compute AHU H/C + economizer metrics. ``df`` columns are measure names.

    Threshold basis (economizer logic, PNNL Re-tuning Ch.6):
      valve_thr=5.0 %       -- valve <5% open counted as shut (noise deadband).
      econ_high_limit_f=70  -- economizer high limit; above this OAT, free cooling is
                               disabled, so it's not an "opportunity". 70F is a common
                               dry-bulb high-limit; verify against the site's sequence.
      damper_min_open=20.0 %-- below ~20% the OA damper is at its minimum-position
                               (ventilation only), i.e. not economizing. "20% damper is
                               never 20% OA" (Ch.6) -- this gates the *damper command*,
                               not the actual OA fraction.
    The economizer-opportunity test also requires OA cooler than return air by a small
    margin (oa < ra - 2.0 F); the 2F guard avoids flagging when OA and RA are
    effectively equal (no useful free cooling, within sensor tolerance).
    """
    if "CHW_Valve" not in df.columns or "HHW_Valve" not in df.columns:
        return None
    work = df.copy()
    n_all = len(work)
    if n_all == 0:
        return None

    if occupied_only:
        work = work[occupied_mask(
            work.index,
            occ=_populated(work, "Occupancy"),
            warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )]
    n = len(work)
    if n == 0:
        return None

    chw = work["CHW_Valve"]
    hhw = work["HHW_Valve"]
    chw_open = chw > valve_thr
    hhw_open = hhw > valve_thr
    simul = chw_open & hhw_open
    overlap_mag = work.loc[simul, ["CHW_Valve", "HHW_Valve"]].min(axis=1)

    # Economizer: opportunity = cooling called (CHW open) AND OA cooler than return
    # AND OA below high-limit. Missed = opportunity but OA damper effectively shut.
    if "OSA" in work.columns and "ReturnAir" in work.columns:
        oa = work["OSA"]
        ra = work["ReturnAir"]
        opp = chw_open & (oa < ra - 2.0) & (oa < econ_high_limit_f)
        if "OA_Damper" in work.columns:
            missed = opp & (work["OA_Damper"].fillna(0) < damper_min_open)
        else:
            missed = pd.Series(False, index=work.index)
        opp_pct = _pct(opp, n)
        missed_pct = round(100.0 * int(missed.sum()) / int(opp.sum()), 2) if opp.sum() else 0.0
    else:
        opp_pct = missed_pct = 0.0

    return AHUResult(
        equip=equip,
        n_intervals=n_all,
        n_considered=n,
        chw_open_pct=_pct(chw_open, n),
        hhw_open_pct=_pct(hhw_open, n),
        simultaneous_hc_pct=_pct(simul, n),
        mean_overlap_when_simul=round(float(overlap_mag.mean()), 1) if simul.any() else 0.0,
        econ_opportunity_pct=opp_pct,
        econ_missed_pct=missed_pct,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
