"""Pattern J — rules as a chart engine: every rule renders its own evidence.

The differentiator: a Finding doesn't just *say* "simultaneous heat/cool 14% of hours" — it can
*render the trend that proves it*, with the violating spans shaded. The chart is the audit evidence
and the report figure, one artifact.

A rule opts in by implementing an optional ``evidence(equip, frame) -> Evidence`` hook (duck-typed —
no base class, back-compatible; rules without it are unaffected). An :class:`Evidence` names a
**renderer** (one of the pattern B/D/E/G primitives) and the roles / violating mask / template it
needs; :func:`render_evidence` dispatches to that renderer over the equipment's frame. This wires
the existing charts (`multitrend`, `oat_scatter`, `diagnostic`, `carpet`) into the FDD layer without
re-implementing any of them.

**Drift rules** are the one family that cannot use the default: their claim is not "these values are
wrong" but "these values have moved off a frozen line", so the evidence is the current period
scattered on that baseline's own band (:func:`camber.charts.diagnostic.fitted_band`), and the
columns the band is fitted on are often *derived* (a chiller's ``tons``, a coil's air-ΔT) rather
than raw roles. :func:`drift_evidence` builds that Evidence from any drift rule, and
:attr:`Evidence.frame` carries the prepared columns to the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .diagnostic import _col


@dataclass
class Evidence:
    """How to render one finding's evidence — a renderer + what it needs from the frame."""

    renderer: str  # "diagnostic" | "multitrend" | "oat_scatter" | "carpet"
    roles: list = field(default_factory=list)  # columns to plot (multitrend/carpet; [y] for oat)
    mask: object = None  # bool Series of violating timestamps (multitrend spans)
    template: object = None  # a DiagnosticTemplate for the "diagnostic" renderer
    label: str = "violation"  # span label
    title: str = ""
    # A prepared frame to render *instead of* the caller's role-frame. Rules whose model is fitted
    # on derived columns (a chiller's ``tons``, a coil's air-DT) cannot point a renderer at raw
    # roles, so they hand over the frame they actually fitted. None = use the caller's frame.
    frame: object = None


def render_evidence(evidence: Evidence, frame: pd.DataFrame, *, ax=None):
    """Render an :class:`Evidence` onto an Axes (created if ``ax`` is None). Returns ``(ax, mask)``.

    Dispatches to the pattern primitive named by ``evidence.renderer`` — reusing `diagnostic`,
    `multitrend`, `oat_scatter`, or `carpet` verbatim.
    """
    from ..model.roles import Role

    if evidence.frame is not None:
        frame = evidence.frame  # the rule prepared its own (derived) columns
    r = evidence.renderer
    if r == "diagnostic":
        from .diagnostic import diagnostic_scatter

        tmpl = evidence.template
        return diagnostic_scatter(frame, tmpl, ax=ax)  # type: ignore[arg-type]  # template
    if r == "multitrend":
        from .multitrend import fault_multitrend

        spans = {evidence.label: evidence.mask} if evidence.mask is not None else None
        ax = fault_multitrend(
            frame, list(evidence.roles) or None, spans=spans, ax=ax, title=evidence.title or None
        )
        return ax, evidence.mask
    if r == "oat_scatter":
        from .oat_scatter import oat_scatter

        y = _col(frame, evidence.roles[0])
        ax, _ = oat_scatter(
            y,
            _col(frame, Role.OAT),
            ax=ax,
            ylabel=getattr(evidence.roles[0], "name", str(evidence.roles[0])),
            title=evidence.title or None,
        )
        return ax, evidence.mask
    if r == "carpet":
        from .carpet import load_carpet

        ax = load_carpet(_col(frame, evidence.roles[0]), ax=ax, title=evidence.title or None)
        return ax, None
    raise ValueError(
        f"unknown evidence renderer {r!r}; use diagnostic/multitrend/oat_scatter/carpet"
    )


def drift_evidence(rule, equip: str, frame: pd.DataFrame, *, k: float = 2.0):
    """Evidence for a **drift** rule: the current period scattered on its frozen baseline's band.

    Duck-typed over any drift rule that declares two methods, so one implementation serves the whole
    family and a new detector opts in by declaring them:

    * ``drift_signature() -> (kind, load_col, metric_col)`` — the frozen-model kind to look up, and
      the x/y columns the baseline was fitted on (a :class:`Role`, or a string for a derived one);
    * ``drift_frame(frame)`` — the prepared frame those columns live on.

    Returns ``None`` when nothing is frozen for this equipment yet, when the prepared frame lacks
    the fitted columns, or when the rule does not declare the attributes -- i.e. exactly when there
    is no baseline to show the reading against. A drift chart with no band would invite the reader
    to judge the scatter by eye, which is the comparison the frozen baseline exists to make.
    """
    signature = getattr(rule, "drift_signature", None)
    prepare = getattr(rule, "drift_frame", None)
    if not callable(signature) or not callable(prepare):
        return None
    kind, load, metric = signature()
    if not kind or load is None or metric is None:
        return None

    store = getattr(rule, "store", None)
    model = None if store is None else store.model_for(getattr(rule, "site", ""), equip, kind)
    if model is None:
        return None

    try:
        prepared = prepare(frame)
    except Exception:  # noqa: BLE001 - a rule that cannot prepare simply has no evidence to show
        return None
    if prepared is None or getattr(prepared, "empty", True):
        return None
    for col in (load, metric):
        try:
            _col(prepared, col)
        except KeyError:
            return None

    from .diagnostic import fitted_band

    name = getattr(rule, "name", "drift")
    return Evidence(
        renderer="diagnostic",
        template=fitted_band(
            model,
            load,
            metric,
            k=k,
            name=f"{equip}: {name}",
            xlabel=getattr(load, "name", str(load)),
            ylabel=getattr(metric, "name", str(metric)),
        ),
        frame=prepared,
        title=f"{equip}: {name} vs frozen baseline",
    )


def finding_evidence(rule, equip: str, frame: pd.DataFrame):
    """Return an :class:`Evidence` for a rule's finding, or None.

    A rule may implement a tailored ``evidence(equip, frame)`` hook (which can shade the specific
    violating spans); a **drift** rule instead declares ``drift_signature``/``drift_frame`` and gets
    its frozen-baseline band via :func:`drift_evidence`. When neither applies — or both decline —
    every rule still gets **default** evidence: a multi-trend of the ``roles_required`` present in
    the frame, i.e. the data the rule examined. So pattern J covers the whole rule library, present
    and future, without a per-rule map.

    Returns None only when no required role is plottable (e.g. a fleet finding with no single
    frame).
    """
    hook = getattr(rule, "evidence", None)
    if callable(hook):
        ev = hook(equip, frame)
        if ev is not None:
            return ev
    # Drift rules opt in with drift_signature/drift_frame rather than a bespoke hook: their evidence
    # is the current period on the frozen baseline's band, which one implementation can build for
    # the whole family. Tried before the default trend, which would show the levels and hide the
    # only thing the rule actually claims -- movement away from that line.
    ev = drift_evidence(rule, equip, frame)
    if ev is not None:
        return ev
    # fleet/aggregate rules have no single-equipment frame -> no default evidence (a shared df is
    # not "this finding's" data); only per-equipment rules fall back to a default trend.
    if hasattr(rule, "analyze_fleet"):
        return None
    roles = [r for r in getattr(rule, "roles_required", ()) if r in getattr(frame, "columns", ())]
    if not roles:
        return None
    return Evidence(
        renderer="multitrend", roles=roles, title=f"{equip}: {getattr(rule, 'name', 'finding')}"
    )


def evidence_descriptor(evidence: Evidence) -> dict:
    """A JSON-friendly descriptor of an Evidence (renderer + roles + violating timestamps) — the
    payload a Finding can carry and the interactive-linking layer consumes; never the figure."""
    d = {
        "renderer": evidence.renderer,
        "roles": [getattr(r, "name", str(r)) for r in evidence.roles],
        "label": evidence.label,
    }
    if evidence.template is not None:
        d["template"] = getattr(evidence.template, "name", str(evidence.template))
    if evidence.mask is not None:
        m = pd.Series(evidence.mask).fillna(False).astype(bool)
        d["violations"] = [str(t) for t in m.index[m.to_numpy()]]
    return d
