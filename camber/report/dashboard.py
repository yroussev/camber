"""Self-contained HTML dashboard — the fuse-graphing-and-diagnostics MVP (A → B → E → I).

Assembles the readiness ribbon (A), the fault-annotated multi-trend (B), the load carpet (E),
and the data-quality dashboard (I) into one self-contained HTML page (matplotlib figures inlined
as base64 PNG — no web framework, no external assets), with the run's findings ranked beneath.
Dependency-light: matplotlib + stdlib only.
"""

from __future__ import annotations

import base64
import html as _html
import io

from ..charts.carpet import load_carpet
from ..charts.multitrend import fault_multitrend
from ..charts.quality_dashboard import quality_dashboard
from ..charts.readiness import readiness_ribbon
from ..integrate.tickets import _attr
from ..rules.triage import rank_findings
from .linking import LINK_STYLE, interactive_scatter_html

_SECTION_TITLES = {"A": "Ingest readiness", "B": "Fault-annotated trends",
                   "E": "Load carpet", "I": "Data-quality dashboard"}


def fig_to_base64(fig, *, dpi: int = 90) -> str:
    """Render a matplotlib figure to a base64 PNG data URI and close it."""
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _section_image(letter, df, *, spans, carpet_col, multitrend_cols, normalize) -> str:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(13, 4))
    if letter == "A":
        readiness_ribbon(df, ax=ax)
    elif letter == "B":
        fault_multitrend(df, multitrend_cols, spans=spans, ax=ax, normalize=normalize)
    elif letter == "E":
        num = df.select_dtypes(include="number")
        col = carpet_col if carpet_col is not None else (num.columns[0] if len(num.columns) else None)
        if col is None:
            ax.text(0.5, 0.5, "no numeric column for a load carpet", ha="center", va="center")
            ax.axis("off")
        else:
            load_carpet(df[col], ax=ax, title=f"Load carpet — {col}")
    elif letter == "I":
        quality_dashboard(df, ax=ax)
    else:
        raise ValueError(f"unknown section {letter!r}; use A/B/E/I")
    return fig_to_base64(fig)


def _rules_map(rules) -> dict:
    """Normalize ``rules`` (a Registry, a {name: rule} dict, or an iterable of rules) to a map."""
    if rules is None:
        return {}
    if hasattr(rules, "names") and hasattr(rules, "get"):        # a Registry
        return {n: rules.get(n) for n in rules.names()}
    if isinstance(rules, dict):
        return dict(rules)
    return {getattr(r, "name", str(i)): r for i, r in enumerate(rules)}


def render_evidence_blocks(ranked, rules_map, frame_for) -> str:
    """Shared pattern-J evidence rendering used by the dashboard and the audit report.

    ``frame_for(equip)`` resolves the role-frame for a finding's equipment (the dashboard passes a
    single ``df`` for all; the audit passes a per-equipment map). A finding renders only when its
    rule exposes an ``evidence()`` hook and a frame exists. A failed render closes only its own
    figure (never the whole pyplot registry) and is skipped.
    """
    from ..charts.evidence import finding_evidence, render_evidence

    if not rules_map:
        return ""
    import matplotlib.pyplot as plt

    blocks = []
    for r in ranked:
        f = r.finding
        equip = _attr(f, "equip", "")
        rule = rules_map.get(_attr(f, "rule", ""))
        frame = frame_for(equip)
        if rule is None or frame is None:
            continue
        fig = None
        try:
            ev = finding_evidence(rule, equip, frame)
            if ev is None:
                continue
            fig, ax = plt.subplots(figsize=(8, 4))
            render_evidence(ev, frame, ax=ax)
            img = fig_to_base64(fig)                    # closes fig on success
        except Exception:
            if fig is not None:
                plt.close(fig)                          # close only this figure, not plt.close("all")
            continue
        cap = _html.escape(f"{equip} · {_attr(f, 'rule', '')} · {_attr(f, 'summary', '')}")
        blocks.append(f"<figure><img src='{img}' alt='evidence'>"
                      f"<figcaption>{cap}</figcaption></figure>")
    return "".join(blocks)


def _evidence_images(ranked, df, rules_map) -> str:
    """Dashboard evidence: one shared ``df`` resolves every finding's frame (single-frame view)."""
    return render_evidence_blocks(ranked, rules_map, lambda _equip: df)


def _findings_table(ranked) -> str:
    if not ranked:
        return "<p>No actionable findings.</p>"
    rows = ["<tr><th>#</th><th>Severity</th><th>Equip</th><th>Rule</th>"
            "<th>Summary</th><th>$/yr</th></tr>"]
    for r in ranked:
        f = r.finding
        cost = (_attr(f, "metrics", {}) or {}).get("annual_cost_usd", "")
        cost = f"${cost:,.0f}" if isinstance(cost, (int, float)) else ""
        rows.append(
            f"<tr><td>{r.rank}</td><td>{_html.escape(r.severity)}</td>"
            f"<td>{_html.escape(str(_attr(f, 'equip', '')))}</td>"
            f"<td>{_html.escape(str(_attr(f, 'rule', '')))}</td>"
            f"<td>{_html.escape(str(_attr(f, 'summary', '')))}</td><td>{cost}</td></tr>")
    return "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"


_STYLE = ("body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#222}"
          "h1{margin-bottom:4px}h2{margin-top:28px;border-bottom:1px solid #ddd}"
          "img{max-width:100%;height:auto}table{border-collapse:collapse;font-size:13px}"
          "th{background:#f4f4f4;text-align:left}")


def _pick_link_cols(df, link_x, link_y):
    """Choose the (x, y) columns for the interactive scatter: default x to an OAT-like column,
    y to the first other column."""
    cols = list(df.columns)
    if not cols:
        return None, None
    x = link_x
    if x is None:
        x = next((c for c in cols if "OAT" in getattr(c, "name", str(c)).upper()), cols[0])
    y = link_y if link_y is not None else next((c for c in cols if c is not x), None)
    return x, y


def _interactive_section(df, link_x, link_y) -> str:
    """Build the brush-able interactive-scatter section (empty string if not renderable)."""
    x, y = _pick_link_cols(df, link_x, link_y)
    if x is None or y is None or x is y:
        return ""
    d = df[[x, y]].dropna()
    if len(d) < 2:
        return ""
    frag = interactive_scatter_html(d[x], d[y], d.index,
                                    xlabel=str(getattr(x, "name", x)),
                                    ylabel=str(getattr(y, "name", y)))
    return "<h2>Interactive — brush to select</h2>" + frag


def build_dashboard(df, *, findings=None, spans=None, sections=("A", "B", "E", "I"),
                    title: str = "CAMBER dashboard", rank_by: str = "severity",
                    top_n: int = 20, carpet_col=None, multitrend_cols=None,
                    normalize: bool = True, rules=None, evidence: bool = True,
                    interactive: bool = False, link_x=None, link_y=None) -> str:
    """Build a self-contained HTML dashboard string.

    ``df`` is a wide point/role frame (DatetimeIndex). ``findings`` are listed ranked beneath the
    charts. ``spans`` (``{label: boolean Series}``) shade fault evidence in section B.

    Pattern J — when ``rules`` is supplied (a Registry, a {name: rule} map, or an iterable of rules)
    and ``evidence`` is on, each actionable finding whose rule exposes an ``evidence(equip, frame)``
    hook renders its own evidence chart beneath the table.

    ``interactive`` adds a brush-able inline-SVG scatter (vanilla JS, no framework) of ``link_y``
    vs ``link_x`` — box-select a region to list the selected timestamps; defaults pick an OAT-like
    x and the first other column for y. Option flags: ``sections`` (subset of A/B/E/I), ``rank_by``
    ("severity"/"cost"), ``top_n``, ``carpet_col``, ``multitrend_cols``, ``normalize``, ``rules``,
    ``evidence``, ``interactive``, ``link_x``, ``link_y``.
    """
    style = _STYLE + (LINK_STYLE if interactive else "")
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style>"
             f"<title>{_html.escape(title)}</title></head><body>",
             f"<h1>{_html.escape(title)}</h1>"]
    for letter in sections:
        img = _section_image(letter, df, spans=spans, carpet_col=carpet_col,
                             multitrend_cols=multitrend_cols, normalize=normalize)
        parts.append(f"<h2>{letter}. {_SECTION_TITLES.get(letter, letter)}</h2>"
                     f"<img src='{img}' alt='{_SECTION_TITLES.get(letter, letter)}'>")
    if interactive:
        parts.append(_interactive_section(df, link_x, link_y))
    if findings is not None:
        key = "annual_cost_usd" if rank_by == "cost" else None
        ranked = rank_findings(findings, magnitude_key=key, actionable_only=True)[:top_n]
        parts.append(f"<h2>Findings (ranked by {_html.escape(rank_by)})</h2>")
        parts.append(_findings_table(ranked))
        if evidence and rules is not None:
            imgs = _evidence_images(ranked, df, _rules_map(rules))
            if imgs:
                parts.append("<h2>Evidence</h2>" + imgs)
    parts.append("</body></html>")
    return "\n".join(parts)
