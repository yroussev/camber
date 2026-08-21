"""Findings (and chiller diagnoses) → tabular export for BI / data-warehouse ingestion.

Turns a run's :class:`~camber.rules.base.Finding` results — or the whole-machine chiller roll-ups
from :func:`camber.chillerdiag.diagnose_chiller_drift` — into a flat table and writes it as
CSV, JSON, or Parquet — the shape a BI tool or warehouse loader expects. Metrics are flattened
into ``metric_*`` columns by default so each finding is one row with scalar columns. Uses
pandas + (for Parquet) the already-required pyarrow; no new dependency.
"""

from __future__ import annotations

import pandas as pd

from .tickets import _attr, fingerprint

_BASE_COLS = ["fingerprint", "site", "equip", "rule", "severity", "summary"]

# One row per pump-loop verdict; the columns a screening dashboard ranks and filters on.
_PUMP_DIAG_COLS = [
    "fingerprint",
    "site",
    "equip",
    "locus",
    "severity",
    "loop_wide",
    "corroborated",
    "causes",
    "n_caveats",
    "summary",
]

# One row per chiller roll-up verdict; the whole-machine columns a screening dashboard wants.
_DIAG_COLS = [
    "fingerprint",
    "site",
    "equip",
    "locus",
    "severity",
    "machine_wide",
    "condenser_severity",
    "evaporator_severity",
    "charge_cause",
    "causes",
    "n_caveats",
    "summary",
]


def findings_to_frame(
    findings, *, site: str = "", flatten_metrics: bool = True, columns=None
) -> pd.DataFrame:
    """Flatten findings into a DataFrame (one row per finding).

    Base columns: fingerprint, site, equip, rule, severity, summary. When ``flatten_metrics``,
    each finding's ``metrics`` become ``metric_<key>`` columns (scalar values only). ``columns``
    restricts/orders the output columns.
    """
    rows = []
    for f in findings:
        equip = _attr(f, "equip", "")
        rule = _attr(f, "rule", "")
        row = {
            "fingerprint": fingerprint(site, equip, rule),
            "site": site,
            "equip": equip,
            "rule": rule,
            "severity": _attr(f, "severity", "info"),
            "summary": _attr(f, "summary", ""),
        }
        if flatten_metrics:
            for k, v in (_attr(f, "metrics", {}) or {}).items():
                if isinstance(v, (int, float, str, bool)) or v is None:
                    row[f"metric_{k}"] = v
        rows.append(row)
    df = pd.DataFrame(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    elif not df.empty:
        extra = [c for c in df.columns if c not in _BASE_COLS]
        df = df.reindex(columns=[c for c in _BASE_COLS if c in df.columns] + sorted(extra))
    return df


def export_findings(
    findings,
    path: str,
    *,
    format: str | None = None,
    site: str = "",
    flatten_metrics: bool = True,
    columns=None,
) -> int:
    """Write findings to ``path`` as CSV / JSON / Parquet. Returns the row count.

    ``format`` is inferred from the file extension when None (``.csv`` / ``.json`` /
    ``.parquet``). JSON is written as records (a list of row objects).
    """
    df = findings_to_frame(findings, site=site, flatten_metrics=flatten_metrics, columns=columns)
    return _write_frame(df, path, format)


def diagnoses_to_frame(diagnoses, *, site: str = "", columns=None) -> pd.DataFrame:
    """Flatten chiller drift roll-ups into a DataFrame (one row per machine).

    ``diagnoses`` is an iterable of :class:`camber.chillerdiag.ChillerDriftDiagnosis` (or anything
    with that shape). Each row carries the whole-machine verdict — ``locus``, ``severity``,
    ``machine_wide``, the per-side severities, the charge cause, the joined ``causes`` and a caveat
    count — the columns a screening dashboard ranks and filters on. ``columns`` restricts/orders the
    output.
    """
    rows = []
    for d in diagnoses:
        equip = _attr(d, "equip", "")
        cond = _attr(d, "condenser", None)
        evap = _attr(d, "evaporator", None)
        charge = _attr(d, "charge", None)
        rows.append(
            {
                "fingerprint": fingerprint(site, equip, "chiller_drift"),
                "site": site,
                "equip": equip,
                "locus": _attr(d, "locus", ""),
                "severity": _attr(d, "severity", "ok"),
                "machine_wide": bool(_attr(d, "machine_wide", False)),
                "condenser_severity": _attr(cond, "severity", "") if cond is not None else "",
                "evaporator_severity": _attr(evap, "severity", "") if evap is not None else "",
                "charge_cause": charge.get("cause", "") if isinstance(charge, dict) else "",
                "causes": "; ".join(_attr(d, "causes", []) or []),
                "n_caveats": len(_attr(d, "caveats", []) or []),
                "summary": _attr(d, "summary", ""),
            }
        )
    df = pd.DataFrame(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    elif not df.empty:
        df = df.reindex(columns=[c for c in _DIAG_COLS if c in df.columns])
    return df


def export_diagnoses(
    diagnoses, path: str, *, format: str | None = None, site: str = "", columns=None
) -> int:
    """Write chiller drift roll-ups to ``path`` as CSV / JSON / Parquet. Returns the row count.

    ``format`` is inferred from the file extension when None. JSON is written as records.
    """
    df = diagnoses_to_frame(diagnoses, site=site, columns=columns)
    return _write_frame(df, path, format)


def pump_diagnoses_to_frame(diagnoses, *, site: str = "", columns=None) -> pd.DataFrame:
    """Flatten per-loop pump drift diagnoses into a DataFrame (one row per loop).

    ``diagnoses`` is an iterable of :class:`camber.pumpdrift.PumpDriftDiagnosis` (or anything with
    that shape). Each row carries the loop verdict — ``locus``, ``severity``, ``loop_wide``,
    ``corroborated``, the joined ``causes`` and a caveat count. ``columns`` restricts/orders output.
    """
    rows = []
    for d in diagnoses:
        equip = _attr(d, "equip", "")
        rows.append(
            {
                "fingerprint": fingerprint(site, equip, "pump_drift"),
                "site": site,
                "equip": equip,
                "locus": _attr(d, "locus", ""),
                "severity": _attr(d, "severity", "ok"),
                "loop_wide": bool(_attr(d, "loop_wide", False)),
                "corroborated": bool(_attr(d, "corroborated", False)),
                "causes": "; ".join(_attr(d, "causes", []) or []),
                "n_caveats": len(_attr(d, "caveats", []) or []),
                "summary": _attr(d, "summary", ""),
            }
        )
    df = pd.DataFrame(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    elif not df.empty:
        df = df.reindex(columns=[c for c in _PUMP_DIAG_COLS if c in df.columns])
    return df


def export_pump_diagnoses(
    diagnoses, path: str, *, format: str | None = None, site: str = "", columns=None
) -> int:
    """Write per-loop pump drift diagnoses to ``path`` as CSV / JSON / Parquet; return the count."""
    df = pump_diagnoses_to_frame(diagnoses, site=site, columns=columns)
    return _write_frame(df, path, format)


# One row per AHU verdict; the air-side columns a screening dashboard ranks and filters on.
_AHU_DIAG_COLS = [
    "fingerprint",
    "site",
    "equip",
    "locus",
    "severity",
    "ahu_wide",
    "corroborated",
    "causes",
    "n_caveats",
    "summary",
]


def ahu_diagnoses_to_frame(diagnoses, *, site: str = "", columns=None) -> pd.DataFrame:
    """Flatten per-AHU air-side drift diagnoses into a DataFrame (one row per AHU).

    ``diagnoses`` is an iterable of :class:`camber.ahudrift.AhuDriftDiagnosis` (or anything with
    that shape). Each row carries the AHU verdict — ``locus``, ``severity``, ``ahu_wide``,
    ``corroborated``, the joined ``causes`` and a caveat count. ``columns`` restricts/orders output.
    """
    rows = []
    for d in diagnoses:
        equip = _attr(d, "equip", "")
        rows.append(
            {
                "fingerprint": fingerprint(site, equip, "ahu_drift"),
                "site": site,
                "equip": equip,
                "locus": _attr(d, "locus", ""),
                "severity": _attr(d, "severity", "ok"),
                "ahu_wide": bool(_attr(d, "ahu_wide", False)),
                "corroborated": bool(_attr(d, "corroborated", False)),
                "causes": "; ".join(_attr(d, "causes", []) or []),
                "n_caveats": len(_attr(d, "caveats", []) or []),
                "summary": _attr(d, "summary", ""),
            }
        )
    df = pd.DataFrame(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    elif not df.empty:
        df = df.reindex(columns=[c for c in _AHU_DIAG_COLS if c in df.columns])
    return df


def export_ahu_diagnoses(
    diagnoses, path: str, *, format: str | None = None, site: str = "", columns=None
) -> int:
    """Write per-AHU air-side drift diagnoses to ``path`` (CSV / JSON / Parquet); return count."""
    df = ahu_diagnoses_to_frame(diagnoses, site=site, columns=columns)
    return _write_frame(df, path, format)


def _write_frame(df: pd.DataFrame, path: str, format: str | None) -> int:
    fmt = (format or path.rsplit(".", 1)[-1]).lower()
    if fmt == "csv":
        df.to_csv(path, index=False)
    elif fmt == "json":
        df.to_json(path, orient="records", indent=2)
    elif fmt in ("parquet", "pq"):
        df.to_parquet(path, index=False)
    else:
        raise ValueError(f"unsupported export format {fmt!r}; use csv, json, or parquet")
    return len(df)
