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
        col = carpet_col or next((c for c in df.columns), None)
        load_carpet(df[col], ax=ax, title=f"Load carpet — {col}")
    elif letter == "I":
        quality_dashboard(df, ax=ax)
    else:
        raise ValueError(f"unknown section {letter!r}; use A/B/E/I")
    return fig_to_base64(fig)


def _findings_table(findings, *, rank_by: str, top_n: int) -> str:
    key = "annual_cost_usd" if rank_by == "cost" else None
    ranked = rank_findings(findings, magnitude_key=key, actionable_only=True)[:top_n]
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


def build_dashboard(df, *, findings=None, spans=None, sections=("A", "B", "E", "I"),
                    title: str = "CAMBER dashboard", rank_by: str = "severity",
                    top_n: int = 20, carpet_col=None, multitrend_cols=None,
                    normalize: bool = True) -> str:
    """Build a self-contained HTML dashboard string.

    ``df`` is a wide point/role frame (DatetimeIndex). ``findings`` are listed ranked beneath the
    charts. ``spans`` (``{label: boolean Series}``) shade fault evidence in section B. Option
    flags: ``sections`` (subset of A/B/E/I, in order), ``rank_by`` ("severity"/"cost"),
    ``top_n``, ``carpet_col``, ``multitrend_cols``, ``normalize``.
    """
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><style>{_STYLE}</style>"
             f"<title>{_html.escape(title)}</title></head><body>",
             f"<h1>{_html.escape(title)}</h1>"]
    for letter in sections:
        img = _section_image(letter, df, spans=spans, carpet_col=carpet_col,
                             multitrend_cols=multitrend_cols, normalize=normalize)
        parts.append(f"<h2>{letter}. {_SECTION_TITLES.get(letter, letter)}</h2>"
                     f"<img src='{img}' alt='{_SECTION_TITLES.get(letter, letter)}'>")
    if findings is not None:
        parts.append(f"<h2>Findings (ranked by {_html.escape(rank_by)})</h2>")
        parts.append(_findings_table(findings, rank_by=rank_by, top_n=top_n))
    parts.append("</body></html>")
    return "\n".join(parts)
