"""A versioned, engine-labelled findings document (draft for camber#22 item 3; provisional).

This is the JSON a separate process (an open-fdd agent or pipeline, a scheduler) reads back after
calling CAMBER: ``camber interop openfdd findings CONFIG --out FILE``. Every record names the
engine and version that produced it; nothing is merged with another engine's verdicts. Fields
CAMBER does not produce yet (cost, confidence, review, root-cause group) are present and ``null``
so a reader can tell "not provided" from "zero". See docs/INTEROP-OPENFDD.md for the field list.
"""

from __future__ import annotations

import datetime as _dt

from ... import __version__

FINDINGS_SCHEMA = "findings-exchange"
FINDINGS_SCHEMA_VERSION = "0.1-draft"
ENGINE_NAME = "camber"
STATUSES = ("fault", "warn", "ok", "info", "declined", "not_evaluated")

# CAMBER metric keys that already mean what the exchange's magnitude fields mean (exact names only)
_HOURS_KEYS = ("fault_hours",)
_PCT_KEYS = ("fault_pct",)
_DENOM_KEYS = ("denominator_definition", "denominator")


def _first(metrics: dict, keys: tuple):
    for k in keys:
        if metrics.get(k) is not None:
            return metrics[k]
    return None


def _status(severity: str, metrics: dict) -> str:
    if metrics.get("declined"):
        return "declined"
    return severity if severity in STATUSES else "info"


def _kind(f) -> str:
    rule = str(getattr(f, "rule", None) or (f.get("rule") if isinstance(f, dict) else "") or "")
    return "mv" if rule.startswith("mv_") else "rule"


def _record(f, *, engine: dict, facility_id, window: dict, kind: str) -> dict:
    d = f.as_dict() if hasattr(f, "as_dict") else dict(f)
    metrics = d.get("metrics") or {}
    status = _status(str(d.get("severity", "")), metrics)
    reason = None
    if status == "declined":
        reason = metrics.get("declined_reason") or metrics.get("reason") or d.get("summary")
    evidence = d.get("evidence")
    return {
        "engine": engine,
        "kind": kind,
        "rule_id": d.get("rule"),
        "equip": d.get("equip"),
        "facility": facility_id,
        "window": window,
        "status": status,
        "declined_reason": reason,
        "magnitude": {
            "fault_hours": _first(metrics, _HOURS_KEYS),
            "fault_pct": _first(metrics, _PCT_KEYS),
            "denominator_definition": _first(metrics, _DENOM_KEYS),
        },
        "summary": d.get("summary", ""),
        "evidence": [evidence] if evidence else [],
        "caveats": list(d.get("caveats") or []),
        "root_cause_group": None,
        "cost": None,
        "confidence": None,
        "review": None,
        "native": {"severity": d.get("severity"), "metrics": metrics},
    }


def _skip_record(s, *, engine: dict, facility_id, window: dict) -> dict:
    d = s.as_dict() if hasattr(s, "as_dict") else dict(s)
    missing = list(d.get("missing") or [])
    why = d.get("reason") or "missing_inputs"
    text = f"{why}: {', '.join(missing)}" if missing else why
    return {
        "engine": engine,
        "kind": "rule",
        "rule_id": d.get("rule"),
        "equip": d.get("equip") or None,
        "facility": facility_id,
        "window": window,
        "status": "not_evaluated",
        "declined_reason": text,
        "magnitude": {"fault_hours": None, "fault_pct": None, "denominator_definition": None},
        "summary": "",
        "evidence": [],
        "caveats": [],
        "root_cause_group": None,
        "cost": None,
        "confidence": None,
        "review": None,
        "native": {"equip_class": d.get("equip_class"), "missing": missing, "reason": why},
    }


def findings_document(
    findings=(),
    *,
    facility_id: str | None,
    mv_findings=(),
    skipped=(),
    window: dict | None = None,
    sources: list | None = None,
) -> dict:
    """Build the engine-labelled findings document (schema ``findings-exchange`` 0.1-draft).

    ``findings`` are rule Findings (``camber run``), ``mv_findings`` the M&V Findings
    (``camber mv run``) and ``skipped`` the :class:`~camber.rules.base.RuleSkip` records (rules
    that applied but could not be evaluated). ``window`` is ``{"start", "end", "tz"}``.
    ``sources`` lists the data provenance (e.g. the open-fdd package hashes).
    """
    engine = {"name": ENGINE_NAME, "version": __version__}
    win = dict(window or {"start": None, "end": None, "tz": None})
    recs = [
        _record(f, engine=engine, facility_id=facility_id, window=win, kind=_kind(f))
        for f in findings
    ]
    recs += [
        _record(f, engine=engine, facility_id=facility_id, window=win, kind="mv")
        for f in mv_findings
    ]
    recs += [_skip_record(s, engine=engine, facility_id=facility_id, window=win) for s in skipped]
    counts: dict = {s: 0 for s in STATUSES}
    for r in recs:
        counts[r["status"]] += 1
    return {
        "schema": FINDINGS_SCHEMA,
        "schema_version": FINDINGS_SCHEMA_VERSION,
        "engine": engine,
        "facility": facility_id,
        "window": win,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": list(sources or []),
        "counts": counts,
        "findings": recs,
    }


def _window(cfg: dict, meta: dict) -> dict:
    """The analysis window: the config's ``source.start`` / ``end``, else the ingested span."""
    src = cfg.get("source") or {}
    span = (meta.get("openfdd") or {}).get("span") if isinstance(meta, dict) else None
    start = src.get("start") or (span[0] if span else None)
    end = src.get("end") or (span[1] if span else None)
    tz = src.get("timezone") or ((meta.get("openfdd") or {}).get("timezone") if meta else None)
    return {"start": start, "end": end, "tz": tz}


def run_findings(config_path) -> dict:
    """Run a config (rules, M&V and drift sections alike) and return its findings document.

    The process-boundary entry point behind ``camber interop openfdd findings``: one call, one
    JSON document, CAMBER read-only toward everything but its own state.
    """
    import os

    from ...config import load_config, run_config
    from ...store import FacilityRegistry

    cfg = load_config(os.fspath(config_path))
    base = os.path.dirname(os.path.abspath(os.fspath(config_path)))
    res = run_config(cfg, base_dir=base)
    src = cfg.get("source") or {}
    meta: dict = {}
    sources: list = []
    if src.get("kind") == "store" and src.get("store") and res.facility_id:
        store = src["store"] if os.path.isabs(src["store"]) else os.path.join(base, src["store"])
        meta = FacilityRegistry(store).get(res.facility_id)
        block = meta.get("openfdd") if isinstance(meta, dict) else None
        if isinstance(block, dict):
            sources.append(
                {
                    k: block.get(k)
                    for k in (
                        "source",
                        "building_id",
                        "schema_version",
                        "package_sha256",
                        "content_hash",
                        "crosswalk_version",
                        "timezone",
                        "unit_system",
                    )
                }
            )
    return findings_document(
        res.findings,
        facility_id=res.facility_id,
        skipped=res.rules_skipped,
        window=_window(cfg, meta),
        sources=sources,
    )
