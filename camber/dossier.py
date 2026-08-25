"""Unified validation & credibility dossier — one artifact across every validation track.

CAMBER validates itself four ways, each with its own benchmark, metrics, and CI gate; this module
pulls them into one legible, sellable artifact (text / HTML / JSON) via :func:`build_dossier`
and the ``camber validate`` CLI verb:

* **Synthetic whole-suite FDD** (`camber.faultlab`) — every single-equipment rule scored against an
  injected fault + a G36 FC engine. **Live-recomputed** here (deterministic, no download).
* **Generated fleet FDD** (`camber.fleetlab`) — the G36 reset fleet detectors scored on a generated
  labeled multi-zone fleet, with correct-attribution. **Live-recomputed** (no download).
* **Real-data FDD (LBNL, CC-BY)** and **real-data M&V (BDG2, CC-BY)** — scored on public labeled
  datasets that need large downloads, so their headline results are carried here as **cited
  reference results** (with provenance + a reproduce command), not recomputed at import time. Two
  tests (see ``tests/test_dossier.py``) fail the build if a cited figure drifts from the committed
  BDG2 baseline or the ``docs/VALIDATION.md`` table — the numbers cannot silently rot.

The dossier embeds **no wall-clock timestamp** — it is anchored on the package version only, so two
builds are byte-identical (a stable, diff-able capstone). Dependency-light: numpy/pandas + stdlib,
and the HTML is a single self-contained file (no external assets), reusing the ``camber.report``
theme-safe style. See ``docs/VALIDATION.md`` for the full methodology.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field

from . import __version__, faultlab, fleetlab
from .eval import benchmark
from .report.dashboard import _STYLE
from .validation import RateCI, metrics_with_ci

__all__ = [
    "TrackResult",
    "ValidationDossier",
    "build_dossier",
]


@dataclass(frozen=True)
class TrackResult:
    """One validation track's headline result.

    ``kind`` is ``"live-recomputed"`` (run in front of you from pure CAMBER, no data) or
    ``"cited-reference"`` (a public-dataset result carried with provenance, reproduced via the named
    example). ``rates`` maps a label (``"tpr"``/``"fpr"``/``"acceptance"``/…) to a
    :class:`camber.validation.RateCI` (rate + 95% Wilson interval + n).
    """

    key: str
    title: str
    kind: str
    headline: str
    rates: dict = field(default_factory=dict)
    coverage: str = ""
    boundary: str = ""
    provenance: str = ""
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Return a plain, JSON-serializable dict (RateCI values expanded)."""
        return {
            "key": self.key,
            "title": self.title,
            "kind": self.kind,
            "headline": self.headline,
            "rates": {k: _ci_dict(v) for k, v in self.rates.items()},
            "coverage": self.coverage,
            "boundary": self.boundary,
            "provenance": self.provenance,
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class ValidationDossier:
    """The aggregated dossier: a package version + one :class:`TrackResult` per validation track."""

    version: str
    tracks: list = field(default_factory=list)

    def headline(self) -> str:
        """The single sellable sentence summarizing the whole dossier."""
        live = sum(1 for t in self.tracks if t.kind == "live-recomputed")
        cited = sum(1 for t in self.tracks if t.kind == "cited-reference")
        return (
            f"CAMBER {self.version} validation: {len(self.tracks)} tracks "
            f"({live} recomputed live, {cited} cited from public CC-BY data) — "
            "synthetic whole-suite FDD, generated multi-zone fleet FDD, real-data FDD (LBNL), "
            "and real-data M&V (BDG2)."
        )

    def as_dict(self) -> dict:
        """Return a JSON-serializable dict of the whole dossier."""
        return {
            "version": self.version,
            "headline": self.headline(),
            "tracks": [t.as_dict() for t in self.tracks],
        }

    def to_text(self) -> str:
        """Render a plain-text summary (one block per track)."""
        lines = [
            f"CAMBER validation dossier — v{self.version}",
            "=" * 68,
            self.headline(),
            "",
        ]
        for t in self.tracks:
            tag = "LIVE" if t.kind == "live-recomputed" else "CITED"
            lines.append(f"[{tag}] {t.title}")
            lines.append(f"    {t.headline}")
            if t.coverage:
                lines.append(f"    coverage: {t.coverage}")
            if t.boundary:
                lines.append(f"    boundary: {t.boundary}")
            if t.provenance:
                lines.append(f"    source:   {t.provenance}")
            lines.append("")
        lines.append(
            "LIVE tracks are recomputed on every run (deterministic, no download); CITED tracks "
            "are public-dataset results reproduced via the named example. See docs/VALIDATION.md."
        )
        return "\n".join(lines)

    def to_html(self) -> str:
        """Render a single self-contained HTML dossier (no external assets, theme-safe)."""
        return _to_html(self)


# ------------------------------------------------------------------------- cited reference figures
#
# Real-data tracks need large downloads, so their headline results are carried here as *cited*
# reference results (reproduce via the named example). tests/test_dossier.py cross-checks every
# value against the committed BDG2 baseline (exact) and the docs/VALIDATION.md table (LBNL) so a
# stale figure fails CI rather than misleading. Only CC-BY datasets already named in docs appear.

_REFERENCE: dict = {
    "lbnl_fdd": {
        "title": "Real-data FDD — LBNL FDD (CC-BY)",
        # only the TPR interval is published in docs/VALIDATION.md; the pooled FPR is a bare 25%
        # (its Wilson denominator isn't reconstructable from the table), so it is stated in the
        # headline text rather than fabricated as an interval.
        "rates": {
            "tpr": RateCI(0.89, 0.56, 0.98, 13),  # pooled OA-fraction across SDAHU+FCU+DDAHU
        },
        "headline": (
            "OA-fraction pooled TPR 89% [56–98%], FPR 25% across 3 equipment families "
            "(SDAHU 100%, FCU 100%, DDAHU 50%); drift + chiller-plant detectors scored too"
        ),
        "coverage": (
            "3 AHU/FCU families (n=13) for OA-fraction; coil-valve/economizer/duct-static drift on "
            "SDAHU; chiller-efficiency + cooling-tower approach on the chiller-plant subset"
        ),
        "boundary": (
            "OA-fraction degrades on dual-duct AHUs (mixing-box + mild-weather noise) and the "
            "modulating-valve leak under-fires — measured, not hidden"
        ),
        "provenance": "LBNL FDD (CC-BY); reproduce via examples/lbnl_fdd/benchmark.py",
    },
    "bdg2_mv": {
        "title": "Real-data M&V — BDG2 (CC-BY)",
        "rates": {
            "acceptance": RateCI(0.1526, 0.1377, 0.1689, 2044),  # pooled G14 baseline-model accept
            "acceptance_chilledwater": RateCI(0.3552, 0.3152, 0.3974, 518),
            "acceptance_electricity": RateCI(0.0839, 0.0710, 0.0989, 1526),
        },
        "headline": (
            "G14 baseline-model acceptance 15% [14–17%] pooled across 2,044 real meters "
            "(chilled-water 36%, electricity 8%); median CV(RMSE) 24%"
        ),
        "coverage": "~2,044 BDG2 meters (518 chilled-water, 1,526 electricity)",
        "boundary": (
            "whole-building energy is messy — half the chilled-water meters sit near the 30% "
            "CV(RMSE) acceptance line; acceptance (not TPR/FPR) is the M&V metric"
        ),
        "provenance": "BDG2 (CC-BY); reproduce via examples/bdg2/benchmark.py",
    },
}


# --------------------------------------------------------------------------- track builders


def _ci_dict(v) -> dict:
    """Expand a RateCI (or pass through a plain value) to a JSON-friendly dict."""
    if isinstance(v, RateCI):
        return {"rate": v.rate, "lo": v.lo, "hi": v.hi, "n": v.n}
    return {"value": v}


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _ci_str(c: RateCI) -> str:
    return f"{_pct(c.rate)} [{100 * c.lo:.0f}–{100 * c.hi:.0f}]"


def _synthetic_track(*, full: bool) -> TrackResult:
    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    ci = metrics_with_ci(rep.overall)
    cov = faultlab.coverage()
    g36 = faultlab.g36_accuracy()
    tpr, fpr = ci["true_positive_rate"], ci["false_positive_rate"]
    metrics = {
        "overall.tpr": round(tpr.rate, 4),
        "overall.fpr": round(fpr.rate, 4),
        "coverage.n_scored": len(cov["scored"]),
        "coverage.n_single": cov["n_single"],
        "g36.tpr": g36["tpr"],
        "g36.fpr": g36["fpr"],
        "g36.n_fc": g36["n_fc"],
    }
    if full:
        for name, c in rep.per_detector.items():
            metrics[f"{name}.tpr"] = round(c.true_positive_rate, 4)
            metrics[f"{name}.fpr"] = round(c.false_positive_rate, 4)
    return TrackResult(
        key="synthetic_fdd",
        title="Synthetic whole-suite FDD (camber.faultlab)",
        kind="live-recomputed",
        headline=(
            f"TPR {_ci_str(tpr)}, FPR {_pct(fpr.rate)} over {len(cov['scored'])} "
            f"single-equipment rules + a G36 FC engine (TPR {_pct(g36['tpr'])}, "
            f"{g36['n_fc']} FCs)"
        ),
        rates={"tpr": tpr, "fpr": fpr},
        coverage=(
            f"{len(cov['scored'])}/{cov['n_single']} single-equipment rules injected + scored; "
            f"{g36['n_fc']} representative G36 fault conditions"
        ),
        boundary="synthetic injected faults — external validity rests on the LBNL real-data track",
        metrics=metrics,
    )


def _fleet_track(*, full: bool) -> TrackResult:
    rep = benchmark(fleetlab.labeled_records(), fleetlab.targets())
    ci = metrics_with_ci(rep.overall)
    attrib = fleetlab.attribution()
    cov = fleetlab.coverage()
    tpr, fpr = ci["true_positive_rate"], ci["false_positive_rate"]
    mean_attrib = round(sum(attrib.values()) / len(attrib), 4) if attrib else 0.0
    metrics = {
        "fleet.overall.tpr": round(tpr.rate, 4),
        "fleet.overall.fpr": round(fpr.rate, 4),
        "fleet.correct_diagnosis": round(rep.correct_diagnosis, 4),
        "fleet.mean_attribution": mean_attrib,
        "coverage.n_fleet_scored": cov["n_fleet_scored"],
    }
    if full:
        for name, a in attrib.items():
            metrics[f"fleet.{name}.attribution"] = round(a, 4)
    return TrackResult(
        key="fleet_fdd",
        title="Generated multi-zone fleet FDD (camber.fleetlab)",
        kind="live-recomputed",
        headline=(
            f"TPR {_ci_str(tpr)}, FPR {_pct(fpr.rate)}, attribution {_pct(mean_attrib)} "
            f"across {cov['n_fleet_scored']} G36 reset fleet detectors"
        ),
        rates={"tpr": tpr, "fpr": fpr},
        coverage=(
            f"{cov['n_fleet_scored']} detectors (rogue-zone / cohort-starvation / "
            "reset-effectiveness x SAT + static), with correct-zone/AHU/mode attribution"
        ),
        boundary=(
            "generated from the public G36 Trim-&-Respond logic — internal-validity accuracy; "
            "external validity rests on the G36 citation, not real data"
        ),
        metrics=metrics,
    )


def _cited_track(key: str) -> TrackResult:
    ref = _REFERENCE[key]
    return TrackResult(
        key=key,
        title=ref["title"],
        kind="cited-reference",
        headline=ref["headline"],
        rates=dict(ref["rates"]),
        coverage=ref["coverage"],
        boundary=ref["boundary"],
        provenance=ref["provenance"],
        metrics={
            f"{label}.{f}": getattr(ci, f)
            for label, ci in ref["rates"].items()
            for f in ("rate", "lo", "hi", "n")
        },
    )


def build_dossier(*, full: bool = False) -> ValidationDossier:
    """Build the unified validation dossier.

    Live-recomputes the two pure tracks (synthetic ``faultlab`` + generated ``fleetlab``, no
    download) and cites the two real-data tracks (LBNL FDD, BDG2 M&V) from the committed reference
    figures. ``full=True`` adds per-detector / per-family breakdown into each track's ``metrics``.
    Deterministic and timestamp-free — anchored on the package version.
    """
    return ValidationDossier(
        version=__version__,
        tracks=[
            _synthetic_track(full=full),
            _fleet_track(full=full),
            _cited_track("lbnl_fdd"),
            _cited_track("bdg2_mv"),
        ],
    )


# --------------------------------------------------------------------------- HTML rendering

_DOSSIER_STYLE = (
    ".ds-badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;"
    "font-weight:700;vertical-align:middle}.ds-live{background:#2a7;color:#fff}"
    ".ds-cited{background:#69c;color:#fff}.ds-bar{height:12px;background:#e5e5e5;border-radius:6px;"
    "position:relative;margin:4px 0;max-width:320px}.ds-fill{height:12px;background:#2a7;"
    "border-radius:6px}.ds-ci{position:absolute;top:3px;height:6px;background:rgba(0,0,0,.28);"
    "border-radius:3px}.ds-note{color:#777;font-size:13px}.ds-b{color:#666;font-size:13px}"
)


def _bar_html(label: str, c: RateCI) -> str:
    """A pure-CSS rate bar with a Wilson-CI whisker (no images, fully self-contained)."""
    rate, lo, hi = 100 * c.rate, 100 * c.lo, 100 * c.hi
    return (
        f"<div class='ds-b'>{_html.escape(label)}: <b>{_ci_str(c)}</b> (n={c.n})</div>"
        f"<div class='ds-bar'><div class='ds-fill' style='width:{rate:.0f}%'></div>"
        f"<div class='ds-ci' style='left:{lo:.0f}%;width:{max(hi - lo, 1):.0f}%'></div></div>"
    )


def _track_html(t: TrackResult) -> str:
    badge = "ds-live" if t.kind == "live-recomputed" else "ds-cited"
    tag = "LIVE" if t.kind == "live-recomputed" else "CITED"
    bars = "".join(_bar_html(k, v) for k, v in t.rates.items() if isinstance(v, RateCI))
    parts = [
        f"<h3>{_html.escape(t.title)} <span class='ds-badge {badge}'>{tag}</span></h3>",
        f"<p>{_html.escape(t.headline)}</p>",
        bars,
        f"<p class='ds-note'><b>Coverage:</b> {_html.escape(t.coverage)}</p>",
        f"<p class='ds-note'><b>Boundary:</b> {_html.escape(t.boundary)}</p>",
    ]
    if t.provenance:
        parts.append(f"<p class='ds-note'><b>Source:</b> {_html.escape(t.provenance)}</p>")
    return "".join(parts)


def _to_html(d: ValidationDossier) -> str:
    style = _STYLE + _DOSSIER_STYLE
    title = f"CAMBER validation dossier — v{d.version}"
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style>"
        f"<title>{_html.escape(title)}</title></head><body>",
        f"<h1>{_html.escape(title)}</h1>",
        f"<p>{_html.escape(d.headline())}</p>",
    ]
    for kind, heading in (
        ("live-recomputed", "Recomputed live (deterministic, no download)"),
        ("cited-reference", "Cited from public CC-BY datasets (reproduce via the example)"),
    ):
        group = [t for t in d.tracks if t.kind == kind]
        if group:
            parts.append(f"<h2>{_html.escape(heading)}</h2>")
            parts.extend(_track_html(t) for t in group)
    parts.append(
        "<p class='ds-note'>Confidence intervals are 95% Wilson score intervals. LIVE tracks are "
        "recomputed each run; CITED tracks are cross-checked against the committed BDG2 baseline "
        "and the docs/VALIDATION.md table so they cannot rot. See docs/VALIDATION.md.</p>"
    )
    parts.append("</body></html>")
    return "".join(parts)
