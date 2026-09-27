"""HTML page for M&V savings chained across baseline versions (``camber mv report``).

Renders :func:`camber.mvrun.chained_report`: per meter, the baseline versions with their
provenance, each version's reported segment (a forecast against that version, restated by its
recorded ledger), the chain across the versions (:func:`camber.mandv.methods.sequential_chain`),
the triggers and the caveats, and -- with ``charts=True`` -- the chained CUSUM with one segment
per version and the rebaseline markers (:func:`camber.charts.cusum_chart.chained_cusum_plot`).

Two things are always on the page, above the numbers: that CAMBER never rebaselines by itself
(every version names who accepted it and why), and that a chain across versions is **CAMBER's
extension** of SEP chaining when it has more than one intermediate step. Pure string building;
the chart path imports matplotlib lazily.
"""

from __future__ import annotations

import html as _html

__all__ = ["mv_report_html"]

_BANNER = (
    "Every baseline version below was frozen or superseded by an attributed, audited operator "
    "decision (<code>camber mv freeze</code> / <code>camber mv rebaseline</code>); CAMBER never "
    "rebaselines automatically. An unresolved trigger declines the saving after its date. Bands "
    "are model-error only and are known to under-cover in practice (Touzani et al. 2019)."
)


def _e(x) -> str:
    return _html.escape("" if x is None else str(x))


def _num(x, fmt="{:,.0f}") -> str:
    return "–" if x is None else fmt.format(x)


def _table(head, rows) -> str:
    out = ["<tr>" + "".join(f"<th>{_e(h)}</th>" for h in head) + "</tr>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(out) + "</table>"


def _chart(m) -> str:
    import matplotlib

    matplotlib.use("Agg")  # a report is never rendered interactively
    import matplotlib.pyplot as plt

    from ..charts.cusum_chart import chained_cusum_plot
    from .dashboard import fig_to_base64

    markers, gaps = [], []
    for seg in m.segments:
        if seg.get("next_trigger_date"):
            markers.append({"date": seg["next_trigger_date"], "label": "rebaseline trigger"})
        if seg.get("gap_after"):
            gaps.append(seg["gap_after"])
    fig = None
    try:
        fig, ax = plt.subplots(figsize=(11, 4))
        chained_cusum_plot(
            m.cusum,
            markers=markers,
            gaps=gaps,
            ax=ax,
            title=f"{m.equip}: chained CUSUM across baseline versions",
        )
        img = fig_to_base64(fig)
    except Exception:  # noqa: BLE001 - an unrenderable chart must not lose the page
        if fig is not None:
            plt.close(fig)
        return ""
    return f"<figure><img src='{img}' alt='chained CUSUM for {_e(m.equip)}'></figure>"


def _meter(m, charts: bool) -> str:
    parts = [f"<h2>{_e(m.equip)} — {_e(m.kind)}</h2>"]
    parts.append("<h3>Baseline versions</h3>")
    parts.append(
        _table(
            [
                "Version",
                "Window",
                "Model",
                "Method",
                "Frozen at",
                "Accepted by",
                "Reason",
                "Triggers",
                "Ledger entries",
                "Provenance",
            ],
            [
                [
                    _e(v["version"]),
                    _e(f"{v['window'][0]} .. {v['window'][1]}"),
                    _e(v["model"]),
                    _e(v["method"]),
                    _e(v["frozen_at"]),
                    _e(v["accepted_by"]),
                    _e(v["reason"]),
                    _e(", ".join(v["trigger_ids"])),
                    _e(v["adjustments"]),
                    "verified" if v["verified"] else "<strong>MISMATCH</strong>",
                ]
                for v in m.versions
            ],
        )
    )
    rows = []
    li = 0
    for seg in m.segments:
        if seg.get("period") is None:
            rows.append(
                [
                    _e(seg["version"]),
                    "–",
                    _e(seg.get("declined") or "no reported days"),
                    "–",
                    "–",
                    "–",
                    "–",
                ]
            )
            continue
        ln = m.links[li] if li < len(m.links) else None
        adj = m.adjusted[li] if li < len(m.adjusted) else None
        li += 1
        if ln is None:
            continue
        rows.append(
            [
                _e(seg["version"]),
                _e(f"{seg['period'][0]} .. {seg['period'][1]}")
                + (" (partial)" if seg.get("partial") else ""),
                _e(_num(ln.savings)) if not ln.declined else _e(f"declined: {ln.declined_reason}"),
                _e(_num(ln.abs_uncertainty)),
                _e(_num(ln.enpi, "{:.3f}")),
                _e(_num(None if adj is None else adj.savings)),
                _e((ln.coverage or {}).get("tier")),
            ]
        )
    parts.append("<h3>Reported segments</h3>")
    parts.append(
        _table(
            [
                "Version",
                "Reported days",
                "Savings",
                "± (90%)",
                "SEnPI",
                "Adjusted savings",
                "Coverage",
            ],
            rows,
        )
    )
    ch = m.chain
    if ch is not None:
        ext = " (CAMBER extension: sequential chain)" if ch.method == "sequential_chain" else ""
        parts.append(
            f"<p><strong>Chained result{_e(ext)}:</strong> savings {_e(_num(ch.savings))} "
            f"± {_e(_num(ch.abs_uncertainty))} at {ch.confidence:.0%}; SEnPI "
            f"{_e(_num(ch.enpi, '{:.3f}'))}; baseline version(s) {_e(ch.baseline_version)}.</p>"
        )
    if charts:
        parts.append(_chart(m))
    live = [t for t in m.triggers if not t.resolved]
    if m.triggers:
        parts.append("<h3>Triggers</h3>")
        parts.append(
            _table(
                ["Key", "Trigger", "Detail", "Calls for", "Status"],
                [
                    [
                        _e(t.key),
                        _e(t.title),
                        _e(t.detail),
                        _e(t.outcome),
                        _e(f"resolved: {t.resolved_by}" if t.resolved else "UNRESOLVED"),
                    ]
                    for t in m.triggers
                ],
            )
        )
    cav = list(m.caveats) + ([] if ch is None else list(ch.caveats))
    if live:
        cav.insert(0, f"{len(live)} unresolved trigger(s): see `camber mv propose`")
    if cav:
        parts.append("<h3>Caveats</h3><ul>" + "".join(f"<li>{_e(c)}</li>" for c in cav) + "</ul>")
    return "".join(parts)


def mv_report_html(report: dict, *, charts: bool = True) -> str:
    """The page for a :func:`camber.mvrun.chained_report` result (a standalone HTML document)."""
    body = [
        "<!doctype html><html><head><meta charset='utf-8'><title>M&amp;V baselines</title>"
        "</head><body>",
        "<h1>M&amp;V savings across baseline versions</h1>",
        f"<p>Facility <code>{_e(report.get('facility_id'))}</code>.</p>",
        f"<p><em>{_BANNER}</em></p>",
    ]
    meters = report.get("meters") or []
    if report.get("skipped_state"):
        body.append(f"<p>The facility is {_e(report['skipped_state'])}: nothing was read.</p>")
    elif not meters:
        body.append(
            "<p>No meter has a frozen M&amp;V baseline (<code>camber mv freeze</code>).</p>"
        )
    for m in meters:
        body.append(_meter(m, charts))
    body.append("</body></html>")
    return "".join(body)
