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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .diagnostic import _col


@dataclass
class Evidence:
    """How to render one finding's evidence — a renderer + what it needs from the frame."""

    renderer: str                # "diagnostic" | "multitrend" | "oat_scatter" | "carpet"
    roles: list = field(default_factory=list)   # columns to plot (multitrend/carpet; [y] for oat)
    mask: object = None          # bool Series of violating timestamps (multitrend spans)
    template: object = None      # a DiagnosticTemplate for the "diagnostic" renderer
    label: str = "violation"     # span label
    title: str = ""


def render_evidence(evidence: Evidence, frame: pd.DataFrame, *, ax=None):
    """Render an :class:`Evidence` onto an Axes (created if ``ax`` is None). Returns ``(ax, mask)``.

    Dispatches to the pattern primitive named by ``evidence.renderer`` — reusing `diagnostic`,
    `multitrend`, `oat_scatter`, or `carpet` verbatim.
    """
    from ..model.roles import Role

    r = evidence.renderer
    if r == "diagnostic":
        from .diagnostic import diagnostic_scatter
        return diagnostic_scatter(frame, evidence.template, ax=ax)
    if r == "multitrend":
        from .multitrend import fault_multitrend
        spans = {evidence.label: evidence.mask} if evidence.mask is not None else None
        ax = fault_multitrend(frame, list(evidence.roles) or None, spans=spans, ax=ax,
                              title=evidence.title or None)
        return ax, evidence.mask
    if r == "oat_scatter":
        from .oat_scatter import oat_scatter
        y = _col(frame, evidence.roles[0])
        ax, _ = oat_scatter(y, _col(frame, Role.OAT), ax=ax,
                            ylabel=getattr(evidence.roles[0], "name", str(evidence.roles[0])),
                            title=evidence.title or None)
        return ax, evidence.mask
    if r == "carpet":
        from .carpet import load_carpet
        ax = load_carpet(_col(frame, evidence.roles[0]), ax=ax, title=evidence.title or None)
        return ax, None
    raise ValueError(f"unknown evidence renderer {r!r}; "
                     "use diagnostic/multitrend/oat_scatter/carpet")


def finding_evidence(rule, equip: str, frame: pd.DataFrame):
    """Return an :class:`Evidence` for a rule's finding, or None.

    A rule may implement a tailored ``evidence(equip, frame)`` hook (which can shade the specific
    violating spans). When it doesn't — or the hook declines — every rule still gets **default**
    evidence: a multi-trend of the ``roles_required`` present in the frame, i.e. the data the rule
    examined. So pattern J covers the whole rule library, present and future, without a per-rule map.
    Returns None only when no required role is plottable (e.g. a fleet finding with no single frame).
    """
    hook = getattr(rule, "evidence", None)
    if callable(hook):
        ev = hook(equip, frame)
        if ev is not None:
            return ev
    # fleet/aggregate rules have no single-equipment frame -> no default evidence (a shared df is
    # not "this finding's" data); only per-equipment rules fall back to a default trend.
    if hasattr(rule, "analyze_fleet"):
        return None
    roles = [r for r in getattr(rule, "roles_required", ()) if r in getattr(frame, "columns", ())]
    if not roles:
        return None
    return Evidence(renderer="multitrend", roles=roles,
                    title=f"{equip}: {getattr(rule, 'name', 'finding')}")


def evidence_descriptor(evidence: Evidence) -> dict:
    """A JSON-friendly descriptor of an Evidence (renderer + roles + violating timestamps) — the
    payload a Finding can carry and the interactive-linking layer consumes; never the figure."""
    d = {"renderer": evidence.renderer,
         "roles": [getattr(r, "name", str(r)) for r in evidence.roles],
         "label": evidence.label}
    if evidence.template is not None:
        d["template"] = getattr(evidence.template, "name", str(evidence.template))
    if evidence.mask is not None:
        m = pd.Series(evidence.mask).fillna(False).astype(bool)
        d["violations"] = [str(t) for t in m.index[m.to_numpy()]]
    return d
