"""Shared fit / score plumbing for the load-normalized chiller drift rules (private).

Every tons-normalized chiller rule does the same three things: pick load gates for the machine in
front of it, freeze a baseline (optionally with a second regressor), and score the current period
against it -- declining with the *real* reason when any step cannot be done. Keeping those steps
here means a gate or message fix lands in every rule at once instead of in seven near-copies.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import (
    fit_load_baseline,
    load_drift_stats,
    size_relative_load_gates,
    unscoreable_reason,
)
from ..model.roles import Role

# A covariate must move at least this much across the baseline before its coefficient is trusted:
# four times the +/-0.5 degF accuracy of a typical plant temperature sensor, so the spread is not
# just instrument noise. Constructor-overridable on the rules that use a covariate.
COVARIATE_MIN_SPAN_F = 2.0

_MIN_SCORE_SAMPLES = 10  # load_drift_stats' default floor


def gates(base_t: pd.DataFrame, min_tons, min_tons_span, load_col="tons") -> tuple[float, float]:
    """The (min load, min span) gates for this machine: explicit values win, else size-relative."""
    load = base_t[load_col] if load_col in base_t.columns else pd.Series(dtype=float)
    return size_relative_load_gates(load, min_load=min_tons, min_load_span=min_tons_span)


def _role_key(name: str):
    """A frame key for a stored covariate name (``Role`` when it names one)."""
    try:
        return Role(name)
    except ValueError:
        return name


def freeze_baseline(
    rule,
    equip,
    base_t: pd.DataFrame,
    caveats: list,
    *,
    kind: str,
    metric_col,
    metric_range,
    gate: tuple[float, float],
    metric_name: str,
    covariates=(),
    covariate_sign: int = 1,
    min_covariate_span: float = COVARIATE_MIN_SPAN_F,
    level_fallback: bool = True,
):
    """The frozen baseline for ``kind``, freezing one from ``base_t`` if none exists yet.

    ``covariates`` are tried in order (only those present in ``base_t``); the first that is
    identified with the physically expected sign (``covariate_sign``) is used, otherwise the fit
    falls back to load only and a caveat says why. A flat ``level`` fit (no load spread) is
    reported as such.
    """
    frozen = rule.store.model_for(rule.site, equip, kind)
    if frozen is not None:
        return frozen
    if not rule.freeze_if_missing:
        caveats.append(f"could not evaluate {kind}: no frozen baseline and freezing is disabled")
        return None
    min_load, min_span = gate
    common = dict(
        metric_col=metric_col,
        load_col="tons",
        min_load=min_load,
        metric_range=metric_range,
        min_load_span=min_span,
        level_fallback=level_fallback,
    )
    fit = None
    for cov in covariates:
        if cov not in base_t.columns:
            continue
        cand = fit_load_baseline(
            base_t, covariate_col=cov, min_covariate_span=min_covariate_span, **common
        )
        cname = getattr(cov, "value", cov)
        if cand is None:
            caveats.append(
                f"{cname} is mapped but its effect on {metric_name} could not be identified in "
                f"the baseline ({_cov_reason(base_t, cov, min_covariate_span)}); normalized on "
                "load only"
            )
            continue
        if cand.covariate_slope * covariate_sign <= 0:
            caveats.append(
                f"{cname} is mapped but the baseline fit gave it the physically wrong sign "
                f"({cand.covariate_slope:+.3g} per unit); not used -- normalized on load only"
            )
            continue
        fit = cand
        break
    if fit is None:
        fit = fit_load_baseline(base_t, **common)
    if fit is None:
        why = unscoreable_reason(
            base_t,
            metric_col=metric_col,
            load_col="tons",
            min_load=min_load,
            metric_range=metric_range,
            min_samples=30,
            min_load_span=None if level_fallback else min_span,
            metric_name=metric_name,
            load_name="tons",
        )
        caveats.append(
            f"could not evaluate {kind}: the baseline period would not support a fit -- {why}"
        )
        return None
    idx = base_t.index
    rule.store.freeze(
        fit,
        site=rule.site,
        equip=equip,
        kind=kind,
        frozen_at=rule.run_id,
        period=(str(idx.min()), str(idx.max())),
        reason="initial baseline frozen from the supplied baseline period",
    )
    return fit


def _cov_reason(frame: pd.DataFrame, cov, min_span: float) -> str:
    vals = pd.to_numeric(frame[cov], errors="coerce").dropna()
    if vals.empty:
        return "no numeric values"
    span = float(vals.quantile(0.95) - vals.quantile(0.05))
    if span < min_span:
        return f"its 5-95% spread was only {span:.2g} (need {min_span:g})"
    return "the fit was degenerate"


def fit_notes(frozen, caveats: list, metrics: dict, prefix: str, *, load_unit: str = "tons"):
    """Report how the baseline was modelled: flat-level fallback and / or a covariate."""
    metrics[f"{prefix}_baseline_model"] = getattr(frozen, "load_model", "linear")
    if getattr(frozen, "load_model", "linear") == "level":
        lo = frozen.tons_min - frozen.load_band
        hi = frozen.tons_max + frozen.load_band
        caveats.append(
            f"the baseline's load barely moved ({frozen.tons_min:.3g}-{frozen.tons_max:.3g} "
            f"{load_unit}), so it is a flat level with no load slope, valid only for "
            f"{lo:.3g}-{hi:.3g} {load_unit}; samples outside that band are not scored"
        )
    if getattr(frozen, "covariate", ""):
        metrics[f"{prefix}_covariate"] = frozen.covariate
        metrics[f"{prefix}_covariate_slope"] = frozen.covariate_slope


_COVARIATE_WORDS = {
    Role.CW_SUPPLY_TEMP.value: "entering condenser-water temperature",
    Role.OAT.value: "outdoor-air temperature",
    Role.CHW_SUPPLY_TEMP.value: "leaving chilled-water temperature",
}


def matched(frozen) -> str:
    """ "matched load" -- plus the covariate when the baseline was fitted on one."""
    name = getattr(frozen, "covariate", "")
    if not name:
        return "matched load"
    return f"matched load and {_COVARIATE_WORDS.get(name, name)}"


def covariate_key(frozen):
    """The frame key of the baseline's covariate, or ``None`` for a load-only baseline."""
    name = getattr(frozen, "covariate", "")
    return _role_key(name) if name else None


def score(
    frozen,
    cur_t: pd.DataFrame,
    caveats: list,
    *,
    kind: str,
    metric_col,
    metric_range,
    gate: tuple[float, float],
    metric_name: str,
):
    """Score ``cur_t`` against ``frozen``; on failure append the real reason and return ``None``."""
    cov = covariate_key(frozen)
    if cov is not None and cov not in cur_t.columns:
        caveats.append(
            f"could not evaluate {kind}: the frozen baseline is normalized on "
            f"{frozen.covariate}, which is not present in the current period"
        )
        return None
    drift = load_drift_stats(
        frozen,
        cur_t,
        metric_col=metric_col,
        load_col="tons",
        min_load=gate[0],
        metric_range=metric_range,
        covariate_col=cov,
    )
    if drift is None:
        why = unscoreable_reason(
            cur_t,
            metric_col=metric_col,
            load_col="tons",
            min_load=gate[0],
            metric_range=metric_range,
            min_samples=_MIN_SCORE_SAMPLES,
            covariate_col=cov,
            baseline=frozen,
            metric_name=metric_name,
            load_name="tons",
        )
        caveats.append(
            f"could not evaluate {kind}: nothing scoreable in the current period -- {why}"
        )
    return drift


def envelope_caveats(drift, caveats: list, frozen, prefix: str = "") -> None:
    """The extrapolation caveats for a scored period (load and, when used, covariate)."""
    if drift.extrapolated:
        caveats.append(
            f"{prefix}over 10% of the current period ran outside the baseline's fitted load "
            "envelope, so part of this drift is extrapolated"
        )
    if getattr(drift, "covariate_extrapolated", False):
        caveats.append(
            f"{prefix}over 10% of the current period's {frozen.covariate} ran outside the range "
            f"it covered in the baseline ({frozen.covariate_min:.3g}-{frozen.covariate_max:.3g}), "
            "so part of this drift is extrapolated"
        )
