"""Rule: economizer not locked out above the high limit.

Above the outdoor-air high limit, bringing in more outdoor air only adds cooling load. The
economizer should hold minimum OA; still admitting excess OA in hot weather is a stuck/mis-tuned
economizer wasting cooling energy.

**What "excess" means depends on the building.** OA *damper position* is a weak proxy — it is
not linear in OA flow, and the design minimum outside air is a building property (a 100%-OA or
high-OA design correctly sits far open at minimum). So when mixed- and return-air temperatures
are available this rule judges on **outside-air fraction** (temperature balance, the method of
:mod:`camber.oafraction`) against a minimum-OA *fraction*; otherwise it falls back to a damper
threshold, and says so in a caveat. Both the high limit and the minimum are configurable — the
defaults (65 °F, 25% damper / 20% OA) encode a typical, not universal, building; e.g. CA Title 24
sets the high-limit changeover by climate zone.

Distinct from :mod:`camber.rules.oafraction_rule` (`outdoor_air_fraction`), which flags excess OA
across cooling weather generally; this rule is specifically the *lockout above the high limit*.
numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding


class EconomizerHighLimit:
    """Flags excess outside air admitted above the economizer high limit (no lockout)."""

    name = "economizer_high_limit"
    roles_required = (Role.OA_DAMPER, Role.OAT)
    # When both are present, judge on OA-fraction (temperature balance) instead of damper %.
    roles_optional = (Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP)

    def __init__(
        self,
        *,
        high_limit_f: float = 65.0,
        min_damper: float = 0.25,  # OA-damper fraction (0..1) for the fallback path
        min_oa_pct: float = 20.0,  # design minimum outside-air fraction (%) for the OAF path
        oa_margin_pct: float = 5.0,  # OAF must exceed min + this to count as "not locked out"
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
        denom_min_f: float = 5.0,  # skip OAF where |RAT-OAT| is too small to be reliable
    ):
        self.high_limit_f = high_limit_f
        self.min_damper = min_damper
        self.min_oa_pct = min_oa_pct
        self.oa_margin_pct = oa_margin_pct
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.denom_min_f = denom_min_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat = frame[Role.OAT]
        hot = (oat > self.high_limit_f) & oat.notna()
        have_temps = Role.MIXED_AIR_TEMP in frame.columns and Role.RETURN_AIR_TEMP in frame.columns
        caveats: list = []

        if have_temps:
            # Temperature-balance OA-fraction (percent), same method + guards as camber.oafraction.
            mat, rat = frame[Role.MIXED_AIR_TEMP], frame[Role.RETURN_AIR_TEMP]
            denom = rat - oat
            oaf = 100.0 * (rat - mat) / denom
            plausible = (
                oat.between(20, 130)
                & mat.between(30, 120)
                & rat.between(40, 110)
                & (denom.abs() >= self.denom_min_f)
                & oaf.between(-20, 120)
            )
            valid = hot & plausible
            n = int(valid.sum())
            not_locked = valid & (oaf > self.min_oa_pct + self.oa_margin_pct)
            basis = "OA-fraction"
        else:
            # OA_DAMPER arrives 0-1 or 0-100 depending on the BAS; canonicalize to a fraction
            # so the fraction threshold is correct either way. (The role pipeline scales percent
            # roles to 0-100 -- comparing that against a 0-1 threshold makes every open damper
            # read "not locked out", the original mis-scaling behind this rule's false faults.)
            damper = normalize_percent(frame[Role.OA_DAMPER]) / 100.0
            valid = hot & damper.notna()
            n = int(valid.sum())
            not_locked = valid & (damper > self.min_damper + 0.05)
            basis = "damper position"
            caveats.append(
                "no mixed/return-air temps: judged on damper position "
                "(a weak proxy for outside-air fraction)"
            )

        if n == 0:
            msg = f"{equip}: no hours above the {self.high_limit_f:g}°F high limit"
            if have_temps:
                msg += " with a usable OA-fraction"
            return Finding(
                rule=self.name, equip=equip, severity="info", summary=msg, caveats=caveats
            )

        pct = 100.0 * float(not_locked.sum()) / n
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        thresh = (
            f"OAF > {self.min_oa_pct:g}%"
            if basis == "OA-fraction"
            else f"damper > {self.min_damper:.0%}"
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "not_locked_out_pct": round(pct, 2),
                "high_limit_f": self.high_limit_f,
                "basis": basis,
                "min_oa_pct": self.min_oa_pct,
                "min_damper": self.min_damper,
                "n_above_limit": n,
            },
            summary=(
                f"{equip}: excess OA above the {self.high_limit_f:g}°F high limit "
                f"{pct:.0f}% of those hours ({basis}: {thresh}; economizer not locked out)"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: OA damper vs OAT against the economizer expectation."""
        from ..charts.diagnostic import TEMPLATES
        from ..charts.evidence import Evidence

        return Evidence(
            renderer="diagnostic",
            template=TEMPLATES["economizer"],
            title=f"{equip}: economizer high-limit",
        )
