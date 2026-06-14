"""Findings → tabular export for BI / data-warehouse ingestion.

Turns a run's :class:`~camber.rules.base.Finding` results into a flat table and writes it as
CSV, JSON, or Parquet — the shape a BI tool or warehouse loader expects. Metrics are flattened
into ``metric_*`` columns by default so each finding is one row with scalar columns. Uses
pandas + (for Parquet) the already-required pyarrow; no new dependency.
"""

from __future__ import annotations

import pandas as pd

from .tickets import _attr, fingerprint

_BASE_COLS = ["fingerprint", "site", "equip", "rule", "severity", "summary"]


def findings_to_frame(findings, *, site: str = "", flatten_metrics: bool = True,
                      columns=None) -> pd.DataFrame:
    """Flatten findings into a DataFrame (one row per finding).

    Base columns: fingerprint, site, equip, rule, severity, summary. When ``flatten_metrics``,
    each finding's ``metrics`` become ``metric_<key>`` columns (scalar values only). ``columns``
    restricts/orders the output columns.
    """
    rows = []
    for f in findings:
        equip = _attr(f, "equip", "")
        rule = _attr(f, "rule", "")
        row = {"fingerprint": fingerprint(site, equip, rule), "site": site, "equip": equip,
               "rule": rule, "severity": _attr(f, "severity", "info"),
               "summary": _attr(f, "summary", "")}
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


def export_findings(findings, path: str, *, format: str | None = None, site: str = "",
                    flatten_metrics: bool = True, columns=None) -> int:
    """Write findings to ``path`` as CSV / JSON / Parquet. Returns the row count.

    ``format`` is inferred from the file extension when None (``.csv`` / ``.json`` /
    ``.parquet``). JSON is written as records (a list of row objects).
    """
    df = findings_to_frame(findings, site=site, flatten_metrics=flatten_metrics, columns=columns)
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
