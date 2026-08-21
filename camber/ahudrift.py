"""AHU air-side drift **co-movement diagnosis** -- localize an air handler's drift to one subsystem.

Five drift detectors watch one air handler, and each is deliberately narrow:

* :class:`camber.rules.fan_efficiency_rule.FanEfficiencyDrift` -- fan power-at-matched-airflow (an
  *excess* is wire-to-air efficiency loss);
* :class:`camber.rules.filter_loading_rule.FilterLoadingDrift` -- filter DP-at-matched-airflow (a
  *rise* is a loading filter);
* :class:`camber.rules.duct_static_rule.DuctStaticControlDrift` -- duct static-at-matched-airflow (a
  *fall* is the fan not holding setpoint, a *rise* is over-pressurization);
* :class:`camber.rules.coil_valve_rule.CoilValveDrift` -- coil valve-at-matched-ΔT (a *creep* is
  coil heat-transfer loss); a cooling and a heating coil can each drift, so up to two appear;
* :class:`camber.rules.economizer_damper_rule.EconomizerDamperDrift` -- outdoor-air
  fraction-at-matched-damper-command (an *up* is a leaking / stuck-open damper, a *down* is a stuck
  or slipping-closed one).

:func:`diagnose_ahu_drift` reads the individual Findings, names each drifting signal's localized
cause, flags **corroboration** when two or more agree, and -- the headline -- runs the **fan-power
disambiguation** that no single signal can do. A fan-power excess alone is ambiguous between *the
fan degrading* (belt slip, bearing drag, motor / VFD) and *the fan fighting added air-path
resistance* (a loading filter, a rising duct static); the filter and static signals resolve it:

* fan-power excess **with** a loading filter or a rising duct static -> the **air path** (fix the
  filter / check the ductwork first) -- the fan power is corroborating, not a separate fan fault;
* fan-power excess **with** the duct static *falling* below setpoint -> the **fan** is working
  harder yet losing static -- fan / drivetrain degradation;
* fan-power excess with a **clean** filter and **steady** static -> the fan itself;
* fan-power excess with **no** filter or static point mapped -> ambiguous, and the rule says so.

It splits the AHU into a **fan** (mechanical), an **air-path** (filter + static delivery), a
**coil** (heat transfer), and an **outdoor-air** (economizer OA mixing) side, reports a ``locus``
(steady · fan · air-path · coil · outdoor-air · ahu-wide) + an ``ahu_wide`` flag (more than one side
drifting), and stays screening-grade -- corroboration raises priority and specificity, not the
severity tier. The economizer is an independent side (like a coil): it corroborates and can make the
verdict AHU-wide, but it is deliberately **outside** the fan-power disambiguation, because its
signal is outdoor-air fraction, not fan power. This is the air-side twin of
:func:`camber.pumpdrift.diagnose_pump_drift`'s flow-vs-head check. Pure over Findings -- no data, no
I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["AhuDriftDiagnosis", "diagnose_ahu_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
_DEGRADING = ("warn", "fault")

_FAN_CAUSE = "fan mechanical degradation (belt slip / bearing drag / motor or VFD)"


@dataclass
class AhuDriftDiagnosis:
    """A single per-AHU verdict synthesized from the five air-side drift Findings.

    ``severity`` is the worst of the contributing signals; ``locus`` says where the drift sits
    (``steady`` · ``fan`` · ``air-path`` · ``coil`` · ``outdoor-air`` · ``ahu-wide``); ``ahu_wide``
    is True when more than one AHU side is drifting; ``causes`` are the localized causes
    (worst-first); ``signals`` maps each rule (coils keyed ``coil_valve_drift:<which>``) to
    ``{drift, severity, cause, side}``; ``corroborated`` is True when two or more signals drift
    together.
    """

    equip: str
    severity: str
    locus: str
    ahu_wide: bool
    causes: list
    signals: dict
    corroborated: bool
    summary: str
    caveats: list

    def as_dict(self) -> dict:
        """Return the diagnosis as a plain dict."""
        return asdict(self)


def _get(findings, rule):
    return next((f for f in findings if getattr(f, "rule", None) == rule), None)


def _all(findings, rule):
    return [f for f in findings if getattr(f, "rule", None) == rule]


def _declined(f, caveats) -> bool:
    m = getattr(f, "metrics", {}) or {}
    if m.get("declined"):
        caveats.append(f"{f.rule} could not be evaluated ({m.get('reason', 'declined')})")
        return True
    return False


def diagnose_ahu_drift(findings, *, equip: str | None = None) -> AhuDriftDiagnosis:
    """Synthesize the four air-side drift Findings for one AHU into one localized diagnosis.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``); the
    air-side ones (``fan_efficiency_drift``, ``filter_loading_drift``, ``duct_static_drift``,
    ``coil_valve_drift``, ``economizer_damper_drift``) are picked out by rule name and the rest
    ignored. A declined signal is a caveat, not a cause.
    """
    fs = list(findings)
    if equip is None:
        equip = next((getattr(f, "equip", "") for f in fs if getattr(f, "equip", "")), "")

    signals: dict = {}
    scored: list = []  # (severity, cause, side)
    caveats: list = []

    def _record(key: str, severity: str, drift, cause: str, side: str) -> None:
        if severity in _DEGRADING:
            signals[key] = {"drift": drift, "severity": severity, "cause": cause, "side": side}
            scored.append((severity, cause, side))
        else:
            signals[key] = {"drift": drift, "severity": severity, "cause": None, "side": side}

    # --- filter first: it (with static) disambiguates the fan-power excess ------------------------
    filter_deg = False
    filter_evaluated = False
    ff = _get(fs, "filter_loading_drift")
    if ff is not None and not _declined(ff, caveats):
        filter_evaluated = ff.severity != "info"
        filter_deg = ff.severity in _DEGRADING
        if filter_deg:
            _record(
                "filter_loading_drift",
                ff.severity,
                ff.metrics.get("filter_dp_drift_inwc"),
                "air filter loading (dirty filter, rising resistance)",
                "air-path",
            )

    # --- duct static: two-sided, routes by direction ---------------------------------------------
    static_up = static_down = False
    static_evaluated = False
    sf = _get(fs, "duct_static_drift")
    if sf is not None and not _declined(sf, caveats):
        static_evaluated = sf.severity != "info"
        if sf.severity in _DEGRADING:
            up = sf.metrics.get("duct_static_drift_direction") == "up"
            static_up, static_down = up, not up
            cause, side = (
                (
                    "duct over-pressurization (sensor reading low / stuck damper / low demand)",
                    "air-path",
                )
                if up
                else (
                    "fan cannot hold duct-static setpoint (fan/belt degradation, or duct leakage)",
                    "fan",
                )
            )
            _record(
                "duct_static_drift",
                sf.severity,
                sf.metrics.get("duct_static_drift_inwc"),
                cause,
                side,
            )

    # --- fan power: the disambiguation ------------------------------------------------------------
    fanf = _get(fs, "fan_efficiency_drift")
    if fanf is not None and not _declined(fanf, caveats) and fanf.severity in _DEGRADING:
        drift = fanf.metrics.get("fan_power_drift_kw")
        if filter_deg or static_up:  # Case A -- fighting added air-path resistance
            _record(
                "fan_efficiency_drift",
                fanf.severity,
                drift,
                "excess fan power fighting added air-path resistance (loading filter / rising "
                "static) -- corroborating; address the air path first",
                "air-path",
            )
            caveats.append(
                "fan power excess co-moves with a loading filter / rising duct static -- the fan "
                "is fighting added resistance, not degrading; fix the air path before the fan"
            )
        elif static_down:  # Case B -- working harder yet losing static
            _record("fan_efficiency_drift", fanf.severity, drift, _FAN_CAUSE, "fan")
            caveats.append(
                "fan power excess with duct static falling below setpoint -- the fan is working "
                "harder yet losing static, consistent with fan / drivetrain degradation"
            )
        elif filter_evaluated or static_evaluated:  # Case C -- clean air path
            _record("fan_efficiency_drift", fanf.severity, drift, _FAN_CAUSE, "fan")
            caveats.append(
                "fan power excess with a clean filter and steady duct static -- the excess "
                "isolates to the fan itself"
            )
        else:  # Case D -- nothing to disambiguate with
            _record(
                "fan_efficiency_drift",
                fanf.severity,
                drift,
                "excess fan power -- fan degradation or unmeasured added air-path resistance",
                "fan",
            )
            caveats.append(
                "excess fan power but no filter or duct-static point is mapped to disambiguate fan "
                "degradation from added air-path resistance"
            )

    # --- coils: one or two, each named on the coil side ------------------------------------------
    for cf in _all(fs, "coil_valve_drift"):
        if _declined(cf, caveats):
            continue
        if cf.severity not in _DEGRADING:
            continue
        m = cf.metrics
        which = m.get("coil_valve_which") or (
            "cooling"
            if "cooling-coil" in getattr(cf, "summary", "")
            else "heating"
            if "heating-coil" in getattr(cf, "summary", "")
            else None
        )
        label = which or "coil"
        cause = (
            f"{label}-coil fouling / waterside starvation / air bypass / valve-authority loss"
            if which
            else "coil heat-transfer loss (valve creep)"
        )
        _record(
            f"coil_valve_drift:{which}" if which else "coil_valve_drift",
            cf.severity,
            m.get("coil_valve_drift_pct"),
            cause,
            "coil",
        )

    # --- economizer: OA-delivery, an independent side (NOT part of the fan-power disambiguation) --
    ef = _get(fs, "economizer_damper_drift")
    if ef is not None and not _declined(ef, caveats) and ef.severity in _DEGRADING:
        m = ef.metrics
        up = m.get("econ_oa_fraction_drift_direction") == "up"
        cause = (
            "economizer over-delivering outdoor air (OA damper leaking / stuck or slipping open)"
            if up
            else "economizer under-delivering outdoor air (OA damper stuck or slipping closed -- "
            "lost free cooling / under-ventilation)"
        )
        _record(
            "economizer_damper_drift",
            ef.severity,
            m.get("econ_oa_fraction_drift_pct"),
            cause,
            "outdoor-air",
        )

    # --- synthesis -------------------------------------------------------------------------------
    severity = "ok"
    for sev, _c, _s in scored:
        if _RANK[sev] > _RANK[severity]:
            severity = sev
    causes = [c for _s, c, _side in sorted(scored, key=lambda t: -_RANK[t[0]])]
    corroborated = len(scored) >= 2
    sides = {side for _s, _c, side in scored}
    ahu_wide = len(sides) >= 2

    if not scored:
        locus = "steady"
    elif ahu_wide:
        locus = "ahu-wide"
    else:
        locus = next(iter(sides))

    if corroborated:
        caveats.append(
            "multiple air-side signals are drifting together -- a stronger, more localized "
            "diagnosis, but still screening-grade; confirm on a walkdown"
        )
    if ahu_wide:
        caveats.append(
            "more than one AHU subsystem (fan / air-path / coil / outdoor-air) is drifting -- an "
            "AHU-wide cause is more likely than one component"
        )

    if not causes:
        summary = f"{equip}: AHU air-side steady vs baseline"
    else:
        tag = " (AHU-wide)" if ahu_wide else ""
        summary = f"{equip}: AHU air-side drift ({locus}) -- {'; '.join(causes)}{tag}"

    return AhuDriftDiagnosis(
        equip=equip,
        severity=severity,
        locus=locus,
        ahu_wide=ahu_wide,
        causes=causes,
        signals=signals,
        corroborated=corroborated,
        summary=summary,
        caveats=caveats,
    )
