"""Shared fit / score plumbing for the DX / heat-pump refrigerant-side rules (private; 0.93).

The DX charge, indoor-airflow and discharge-superheat detectors all compare a metric with what the
same unit did when it was known to be healthy, **at matched conditions**. An air-cooled DX unit
has no chilled-water load to normalize on; what moves its refrigerant-side readings is the
weather at the condenser and the air entering the evaporator, so the frozen reference is

    metric ~ a + b * OAT + c * (return-air temperature - its baseline mean)

fitted on the machinery the chiller detectors already use (:func:`camber.chillerbaseline.
fit_load_baseline`, with outdoor-air temperature as the "load" column: the record's ``tons_*``
fields then hold degF of OAT). A water-cooled machine (chilled-water flow and temperatures
mapped) is normalized on its load in tons instead, exactly as the chiller detectors are.

Only rows where the refrigerant circuit runs in cooling are used: compressor status on (when
mapped), reversing valve in cooling (0, when mapped) and a supply-air temperature below the return
(when both are mapped).
"""

from __future__ import annotations

import fnmatch

import numpy as np
import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats, tons_from_flow
from ..model.roles import Role

LOAD = "tons"  # the prepared frame's normalizer column (degF of OAT for an air-cooled unit)
_NO_LOAD_FLOOR = -200.0  # OAT is a condition, not a load: no "unloaded" rows to drop
MIN_OAT_SPAN_F = 5.0  # below this the OAT slope is not identified -> a flat level model
COVARIATE_MIN_SPAN_F = 2.0  # as camber.rules._chillerfit
MIN_BASELINE_SAMPLES = 30

_CHW = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
_CHW_COLS = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}


def cooling_rows(frame: pd.DataFrame, *, min_split_f: float = 0.0) -> pd.Series:
    """Rows where the refrigerant circuit runs in cooling, from whatever status is mapped."""
    ok = pd.Series(True, index=frame.index)
    if Role.COMPRESSOR_STATUS in frame.columns:
        ok &= pd.to_numeric(frame[Role.COMPRESSOR_STATUS], errors="coerce") > 0.5
    if Role.REVERSING_VALVE_CMD in frame.columns:
        ok &= pd.to_numeric(frame[Role.REVERSING_VALVE_CMD], errors="coerce") < 0.5
    if Role.SUPPLY_FAN_STATUS in frame.columns:
        ok &= pd.to_numeric(frame[Role.SUPPLY_FAN_STATUS], errors="coerce") > 0.5
    if Role.RETURN_AIR_TEMP in frame.columns and Role.SUPPLY_AIR_TEMP in frame.columns:
        split = pd.to_numeric(frame[Role.RETURN_AIR_TEMP], errors="coerce") - pd.to_numeric(
            frame[Role.SUPPLY_AIR_TEMP], errors="coerce"
        )
        ok &= split > min_split_f
    return ok.fillna(False)


def is_water_cooled(frame: pd.DataFrame) -> bool:
    """True when the chilled-water load roles are present (normalize on tons, not OAT)."""
    return all(r in frame.columns for r in _CHW)


def prepared(
    frame: pd.DataFrame, metric, *, water: bool, load_role=Role.OAT, cov_role=None
) -> pd.DataFrame:
    """``{LOAD, metric, covariate}`` over the cooling rows; empty when the normalizer is absent.

    The normalizer is the chilled-water load in tons for a water-cooled machine, else
    ``load_role`` (outdoor air by default); the covariate column is ``cov_role`` (default: the
    entering condenser water, or the return air), kept under its own key.
    """
    rows = frame[cooling_rows(frame)]
    if water:
        legacy = rows.rename(columns={r: c for r, c in _CHW_COLS.items() if r in rows.columns})
        load = tons_from_flow(legacy)
        cov_role = cov_role or Role.CW_SUPPLY_TEMP
    else:
        if load_role not in rows.columns:
            return pd.DataFrame()
        load = pd.to_numeric(rows[load_role], errors="coerce")
        cov_role = cov_role or Role.RETURN_AIR_TEMP
    out = pd.DataFrame({LOAD: load}, index=rows.index)
    if metric in rows.columns:
        out[metric] = pd.to_numeric(rows[metric], errors="coerce")
    if cov_role in rows.columns:
        out[cov_role] = pd.to_numeric(rows[cov_role], errors="coerce")
    return out


def gates(water: bool, base: pd.DataFrame) -> tuple[float, float]:
    """``(min_load, min_load_span)``: none for OAT; size-relative tons gates for a chiller."""
    if not water:
        return _NO_LOAD_FLOOR, MIN_OAT_SPAN_F
    from ..chillerbaseline import size_relative_load_gates

    return size_relative_load_gates(base[LOAD] if LOAD in base else pd.Series(dtype=float))


def freeze(
    rule,
    equip,
    base: pd.DataFrame,
    caveats: list,
    *,
    kind: str,
    metric,
    metric_range,
    metric_name: str,
    gate: tuple[float, float],
    covariate=None,
):
    """The frozen reference for ``kind``, freezing one from ``base`` if none exists (and allowed).

    ``covariate`` (a column of ``base``) is tried first; a fit on the normalizer alone is the
    fallback when it is absent or not identified.
    """
    frozen = rule.store.model_for(rule.site, equip, kind)
    if frozen is not None:
        return frozen
    if not rule.freeze_if_missing:
        caveats.append(f"could not evaluate {kind}: no frozen baseline and freezing is disabled")
        return None
    min_load, min_span = gate
    water = min_load > _NO_LOAD_FLOOR
    common = dict(
        metric_col=metric,
        load_col=LOAD,
        min_load=min_load,
        metric_range=metric_range,
        min_samples=getattr(rule, "min_baseline_samples", MIN_BASELINE_SAMPLES),
        min_load_span=min_span,
        level_fallback=True,
    )
    cov = covariate or (Role.CW_SUPPLY_TEMP if water else Role.RETURN_AIR_TEMP)
    fit = None
    if cov in base.columns:
        fit = fit_load_baseline(
            base, covariate_col=cov, min_covariate_span=COVARIATE_MIN_SPAN_F, **common
        )
    if fit is None:
        fit = fit_load_baseline(base, **common)
    if fit is None:
        n = int(
            (base[metric].between(*metric_range) & base[LOAD].notna()).sum()
            if metric in base.columns
            else 0
        )
        caveats.append(
            f"could not evaluate {kind}: the baseline period would not support a {metric_name} "
            f"fit ({n} usable cooling rows, need {common['min_samples']})"
        )
        return None
    idx = base.index
    rule.store.freeze(
        fit,
        site=rule.site,
        equip=equip,
        kind=kind,
        frozen_at=rule.run_id,
        period=(str(idx.min()), str(idx.max())),
        reason="initial baseline frozen from the supplied fault-free period",
    )
    return fit


def score(
    frozen, cur: pd.DataFrame, caveats: list, *, kind, metric, metric_range, min_samples, gate
):
    """The current period's drift from ``frozen`` at matched conditions, or ``None`` (with why)."""
    cov = frozen.covariate or None
    cov_key = None
    if cov:
        try:
            cov_key = Role(cov)
        except ValueError:
            cov_key = cov
        if cov_key not in cur.columns:
            caveats.append(
                f"could not evaluate {kind}: the reference is normalized on {cov}, which is not "
                "present in the current period"
            )
            return None
    drift = load_drift_stats(
        frozen,
        cur,
        metric_col=metric,
        load_col=LOAD,
        min_load=gate[0],
        metric_range=metric_range,
        min_samples=min_samples,
        covariate_col=cov_key,
    )
    if drift is None:
        caveats.append(
            f"could not evaluate {kind}: fewer than {min_samples} scoreable cooling rows in the "
            "current period"
        )
    return drift


def severity(drift_f: float, sigma: float, *, warn_f, fault_f, warn_sigma, fault_sigma) -> str:
    """Two-sided: ``|drift|`` must clear both the degF floor and the sigma floor."""
    mag = abs(drift_f)
    if not (sigma > 0):
        return "fault" if mag >= fault_f else ("warn" if mag >= warn_f else "ok")
    if mag >= max(fault_f, fault_sigma * sigma):
        return "fault"
    if mag >= max(warn_f, warn_sigma * sigma):
        return "warn"
    return "ok"


def target_for(targets, equip: str) -> dict | None:
    """The target dict for ``equip``: ``targets`` itself, or the first glob key that matches.

    ``targets`` is either one target dict (every equipment) or ``{pattern: target dict}`` with
    :mod:`fnmatch` patterns on the equipment id (``"HP_SEER14__*"``).
    """
    if not targets:
        return None
    if all(isinstance(v, dict) for v in targets.values()):
        for pat, tgt in targets.items():
            if fnmatch.fnmatchcase(str(equip), str(pat)):
                return tgt
        return None
    return targets


def median(series: pd.Series) -> float:
    """Median of the finite values (NaN when there are none)."""
    v = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if v.size else float("nan")
