"""Config-driven M&V baseline versioning: what ``camber mv`` runs (provisional; #21 phase 21d).

The pure pieces live in :mod:`camber.mandv.rebaseline` (the store, the policy, the triggers, the
window rule); this module is the layer that reads a config's meters and drives them, the way
:mod:`camber.driftrun` drives the drift detectors:

* :func:`meter_series` -- each ``mv`` entry's meters as daily ``oat`` / ``energy`` frames;
* :func:`open_mv_store` -- the facility's :class:`~camber.mandv.rebaseline.MVBaselineStore`
  (``state/<facility_id>/mv_baselines.json`` in a portfolio workspace, else the config's
  top-level ``"mv_store"``);
* :func:`plan_freeze`, :func:`plan_rebaseline`, :func:`plan_adjust` -- the three **write** verbs.
  Each builds its change on the in-memory store and says what it would write; only the CLI saves,
  under the workspace lock, with ``--reason`` and an audit line, and only with ``--apply``;
* :func:`propose` and :func:`chained_report` -- read-only: triggers, the rebaseline proposal, the
  SEP method proposal, and the chain of savings across baseline versions.

**Nothing here moves a baseline by itself.** The run path (``camber run``, ``camber mv run``)
reads the store and never writes it; an unresolved trigger declines the saving after its date.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .mandv.rebaseline import (
    MV_BASELINES_FILE,
    MVBaselineStore,
    RebaselinePolicy,
    events_from_entry,
    fit_frame_sha256,
    mv_kind,
    mv_provenance,
    version_label,
    version_segments,
)

__all__ = [
    "MeterSeries",
    "MVPlan",
    "MeterChain",
    "mv_store_path",
    "open_mv_store",
    "meter_series",
    "baseline_window_check",
    "fit_version",
    "plan_freeze",
    "plan_rebaseline",
    "plan_adjust",
    "propose",
    "chained_report",
    "versioned_rows",
]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(x) -> pd.Timestamp:
    return pd.Timestamp(x).normalize()


def _ds(x) -> str:
    return str(_day(x).date())


@dataclass
class MeterSeries:
    """One meter of one ``mv`` entry, as daily energy against daily-mean outdoor temperature."""

    entry_index: int
    entry: dict
    equip: str
    role: str
    kind: str
    daily: pd.DataFrame  # columns oat, energy; one row per day with data


@dataclass
class MVPlan:
    """What a write verb would do (and, once the caller saves ``store``, did).

    ``changes`` lists one dict per baseline touched; ``refused`` one per meter left alone and why.
    The store is mutated in memory only -- saving it is the caller's decision.
    """

    verb: str
    store: MVBaselineStore
    path: str
    ctx: Any
    changes: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    skipped_state: str | None = None  # the facility's lifecycle state when it was skipped


# --------------------------------------------------------------------------- store + data


def mv_store_path(config: dict, *, base_dir: str = ".", ctx=None):
    """The M&V baseline store a config uses: its top-level ``"mv_store"`` (resolved against
    ``base_dir``), else ``state/<facility_id>/mv_baselines.json`` in its workspace, else
    ``None``."""
    from .config import _facility_context, _path, _state_path

    if config.get("mv_store"):
        return _path(base_dir, config["mv_store"])
    return _state_path(ctx or _facility_context(config, base_dir), MV_BASELINES_FILE)


def open_mv_store(config: dict, *, base_dir: str = ".", ctx=None, required: bool = True):
    """``(store, path, ctx)``: the config's M&V baseline store, bound to its facility.

    ``required`` raises ``ValueError`` when no store can be named (outside a workspace with no
    ``"mv_store"``); otherwise ``(None, None, ctx)`` comes back.
    """
    from .config import _facility_context

    ctx = ctx or _facility_context(config, base_dir)
    path = mv_store_path(config, base_dir=base_dir, ctx=ctx)
    if path is None:
        if required:
            raise ValueError(
                "no M&V baseline store: inside a portfolio workspace it is "
                f"state/<facility_id>/{MV_BASELINES_FILE}; outside one, name it with the config's "
                'top-level "mv_store"'
            )
        return None, None, ctx
    store = MVBaselineStore.load(
        path, facility_id=ctx.bound, legacy_sites=ctx.legacy_sites if ctx.bound else None
    )
    return store, path, ctx


def _facility_state(ctx) -> str | None:
    if not (ctx.workspace and ctx.facility_id):
        return None
    from .portfolio import Portfolio

    try:
        return Portfolio(ctx.workspace).facility(ctx.facility_id).get("state")
    except KeyError:
        return None


def meter_series(config: dict, *, base_dir: str = ".", prep=None, equips=None) -> list:
    """Every ``mv`` entry's meters as :class:`MeterSeries` (all the data the source holds).

    Equipment discovery is the ordinary run's (:func:`camber.config._prepare`), so a suspended
    facility finds no meters -- see docs/PORTFOLIO.md.
    """
    from .config import _prepare
    from .mandv import _mvform
    from .mandv.intervalfit import daily_energy_vs_temp
    from .model.roles import Role
    from .resolve import resolve
    from .rules.base import _merge_shared

    prep = prep or _prepare(config, base_dir)
    want = None if not equips else set(equips)
    out = []
    for k, entry in enumerate(config.get("mv") or []):
        if entry.get("bills") is not None:  # billing entries are not versioned (0.92)
            continue
        role = Role(entry.get("role", Role.ENERGY_RATE.value))
        extra = _mvform.driver_roles(entry)
        for ref in prep.refs_by_class.get(entry["class"], []):
            if want is not None and ref.equip not in want:
                continue
            full = _merge_shared(
                resolve(ref, prep.mapping, (role, Role.OAT, *extra), resample="1h"), prep.shared
            )
            if full is None or full.empty or role not in full.columns or Role.OAT not in full:
                continue
            if any(r not in full.columns for r in extra):
                continue
            daily = daily_energy_vs_temp(full[role].dropna(), full[Role.OAT].dropna())
            daily = _mvform.add_drivers(daily, entry, full)
            out.append(MeterSeries(k, entry, ref.equip, role.value, mv_kind(role), daily))
    return out


def _window(daily: pd.DataFrame, win) -> pd.DataFrame:
    return daily.loc[_day(win[0]) : _day(win[1]) + pd.Timedelta(hours=23)]


def _declared_method(entry: dict) -> tuple:
    from .config import _mv_method_spec

    method, kernel, declared = _mv_method_spec(
        entry, entry.get("period"), entry.get("reporting_period")
    )
    return method, kernel, declared


def baseline_window_check(n_with_data: int, period, entry: dict) -> tuple:
    """``(missing_frac, why)`` for a baseline window; ``why`` is ``None`` when it is long enough.

    The one baseline-length rule (#21 §1.6.8): a window shorter than the entry's
    ``min_baseline_days`` (365) or missing more than ``max_missing_frac`` of its days is short.
    :func:`fit_version` refuses it (unless overridden); the plain config ``mv`` path fits it with
    this as a caveat (0.90.1, issue #59). ``n_with_data`` counts the window's days with data.
    """
    pol = RebaselinePolicy.from_entry(entry)
    n_days = int((_day(period[1]) - _day(period[0])).days) + 1
    missing = 1.0 - n_with_data / float(max(n_days, 1))
    if n_days >= pol.min_baseline_days and missing <= pol.max_missing_frac:
        return missing, None
    return missing, (
        f"the window {_ds(period[0])}..{_ds(period[1])} is {n_days} days "
        f"({n_with_data} with data, {missing:.0%} missing); an M&V baseline needs "
        f"{pol.min_baseline_days} consecutive days with at most {pol.max_missing_frac:.0%} "
        "missing (SEP 2019 Ed. 2 §4.2; IPMVP 2012 §4.5.2; CalTRACK §3.1.3)"
    )


def fit_version(daily: pd.DataFrame, period, *, entry: dict, allow_short: bool = False) -> dict:
    """Fit a baseline over ``period`` the way the ``mv`` config path does, with its verdicts.

    Returns ``model``, ``st`` (FitStats), ``tests`` (RegressionTests), ``sep`` (the SEP verdict),
    ``sub`` (the fit frame), ``missing_frac`` and ``caveats``. A window shorter than the policy's
    ``min_baseline_days`` or missing more than ``max_missing_frac`` of its days raises
    ``ValueError`` unless ``allow_short`` (then a caveat records it; SEP methods are always
    declined on a short baseline, #21 §1.6.8).
    """
    from .mandv import _mvform
    from .mandv.stats import (
        cv_rmse_max_for,
        fit_stats,
        logical_signs,
        model_regression_tests,
        sep_validity,
    )

    sub = _window(daily, period)
    missing, why = baseline_window_check(len(sub), period, entry)
    caveats = []
    if why:
        if not allow_short:
            raise ValueError(why + " -- pass --allow-short to freeze it anyway, with a caveat")
        caveats.append("short or gappy baseline accepted by override: " + why)
    if len(sub) < 10:
        raise ValueError(f"only {len(sub)} days of data in {period}")
    y = sub["energy"].to_numpy(float)
    model = _mvform.fit(sub)
    if model is None:
        raise ValueError("no change-point model could be fitted")
    X = _mvform.design_rows(sub, model)
    st = fit_stats(
        y,
        model.predict(X),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("daily"),
        time_index=sub.index,
    )
    tests = model_regression_tests(model, X, y, time_index=sub.index)
    vd = sep_validity(tests, signs=logical_signs(model))
    return {
        "model": model,
        "st": st,
        "tests": tests,
        "sep": vd,
        "sub": sub,
        "missing_frac": round(max(missing, 0.0), 4),
        "caveats": caveats,
    }


def _fit_valid(prov: dict) -> dict:
    fs = (prov or {}).get("fit_stats") or {}
    sv = (prov or {}).get("sep_validity") or {}
    return {"g14": fs.get("accept"), "sep": sv.get("sep_valid")}


def _plan(verb, config, base_dir, store_path=None) -> MVPlan:
    from .config import _facility_context

    ctx = _facility_context(config, base_dir)
    if store_path:
        store = MVBaselineStore.load(
            store_path, facility_id=ctx.bound, legacy_sites=ctx.legacy_sites if ctx.bound else None
        )
        path = store_path
    else:
        store, path, ctx = open_mv_store(config, base_dir=base_dir, ctx=ctx)
    plan = MVPlan(verb, store, path, ctx)
    state = _facility_state(ctx)
    if state is not None and state != "active":
        plan.skipped_state = state
    return plan


# --------------------------------------------------------------------------- write verbs


def plan_freeze(
    config: dict,
    *,
    base_dir: str = ".",
    reason: str,
    accepted_by: str,
    equips=None,
    allow_short: bool = False,
    at: str | None = None,
    store_path: str | None = None,
) -> MVPlan:
    """Freeze version 1 for every meter without a frozen M&V baseline (never overwrites one).

    The entry must declare its ``period`` and its ``method`` (not ``auto``): a reported saving
    needs a declared method, and it is frozen with the baseline (the method-shopping guard).
    """
    plan = _plan("freeze", config, base_dir, store_path)
    if plan.skipped_state:
        return plan
    site = plan.ctx.site
    at = at or _now()
    for ms in meter_series(config, base_dir=base_dir, equips=equips):
        entry = ms.entry
        label = f"{ms.equip}/{ms.kind}"
        if plan.store.get(site, ms.equip, ms.kind) is not None:
            plan.refused.append({"baseline": label, "why": "already frozen (left untouched)"})
            continue
        if not entry.get("period"):
            plan.refused.append({"baseline": label, "why": "mv entry has no period to fit"})
            continue
        try:
            method, kernel, declared = _declared_method(entry)
            if not declared or method == "auto":
                raise ValueError(
                    "declare mv.method (forecast, backcast, chaining or standard_conditions) "
                    "before freezing: the declared method is frozen with the baseline"
                )
            fit = fit_version(ms.daily, entry["period"], entry=entry, allow_short=allow_short)
        except ValueError as e:
            plan.refused.append({"baseline": label, "why": str(e)})
            continue
        prov = mv_provenance(
            fit["model"],
            fit["sub"],
            reason=reason,
            accepted_by=accepted_by,
            method=method,
            kernel=kernel,
            validity=entry.get("validity", "g14"),
            fit_stats=fit["st"],
            regression_tests=fit["tests"],
            sep_verdict=fit["sep"],
            extra={
                "period": [_ds(entry["period"][0]), _ds(entry["period"][1])],
                "policy": RebaselinePolicy.from_entry(entry).as_dict(),
                "missing_frac": fit["missing_frac"],
                "caveats": fit["caveats"],
                "entry": ms.entry_index,
            },
        )
        rec = plan.store.freeze_version(
            fit["model"],
            site=site,
            equip=ms.equip,
            kind=ms.kind,
            frozen_at=at,
            period=entry["period"],
            provenance=prov,
        )
        plan.changes.append(_change(rec, fit, "froze"))
    return plan


def _change(rec, fit, what: str) -> dict:
    st = fit["st"]
    return {
        "baseline": f"{rec.equip}/{rec.kind}",
        "action": what,
        "version": version_label(rec),
        "window": [rec.period_start, rec.period_end],
        "model": getattr(fit["model"], "kind", type(fit["model"]).__name__),
        "r2": round(float(st.r2), 4),
        "cv_rmse": round(float(st.cv_rmse), 4),
        "g14_accept": bool(st.accept),
        "sep_valid": bool(fit["sep"].sep_valid),
        "method": rec.provenance.get("method"),
        "fit_frame_sha256": rec.provenance.get("fit_frame_sha256"),
        "caveats": list(fit.get("caveats") or []),
    }


def _assess(ms: MeterSeries, store, site, rec, *, as_of=None, versions=None):
    """Triggers of one version, over its reporting days (read-only)."""
    from .mandv.rebaseline import assess_triggers

    entry = ms.entry
    pol = RebaselinePolicy.from_entry(entry)
    events, statics = events_from_entry(entry)
    vs = versions if versions is not None else store.versions(site, ms.equip, ms.kind)
    nxt = next(
        (
            v
            for v in vs
            if int(v.provenance.get("version", 0)) == int(rec.provenance.get("version", 0)) + 1
        ),
        None,
    )
    method = rec.provenance.get("method")
    inter = entry.get("intermediate_period") if method == "chaining" else None
    daily = ms.daily if as_of is None else ms.daily.loc[: _day(as_of) + pd.Timedelta(hours=23)]
    trig = assess_triggers(
        daily,
        store.model_of(rec),
        baseline=[rec.period_start, rec.period_end],
        policy=pol,
        as_of=as_of,
        fit_valid=_fit_valid(rec.provenance),
        events=events,
        static_factors=statics,
        ledger=store.ledger(rec),
        next_version_start=None if nxt is None else nxt.period_start,
        intermediate_period=inter,
    )
    return trig, pol, events, statics


def plan_rebaseline(
    config: dict,
    *,
    base_dir: str = ".",
    equip: str,
    reason: str,
    accepted_by: str,
    period=None,
    as_of=None,
    trigger_ids=(),
    from_proposal: dict | None = None,
    at: str | None = None,
    store_path: str | None = None,
) -> MVPlan:
    """Plan version *n+1* for one meter: the attributed rebaseline (never automatic).

    The new window is ``period`` if given, else the proposal's (``from_proposal``: a
    ``camber mv propose --json`` row, whose model is then used **exactly as proposed**, after its
    fit-frame sha256 is checked against the data), else :func:`~camber.mandv.rebaseline.
    new_baseline_window`'s. It must satisfy the window rule for the unresolved rebaseline-class
    triggers it answers (``trigger_ids``, default all of them): start at least ``settle_days``
    after the latest, overlap no ECM installation window, miss at most ``max_missing_frac``, give a
    valid model and cover the expected conditions. No such trigger means no rebaseline: declare
    the change in ``mv[].rebaseline.events`` (T2) first.
    """
    from .mandv.rebaseline import (
        _install_windows,
        _overlaps,
        event_phrase,
        mv_model_from_dict,
        new_baseline_window,
        window_anchor,
    )

    plan = _plan("rebaseline", config, base_dir, store_path)
    if plan.skipped_state:
        return plan
    site = plan.ctx.site
    series = [m for m in meter_series(config, base_dir=base_dir, equips=[equip])]
    if not series:
        plan.refused.append({"baseline": equip, "why": "no such M&V meter in the config"})
        return plan
    for ms in series:
        label = f"{ms.equip}/{ms.kind}"
        rec = plan.store.get(site, ms.equip, ms.kind)
        if rec is None:
            plan.refused.append(
                {"baseline": label, "why": "nothing frozen yet: `camber mv freeze`"}
            )
            continue
        trig, pol, _ev, _sf = _assess(ms, plan.store, site, rec, as_of=as_of)
        live = [t for t in trig if not t.resolved and t.outcome == "rebaseline"]
        if trigger_ids:
            want = set(trigger_ids)
            live = [t for t in live if t.key in want or t.id in want]
            missing = want - {t.key for t in live} - {t.id for t in live}
            if missing:
                plan.refused.append(
                    {
                        "baseline": label,
                        "why": f"no unresolved rebaseline-class "
                        f"trigger {sorted(missing)} (assessed: "
                        f"{[t.key for t in trig]})",
                    }
                )
                continue
        if not live:
            plan.refused.append(
                {
                    "baseline": label,
                    "why": (
                        "no unresolved trigger calls for a rebaseline (assessed: "
                        + (
                            ", ".join(
                                f"{t.key} {t.outcome}{' resolved' if t.resolved else ''}"
                                for t in trig
                            )
                            or "none"
                        )
                        + "); declare the change in mv.rebaseline.events (T2) to rebaseline for it"
                    ),
                }
            )
            continue
        anchor = window_anchor(trig, live)
        daily = ms.daily if as_of is None else ms.daily.loc[: _day(as_of) + pd.Timedelta(hours=23)]
        if from_proposal is not None:
            win = from_proposal.get("window") or {}
            period = win.get("window")
            if not period or not win.get("model"):
                plan.refused.append(
                    {
                        "baseline": label,
                        "why": "the proposal has no window/model "
                        f"({from_proposal.get('declined_reason')})",
                    }
                )
                continue
        if period is None:
            prop = new_baseline_window(
                daily, after=anchor.date, policy=pol, as_of=as_of, event=event_phrase(anchor)
            )
            if not prop.ok:
                plan.refused.append(
                    {
                        "baseline": label,
                        "why": prop.declined_reason,
                        "days_needed": prop.days_needed,
                    }
                )
                continue
            period = prop.window
        start, end = _day(period[0]), _day(period[1])
        why = []
        if start < _day(anchor.date) + pd.Timedelta(days=pol.settle_days):
            why.append(
                f"starts before {pol.settle_days} settle days after the trigger {anchor.key}"
            )
        hit = _overlaps(start, end, _install_windows(pol.schedule))
        if hit is not None:
            why.append(f"overlaps the ECM installation window {hit[0].date()}..{hit[1].date()}")
        if why:
            plan.refused.append({"baseline": label, "why": "; ".join(why)})
            continue
        try:
            fit = fit_version(daily, [start, end], entry=ms.entry)
        except ValueError as e:
            plan.refused.append({"baseline": label, "why": str(e)})
            continue
        if from_proposal is not None:
            prop_sha = (from_proposal.get("window") or {}).get("fit_frame_sha256")
            if prop_sha != fit_frame_sha256(fit["sub"]):
                plan.refused.append(
                    {
                        "baseline": label,
                        "why": "the data under the proposed "
                        "window changed since the proposal (fit-frame sha256 "
                        "differs); propose again",
                    }
                )
                continue
            fit = _refit_stats(mv_model_from_dict(from_proposal["window"]["model"]), fit)
        need = {
            "g14": (fit["st"].accept,),
            "sep": (fit["sep"].sep_valid,),
            "both": (fit["st"].accept, fit["sep"].sep_valid),
        }[pol.require_validity]
        if not all(need):
            plan.refused.append(
                {
                    "baseline": label,
                    "why": f"the new window's model is not valid under {pol.require_validity}",
                }
            )
            continue
        from .mandv import _mvform
        from .mandv.coverage import assess_coverage

        cov = assess_coverage(fit["model"], _mvform.design_rows(ms.daily, fit["model"]))
        if cov.tier == "severe":
            plan.refused.append(
                {
                    "baseline": label,
                    "why": f"the new model does not cover the expected conditions ({cov.reason})",
                }
            )
            continue
        method = rec.provenance.get("method") or "forecast"
        kernel = rec.provenance.get("kernel") or "g14"
        prov = mv_provenance(
            fit["model"],
            fit["sub"],
            reason=reason,
            accepted_by=accepted_by,
            method=method,
            kernel=kernel,
            validity=ms.entry.get("validity", "g14"),
            trigger_ids=[t.key for t in live],
            trigger_date=anchor.date,
            fit_stats=fit["st"],
            regression_tests=fit["tests"],
            sep_verdict=fit["sep"],
            coverage=cov.as_dict(),
            extra={
                "period": [_ds(start), _ds(end)],
                "policy": pol.as_dict(),
                "missing_frac": fit["missing_frac"],
                "triggers": [t.as_dict() for t in live],
                "entry": ms.entry_index,
                "from_proposal": from_proposal is not None,
            },
        )
        new = plan.store.rebaseline(
            fit["model"],
            site=site,
            equip=ms.equip,
            kind=ms.kind,
            at=at or _now(),
            period=[start, end],
            provenance=prov,
        )
        ch = _change(new, fit, "rebaselined")
        ch["supersedes"] = new.provenance.get("supersedes_version")
        ch["triggers"] = [t.key for t in live]
        plan.changes.append(ch)
    return plan


def _refit_stats(model, fit: dict) -> dict:
    """``fit`` with the statistics of a given (proposed) model over the same frame."""
    from .mandv import _mvform
    from .mandv.stats import (
        cv_rmse_max_for,
        fit_stats,
        logical_signs,
        model_regression_tests,
        sep_validity,
    )

    sub = fit["sub"]
    T, y = _mvform.design_rows(sub, model), sub["energy"].to_numpy(float)
    st = fit_stats(
        y,
        model.predict(T),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("daily"),
        time_index=sub.index,
    )
    tests = model_regression_tests(model, T, y, time_index=sub.index)
    return {
        **fit,
        "model": model,
        "st": st,
        "tests": tests,
        "sep": sep_validity(tests, signs=logical_signs(model)),
    }


def plan_adjust(
    config: dict,
    *,
    base_dir: str = ".",
    equip: str,
    specs: list,
    reason: str,
    accepted_by: str,
    as_of=None,
    at: str | None = None,
    store_path: str | None = None,
) -> MVPlan:
    """Plan recording accepted NRA / static-factor entries on a meter's **live** version.

    ``specs`` are ledger dicts (the ``mv[].adjustments`` form). An ``indicator`` entry is
    estimated here against the version's model, on its baseline days or its reporting days
    (``fit_period``; default the period its ``start`` falls in). The entries pass the guards of
    :func:`~camber.mandv.adjustments.apply_adjustments` (the confounding guard on the entry's ECM
    schedule, SEP's evidence rule under ``validity: sep``) before anything is recorded.
    """
    from .mandv import _mvform
    from .mandv.adjustments import (
        _check_entries,
        adjustment_from_dict,
        estimate_nre_indicator,
    )

    plan = _plan("adjust", config, base_dir, store_path)
    if plan.skipped_state:
        return plan
    site = plan.ctx.site
    series = meter_series(config, base_dir=base_dir, equips=[equip])
    if not series:
        plan.refused.append({"baseline": equip, "why": "no such M&V meter in the config"})
        return plan
    if not isinstance(specs, list) or not specs:
        raise ValueError("the adjustment spec must be a non-empty JSON list of ledger entries")
    for ms in series:
        label = f"{ms.equip}/{ms.kind}"
        rec = plan.store.get(site, ms.equip, ms.kind)
        if rec is None:
            plan.refused.append(
                {"baseline": label, "why": "nothing frozen yet: `camber mv freeze`"}
            )
            continue
        model = plan.store.model_of(rec)
        pol = RebaselinePolicy.from_entry(ms.entry)
        b = _window(ms.daily, [rec.period_start, rec.period_end])
        rep_end = _day(as_of) if as_of is not None else _day(ms.daily.index.max())
        r = ms.daily.loc[
            _day(rec.period_end) + pd.Timedelta(days=1) : rep_end + pd.Timedelta(hours=23)
        ]
        try:
            entries = []
            for k, spec in enumerate(specs):
                if not isinstance(spec, dict):
                    raise ValueError(f"entry {k} must be an object, got {spec!r}")
                d = dict(spec)
                fp = d.pop("fit_period", None)
                if (
                    d.get("kind", "nra") == "nra"
                    and d.get("method") == "indicator"
                    and "rate" not in d
                ):
                    start = _day(d["start"])
                    fp = fp or ("reporting" if start > _day(rec.period_end) else "baseline")
                    frame = b if fp == "baseline" else r
                    entries.append(
                        estimate_nre_indicator(
                            _mvform.design_rows(frame, model),
                            frame["energy"].values,
                            frame.index,
                            start=start,
                            fit_period=fp,
                            model=model,
                            reason=d.get("reason") or "",
                            end=d.get("end"),
                            evidence=d.get("evidence"),
                            approved_by=d.get("approved_by"),
                        )
                    )
                else:
                    entries.append(adjustment_from_dict(d))
            _check_entries(entries, schedule=pol.schedule, validity=ms.entry.get("validity", "g14"))
        except (ValueError, TypeError, KeyError) as e:
            plan.refused.append({"baseline": label, "why": str(e)})
            continue
        added = plan.store.add_adjustments(
            entries,
            site=site,
            equip=ms.equip,
            kind=ms.kind,
            accepted_by=accepted_by,
            reason=reason,
            at=at,
        )
        plan.changes.append(
            {
                "baseline": label,
                "action": "adjusted",
                "version": version_label(rec),
                "entries": [
                    f"{e['entry'].get('method')} from {e['entry'].get('start')}: "
                    f"{e['entry'].get('reason')}"
                    for e in added
                ],
                "skipped_duplicates": len(entries) - len(added),
            }
        )
    return plan


# --------------------------------------------------------------------------- read-only verbs


def propose(config: dict, *, base_dir: str = ".", as_of=None, equips=None, store_path=None) -> dict:
    """Triggers, the rebaseline proposal and the SEP method proposal per meter (read-only).

    Returns ``{"facility_id", "as_of", "skipped_state", "meters": [...]}``; each meter row has the
    live version, its ``triggers``, the ``rebaseline`` proposal (:class:`~camber.mandv.
    rebaseline.RebaselineProposal`) and, when the entry names a ``reporting_period``, the
    ``method_proposal`` of :func:`~camber.mandv.methods.select_method` (with its fitted models'
    ``as_dict``). A meter with nothing frozen gets only the method proposal.
    """
    from .mandv.methods import select_method
    from .mandv.rebaseline import propose_rebaseline

    plan = _plan("propose", config, base_dir, store_path)
    out = {
        "facility_id": plan.ctx.facility_id,
        "as_of": None if as_of is None else _ds(as_of),
        "skipped_state": plan.skipped_state,
        "store": plan.path,
        "meters": [],
    }
    if plan.skipped_state:
        return out
    site = plan.ctx.site
    for ms in meter_series(config, base_dir=base_dir, equips=equips):
        row: dict = {"equip": ms.equip, "kind": ms.kind, "entry": ms.entry_index}
        rec = plan.store.get(site, ms.equip, ms.kind)
        daily = ms.daily if as_of is None else ms.daily.loc[: _day(as_of) + pd.Timedelta(hours=23)]
        if rec is not None:
            row["version"] = version_label(rec)
            row["window"] = [rec.period_start, rec.period_end]
            _trig, pol, events, statics = _assess(ms, plan.store, site, rec, as_of=as_of)
            prop = propose_rebaseline(
                daily,
                plan.store.model_of(rec),
                baseline=[rec.period_start, rec.period_end],
                policy=pol,
                as_of=as_of,
                fit_valid=_fit_valid(rec.provenance),
                events=events,
                static_factors=statics,
                ledger=plan.store.ledger(rec),
                intermediate_period=(
                    ms.entry.get("intermediate_period")
                    if rec.provenance.get("method") == "chaining"
                    else None
                ),
            )
            row["rebaseline"] = prop.as_dict()
            sub = _window(ms.daily, [rec.period_start, rec.period_end])
            row["baseline_data_changed"] = fit_frame_sha256(sub) != rec.provenance.get(
                "fit_frame_sha256"
            )
        rp = ms.entry.get("reporting_period")
        base = ms.entry.get("period") or (
            [rec.period_start, rec.period_end] if rec is not None else None
        )
        if rp and base and ms.entry.get("model") == "cp_driver":
            row["method_proposal"] = {
                "error": "not run: the SEP method proposal ranks temperature-only models, and "
                'this entry declares mv.model "cp_driver"'
            }
        elif rp and base:
            try:
                mp = select_method(
                    daily,
                    baseline=[_ds(base[0]), _ds(base[1])],
                    reporting=[_ds(rp[0]), _ds(rp[1])],
                    standard_conditions=ms.entry.get("normal_year"),
                )
                d = mp.as_dict()
                d.pop("results", None)
                row["method_proposal"] = d
            except (ValueError, KeyError) as e:
                row["method_proposal"] = {"error": str(e)}
        out["meters"].append(row)
    return out


@dataclass
class MeterChain:
    """The savings of one meter across its baseline versions (see :func:`chained_report`)."""

    equip: str
    kind: str
    versions: list
    segments: list
    links: list
    chain: Any  # MethodResult (a single forecast, or a sequential chain), or None
    adjusted: list  # AdjustedResult per link (or None where no ledger / refused)
    cusum: pd.DataFrame  # date, version, projected, actual (reported days only)
    triggers: list
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """JSON-safe summary (the per-day CUSUM frame stays out)."""
        from .mandv.rebaseline import _json_safe

        return _json_safe(
            {
                "equip": self.equip,
                "kind": self.kind,
                "versions": self.versions,
                "segments": self.segments,
                "links": [ln.as_dict() for ln in self.links],
                "chain": None if self.chain is None else self.chain.as_dict(),
                "adjusted": [None if a is None else a.as_dict() for a in self.adjusted],
                "triggers": [t.as_dict() for t in self.triggers],
                "caveats": list(self.caveats),
            }
        )


def _version_row(rec) -> dict:
    p = rec.provenance or {}
    return {
        "version": version_label(rec),
        "window": [rec.period_start, rec.period_end],
        "frozen_at": rec.frozen_at,
        "accepted_by": rec.accepted_by,
        "reason": rec.reason,
        "method": p.get("method"),
        "model": (p.get("model") or {}).get("kind"),
        "trigger_ids": p.get("trigger_ids") or [],
        "adjustments": len(p.get("adjustments") or []),
        "verified": MVBaselineStore.verify(rec),
    }


def chained_report(
    config: dict, *, base_dir: str = ".", as_of=None, equips=None, store_path=None
) -> dict:
    """Savings chained across every baseline version, per meter (read-only).

    Each version reports its segment (:func:`~camber.mandv.rebaseline.version_segments`, bounded
    by the entry's ``reporting_period`` when it declares one) as a forecast against that version's
    frozen model (``baseline_version`` set), restated by that
    version's recorded ledger; an unresolved blocking trigger inside a segment cuts it at the
    trigger's date (partial, with a caveat). Two or more reported segments are combined by
    :func:`~camber.mandv.methods.sequential_chain` (each link dated from the store, so its ledger
    can be dated without row indexes). Returns ``{"facility_id", "skipped_state", "meters":
    [MeterChain, ...]}``.
    """
    from .mandv import _mvform
    from .mandv.methods import forecast_savings, sequential_chain
    from .mandv.rebaseline import event_phrase, first_block
    from .mandv.stats import fit_stats

    plan = _plan("report", config, base_dir, store_path)
    out: dict = {
        "facility_id": plan.ctx.facility_id,
        "skipped_state": plan.skipped_state,
        "store": plan.path,
        "meters": [],
    }
    if plan.skipped_state:
        return out
    site = plan.ctx.site
    for ms in meter_series(config, base_dir=base_dir, equips=equips):
        vs = plan.store.versions(site, ms.equip, ms.kind)
        if not vs:
            continue
        end = _day(as_of) if as_of is not None else _day(ms.daily.index.max())
        rp = ms.entry.get("reporting_period")
        if rp:
            end = min(end, _day(rp[1]))
        segs = version_segments(vs, end=end)
        for seg in segs:  # the declared reporting period bounds every segment (e.g. ECM settling)
            if rp and seg["period"] is not None:
                a, b = max(_day(seg["period"][0]), _day(rp[0])), _day(seg["period"][1])
                seg["period"] = [_ds(a), _ds(b)] if b >= a else None
        links, adjusted, frames, trig_all, caveats, wins = [], [], [], [], [], []
        for rec, seg in zip(vs, segs):
            label = version_label(rec)
            if seg["period"] is None:
                continue
            model = plan.store.model_of(rec)
            trig, _pol, _e, _s = _assess(ms, plan.store, site, rec, as_of=end, versions=vs)
            trig_all += trig
            s0, s1 = _day(seg["period"][0]), _day(seg["period"][1])
            blk = first_block([t for t in trig if _day(t.date) <= s1])
            if blk is not None:
                cut = _day(blk.date) - pd.Timedelta(days=1)
                hint = (
                    "rebaseline (`camber mv propose`)"
                    if blk.outcome == "rebaseline"
                    else "record an NRA (`camber mv adjust`) or rebaseline"
                )
                caveats.append(
                    f"{label}: savings from {blk.date} on declined -- {event_phrase(blk)}: "
                    f"{blk.detail}; {hint}"
                )
                if cut < s0:
                    seg["period"] = None
                    seg["declined"] = f"unresolved trigger {blk.key} at the segment start"
                    continue
                seg["period"] = [_ds(s0), _ds(cut)]
                seg["partial"] = True
                s1 = cut
            rep = _window(ms.daily, [s0, s1])
            base = _window(ms.daily, [rec.period_start, rec.period_end])
            if len(rep) == 0 or len(base) < 10:
                seg["declined"] = "no data"
                continue
            if fit_frame_sha256(base) != rec.provenance.get("fit_frame_sha256"):
                caveats.append(
                    f"{label}: the data under this baseline changed since it was frozen "
                    "(fit-frame sha256 differs)"
                )
            st = fit_stats(
                base["energy"].values,
                model.predict(_mvform.design_rows(base, model)),
                _mvform.n_params(model),
                time_index=base.index,
            )
            res = forecast_savings(
                model,
                _mvform.design_rows(rep, model),
                rep["energy"].values,
                cv_rmse=st.cv_rmse,
                n_baseline=st.n,
                p_baseline=_mvform.n_params(model),
                rho=st.rho_lag1,
                kernel=rec.provenance.get("kernel") or "g14",
                baseline_version=label,
            )
            if rec.provenance.get("method") not in (None, "forecast"):
                res.caveats.append(
                    f"{label} declares method {rec.provenance.get('method')!r}; the chain across "
                    "versions reports each segment as a forecast against its version"
                )
            links.append(res)
            wins.append(
                {"period": [_ds(s0), _ds(s1)], "model_window": [rec.period_start, rec.period_end]}
            )
            adjusted.append(_adjust_link(plan.store, rec, res, rep, model, ms.entry, caveats))
            proj = pd.Series(model.predict(_mvform.design_rows(rep, model)), index=rep.index)
            frames.append(
                pd.DataFrame({"version": label, "projected": proj, "actual": rep["energy"]})
            )
        chain = None
        good = [ln for ln in links if not ln.declined]
        if len(good) == len(links) and len(links) >= 2:
            chain = sequential_chain(
                links,
                windows=wins,
                baseline_version="+".join(str(ln.baseline_version) for ln in links),
            )
        elif len(links) == 1:
            chain = links[0]
        cus = (
            pd.concat(frames)
            if frames
            else pd.DataFrame(columns=["version", "projected", "actual"])
        )
        out["meters"].append(
            MeterChain(
                ms.equip,
                ms.kind,
                [_version_row(v) for v in vs],
                segs,
                links,
                chain,
                adjusted,
                cus,
                trig_all,
                caveats,
            )
        )
    return out


def _adjust_link(store, rec, res, rep, model, entry, caveats):
    """The version's recorded ledger applied to one link (``None`` when there is none)."""
    from .mandv import _mvform
    from .mandv.adjustments import EcmSchedule, apply_adjustments

    led = store.ledger(rec)
    if not led or res.declined:
        return None
    lo, hi = rep.index.min(), rep.index.max()
    mine = [a for a in led if lo <= _day(a.start) <= hi]
    if not mine:
        return None
    try:
        return apply_adjustments(
            res,
            mine,
            index=rep.index,
            drivers=_mvform.design_rows(rep, model),
            measured=rep["energy"].values,
            model=model,
            schedule=EcmSchedule.from_dict(
                {
                    "ecm_dates": list(entry.get("ecm_dates") or ()),
                    "settle_days": entry.get("settle_days", 14),
                }
            ),
            validity=entry.get("validity", "g14"),
        )
    except ValueError as e:
        caveats.append(f"{res.baseline_version}: recorded adjustments not applied: {e}")
        return None


def versioned_rows(daily: pd.DataFrame, rec) -> tuple:
    """``(baseline daily frame, sha matches)`` of a stored version (for the run path)."""
    sub = _window(daily, [rec.period_start, rec.period_end])
    return sub, fit_frame_sha256(sub) == (rec.provenance or {}).get("fit_frame_sha256")
