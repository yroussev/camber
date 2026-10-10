"""Retention: the policy (a documented, validated dict) and ``camber retention apply``.

**Policy.** Each data class has one rule. Precedence is **legal hold > facility override >
portfolio default**; the audit log is never deleted and cannot be overridden. The rule vocabulary:

=====================  ================================================================
``keep_months: N``     keep at least N months (a partition goes once all of it is older)
``keep_years: N``      keep at least N years
``keep``               ``"indefinite"`` / ``"forever"`` (never by age), ``"equipment_life"``
                       (a baseline lives as long as its facility), ``"legal_hold"``
                       (effective rules only)
``keep_versions: N``   baselines: the live version plus the N-1 most recent superseded ones
                       (``"all"``: every version)
``keep_last: N``       reports: the N most recent per facility
=====================  ================================================================

:data:`RETENTION_SCHEMA` is the JSON Schema of the policy document :func:`policy_document`
returns (``camber retention show --json``). Edge / cloud tooling reads that document to emit
bucket lifecycle rules: each class carries its storage ``location`` pattern and a conservative
``min_age_days`` (``None`` = never expire by age), and held facilities are listed so their
prefixes are excluded.

**Apply** (:func:`plan` / :func:`apply`): per facility, not under a hold,

1. raw month partitions older than ``raw_trends`` are **rolled up** to ``rollups/hourly`` and
   ``rollups/daily`` (mean and count per bucket, each partition replaced crash-safely), the
   rollups are **verified** (read back; their counts must add up to the raw row count), and only
   then is the raw partition **pruned**;
2. hourly month partitions older than ``hourly_rollups`` are verified against (or first rolled up
   into) the daily rollup, then pruned; daily partitions go only if an override gives them an age;
3. resolved / suppressed faults older than ``findings``, drift baseline history beyond
   ``keep_versions`` (M&V baselines keep every version by default), reports beyond
   ``keep_last`` and, if a rule is set, old weather-audit lines are removed;
4. offboarding facilities whose grace period has ended are archived.

A dry run (the default) returns the plan. Applied, it takes the lock, first recovers any
interrupted work, writes one ``retention.apply`` audit record per facility *before* acting (the
plan it is carrying out), and a ``retention.incomplete`` record if a verification refused a
prune. Every step is idempotent, so re-running (from cron) finishes an interrupted run and is
otherwise a no-op.
"""

from __future__ import annotations

import copy
import json
import os

import pandas as pd

from .._statefile import read_json, write_json
from ..store import _swap
from ._bundle import ROLLUPS_DIR
from ._state import (
    BASELINES_FILE,
    FAULTS_FILE,
    MANIFEST_FILE,
    MV_BASELINES_FILE,
    read_manifest,
    refresh_manifest,
    sha256_file,
    state_dir,
)

POLICY_SCHEMA_ID = "camber.retention/1"
ROLLUP_FREQS = {"hourly": "h", "daily": "D"}
WEATHER_AUDIT_FILE = "weather_audit.ndjson"  # camber.weather_privacy.AUDIT_FILE
_AGE_KEYS = ("keep_months", "keep_years")
_KEEP_WORDS = ("indefinite", "forever", "equipment_life", "legal_hold")

#: Per data class: what it covers, where it lives, and which rule keys it accepts.
CLASSES: dict = {
    "raw_trends": {
        "description": "raw trend rows in the store (month partitions)",
        "location": "store/facility_id={facility_id}/year={year}/month={month}/",
        "keys": ("keep_months", "keep_years", "keep"),
    },
    "hourly_rollups": {
        "description": "hourly mean + count rollups, written before raw rows are pruned",
        "location": "rollups/hourly/facility_id={facility_id}/year={year}/month={month}/",
        "keys": ("keep_months", "keep_years", "keep"),
    },
    "daily_rollups": {
        "description": "daily mean + count rollups",
        "location": "rollups/daily/facility_id={facility_id}/year={year}/month={month}/",
        "keys": ("keep_months", "keep_years", "keep"),
    },
    "findings": {
        "description": "fault history: resolved or suppressed faults, by last activity",
        "location": "state/{facility_id}/faults.json",
        "keys": ("keep_months", "keep_years", "keep"),
    },
    "drift_baselines": {
        "description": "frozen drift baselines: live for the equipment's life, history trimmed",
        "location": "state/{facility_id}/baselines.json",
        "keys": ("keep", "keep_versions"),
    },
    "mv_baselines": {
        "description": "versioned M&V baselines (interval and bill-based)",
        "location": "state/{facility_id}/mv_baselines.json",
        "keys": ("keep", "keep_versions"),
    },
    "reports": {
        "description": "reports and outputs listed in the facility's manifest (regenerable)",
        "location": "state/{facility_id}/reports/ and external paths in its manifest",
        "keys": ("keep_last", "keep"),
    },
    "weather_audit": {
        "description": "log of requests to weather and price services (privacy record)",
        "location": "state/{facility_id}/weather_audit.ndjson",
        "keys": ("keep_months", "keep_years", "keep"),
    },
    "audit": {
        "description": "the portfolio audit log; never deleted",
        "location": "_audit.ndjson",
        "keys": ("keep",),
    },
}

_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keep_months": {"type": "integer", "minimum": 1},
        "keep_years": {"type": "integer", "minimum": 1},
        "keep": {"enum": list(_KEEP_WORDS)},
        "keep_versions": {"oneOf": [{"type": "integer", "minimum": 1}, {"const": "all"}]},
        "keep_last": {"type": "integer", "minimum": 1},
        "source": {"enum": ["legal_hold", "facility", "default"]},
        "min_age_days": {"type": ["integer", "null"], "minimum": 1},
    },
}

#: JSON Schema (draft 2020-12) of the document :func:`policy_document` returns.
RETENTION_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": POLICY_SCHEMA_ID,
    "title": "CAMBER retention policy",
    "type": "object",
    "required": ["schema", "precedence", "classes", "defaults", "facilities", "legal_holds"],
    "properties": {
        "schema": {"const": POLICY_SCHEMA_ID},
        "generated_at": {"type": "string"},
        "precedence": {"const": ["legal_hold", "facility", "default"]},
        "classes": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["description", "location"],
                "properties": {
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "defaults": {"type": "object", "additionalProperties": {"$ref": "#/$defs/rule"}},
        "facilities": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "legal_hold": {"type": "boolean"},
                    "rules": {"type": "object", "additionalProperties": {"$ref": "#/$defs/rule"}},
                },
            },
        },
        "legal_holds": {"type": "array", "items": {"type": "string"}},
    },
    "$defs": {"rule": _RULE_SCHEMA},
}


# --------------------------------------------------------------------------- rules


def parse_rule_args(pairs) -> dict:
    """``["keep_months=36", "keep=forever"]`` -> ``{"keep_months": 36, "keep": "forever"}``."""
    out: dict = {}
    for p in pairs or ():
        if "=" not in str(p):
            raise ValueError(f"expected KEY=VALUE, got {p!r}")
        k, v = str(p).split("=", 1)
        k, v = k.strip(), v.strip()
        out[k] = int(v) if v.lstrip("-").isdigit() else v
    return out


def check_class(cls: str) -> None:
    """``ValueError`` unless ``cls`` is a data class whose rule may be changed."""
    if cls not in CLASSES:
        raise ValueError(f"unknown data class {cls!r} (known: {', '.join(CLASSES)})")
    if cls == "audit":
        raise ValueError("the audit log is never deleted; its rule cannot be changed")


def validate_rule(cls: str, rule: dict) -> dict:
    """The normalized rule for data class ``cls``; ``ValueError`` when it is not valid."""
    check_class(cls)
    rule = dict(rule)
    allowed = CLASSES[cls]["keys"]
    bad = sorted(k for k in rule if k not in allowed)
    if bad:
        raise ValueError(f"{cls} does not take {', '.join(bad)} (it takes {', '.join(allowed)})")
    for k in ("keep_months", "keep_years", "keep_last"):
        if k in rule and (not isinstance(rule[k], int) or isinstance(rule[k], bool) or rule[k] < 1):
            raise ValueError(f"{cls}.{k} must be a whole number >= 1")
    if "keep_versions" in rule:
        v = rule["keep_versions"]
        if v != "all" and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
            raise ValueError(f"{cls}.keep_versions must be a whole number >= 1 or 'all'")
    words = _KEEP_WORDS[:3] if cls in ("drift_baselines", "mv_baselines") else _KEEP_WORDS[:2]
    if "keep" in rule and rule["keep"] not in words:
        raise ValueError(f"{cls}.keep must be one of {', '.join(words)}")
    ages = [k for k in _AGE_KEYS if k in rule]
    if len(ages) + ("keep" in rule and cls not in ("drift_baselines", "mv_baselines")) > 1:
        raise ValueError(f"{cls}: give one of {', '.join(_AGE_KEYS)} or keep, not several")
    if cls in ("drift_baselines", "mv_baselines"):
        rule.setdefault("keep", "equipment_life" if cls == "drift_baselines" else "indefinite")
        rule.setdefault("keep_versions", 10 if cls == "drift_baselines" else "all")
    elif not rule:
        raise ValueError(f"{cls}: the rule is empty")
    return rule


def merge_rule(cls: str, current: dict, change: dict) -> dict:
    """``change`` applied to ``current``: an age key replaces the other age keys and ``keep``."""
    out = dict(current or {})
    if any(k in change for k in (*_AGE_KEYS, "keep", "keep_last")) and cls not in (
        "drift_baselines",
        "mv_baselines",
    ):
        for k in (*_AGE_KEYS, "keep", "keep_last"):
            out.pop(k, None)
    out.update(change)
    return validate_rule(cls, out)


def min_age_days(rule: dict):
    """A conservative object age (days) past which data under ``rule`` may expire, else ``None``.

    Months count as 31 days and years as 366, so an age-based bucket rule never expires anything
    the month-exact ``camber retention apply`` would still keep.
    """
    if "keep_months" in rule:
        return int(rule["keep_months"]) * 31
    if "keep_years" in rule:
        return int(rule["keep_years"]) * 366
    return None


def _cutoff(rule: dict, now: pd.Timestamp):
    """The instant before which data under ``rule`` may go (naive UTC), else ``None``."""
    if "keep_months" in rule:
        return now - pd.DateOffset(months=int(rule["keep_months"]))
    if "keep_years" in rule:
        return now - pd.DateOffset(years=int(rule["keep_years"]))
    return None


def _now(now) -> pd.Timestamp:
    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _month_end(year: int, month) -> pd.Timestamp:
    if month is None:  # a legacy year-only partition ends with its year
        return pd.Timestamp(year=int(year) + 1, month=1, day=1)
    return pd.Timestamp(year=int(year), month=int(month), day=1) + pd.DateOffset(months=1)


# --------------------------------------------------------------------------- policy document


def policy_document(pf, *, now=None) -> dict:
    """The retention policy as one JSON-ready document (see :data:`RETENTION_SCHEMA`)."""
    defaults = {c: {**r, "min_age_days": min_age_days(r)} for c, r in pf.policy().items()}
    holds = sorted(pf.legal_holds())
    facilities = {}
    for fid, e in pf.facilities().items():
        rules = {}
        for c, eff in pf.effective_retention(fid).items():
            r = dict(eff["rule"])
            rules[c] = {**r, "source": eff["source"], "min_age_days": min_age_days(r)}
        facilities[fid] = {
            "state": e.get("state", "active"),
            "legal_hold": fid in holds,
            "rules": rules,
        }
    return {
        "schema": POLICY_SCHEMA_ID,
        "generated_at": _now(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "precedence": ["legal_hold", "facility", "default"],
        "classes": {c: {**copy.deepcopy(v), "keys": list(v["keys"])} for c, v in CLASSES.items()},
        "defaults": defaults,
        "facilities": facilities,
        "legal_holds": holds,
    }


# --------------------------------------------------------------------------- rollups


def _rollup_store(pf, freq: str):
    from ..store import ParquetStore

    return ParquetStore(os.path.join(pf.root, ROLLUPS_DIR, freq))


def _part_path(root: str, fid: str, year: int, month: int) -> str:
    return os.path.join(root, f"facility_id={fid}", f"year={int(year)}", f"month={int(month)}")


def _read_dir(path: str) -> pd.DataFrame:
    import pyarrow.dataset as ds

    if not os.path.isdir(path):
        return pd.DataFrame()
    return ds.dataset(path, format="parquet").to_table().to_pandas()


def _read_legacy(path: str) -> pd.DataFrame:
    """The legacy part files directly under a year directory."""
    from ..store.parquet_store import _read_part_file

    files = sorted(f for f in os.listdir(path) if f.endswith(".parquet"))
    frames = [_read_part_file(os.path.join(path, f)).to_pandas() for f in files]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _rollup(df: pd.DataFrame, freq: str, *, weighted: bool = False) -> pd.DataFrame:
    """Mean and count per (equip, class, role, bucket); ``weighted`` rolls a rollup up further."""
    cols = ["ts", "equip", "equip_class", "role", "value", "n"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    d = df.copy()
    d["ts"] = pd.to_datetime(d["ts"]).dt.floor(ROLLUP_FREQS[freq])
    if "equip_class" not in d:
        d["equip_class"] = ""
    d["equip_class"] = d["equip_class"].fillna("").astype(str)
    keys = ["equip", "equip_class", "role", "ts"]
    if weighted:
        d["_w"] = d["value"] * d["n"]
        g = d.groupby(keys, sort=True)[["_w", "n"]].sum().reset_index()
        g["value"] = g["_w"] / g["n"]
        g = g.drop(columns=["_w"])
    else:
        d = d.dropna(subset=["value"])
        g = d.groupby(keys, sort=True)["value"].agg(["mean", "count"]).reset_index()
        g = g.rename(columns={"mean": "value", "count": "n"})
    g["n"] = g["n"].astype("int64")
    g["value"] = g["value"].astype("float64")
    return g[cols].sort_values(["ts", "equip", "role"]).reset_index(drop=True)


_COVERS_KEY = b"camber.rollup.covers"


def _covers_of(path: str):
    """The raw files a rollup part was built from (its Parquet metadata), else ``None``."""
    import pyarrow.parquet as pq

    try:
        meta = pq.read_schema(path).metadata or {}
        raw = meta.get(_COVERS_KEY)
        return set(json.loads(raw)) if raw is not None else None
    except (OSError, ValueError):  # pragma: no cover - an unreadable part is replaced
        return None


def _write_rollup(
    root: str, fid: str, year: int, month: int, frame: pd.DataFrame, *, covers=None
) -> dict:
    """Write one rollup month partition (crash-safe) and verify the new part by reading it back.

    ``covers`` names the raw files (relative to the facility's store partition) the frame was
    built from. The part is named by them and records them, and the partition keeps every other
    part built from raw files that are *not* all among them -- rows whose raw data was pruned
    after an earlier rollup -- so a late upload into an already rolled-up month adds to its
    rollup instead of replacing it, and re-running over the same raw files replaces its own part
    (idempotent). ``covers=None`` replaces the whole partition (a rollup derived from a complete
    source, such as daily from hourly).
    """
    import hashlib

    import pyarrow as pa

    from ..store.parquet_store import _canonicalize, _read_part_file, _write_part

    target = _part_path(root, fid, year, month)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    stage = _swap.staging(target)
    name = "part-0-0.parquet"
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if covers is not None:
        cset = sorted(set(covers))
        name = f"part-{hashlib.sha256(chr(10).join(cset).encode()).hexdigest()[:16]}.parquet"
        table = table.replace_schema_metadata(
            {**(table.schema.metadata or {}), _COVERS_KEY: json.dumps(cset).encode()}
        )
        if os.path.isdir(target):
            for n in sorted(os.listdir(target)):
                src = os.path.join(target, n)
                if not n.endswith(".parquet") or n == name:
                    continue
                old = _covers_of(src)
                if old is not None and not old <= set(cset):  # history of pruned raw rows
                    try:
                        os.link(src, os.path.join(stage, n))
                    except OSError:  # pragma: no cover - no hard links on this filesystem
                        import shutil

                        shutil.copy2(src, os.path.join(stage, n))
    # the canonical part layout (#130): sorted, fixed writer options, no pandas metadata
    _write_part(_canonicalize(table, keep_meta=(_COVERS_KEY,)), os.path.join(stage, name))
    _swap.commit(target)
    back = _read_part_file(os.path.join(target, name)).to_pandas()
    return {"rows": int(len(back)), "n": int(back["n"].sum()) if len(back) else 0}


def _raw_count(df: pd.DataFrame) -> int:
    return int(df["value"].notna().sum()) if len(df) else 0


# --------------------------------------------------------------------------- planning


def _facility_ids(pf) -> list:
    """Every facility with anything retention could touch (tombstoned ids excluded)."""
    tomb = pf.registry.tombstones()
    ids = set(pf.facilities())
    for freq in ROLLUP_FREQS:
        r = os.path.join(pf.root, ROLLUPS_DIR, freq)
        if os.path.isdir(r):
            ids |= {d.split("=", 1)[1] for d in os.listdir(r) if d.startswith("facility_id=")}
    sroot = os.path.join(pf.root, "state")
    if os.path.isdir(sroot):
        ids |= {d for d in os.listdir(sroot) if not d.startswith(("_", "."))}
    return sorted(i for i in ids if i not in tomb)


def _faults_expired(path: str, cutoff) -> list:
    """Fingerprints of resolved/suppressed faults whose last activity is before ``cutoff``."""
    if cutoff is None or not os.path.isfile(path):
        return []
    data = read_json(path)
    if "camber_redirect" in data:
        return []
    out = []
    for r in data.get("faults") or []:
        if r.get("status") not in ("resolved", "suppressed"):
            continue
        seen = [
            pd.to_datetime(r.get(k), utc=True, errors="coerce")
            for k in ("resolved_at", "last_seen")
        ]
        seen = [t.tz_localize(None) for t in seen if t is not pd.NaT and not pd.isna(t)]
        if seen and max(seen) < cutoff:
            out.append(r.get("fingerprint"))
    return out


def _versions_trimmed(path: str, list_key: str, keep_versions) -> int:
    """How many superseded versions ``keep_versions`` would drop from a baseline store."""
    if keep_versions == "all" or not os.path.isfile(path):
        return 0
    data = read_json(path)
    if "camber_redirect" in data:
        return 0
    keep_hist = max(0, int(keep_versions) - 1)
    return sum(max(0, len(r.get("history") or []) - keep_hist) for r in data.get(list_key) or [])


def _reports_expired(pf, fid: str, keep_last) -> list:
    """Report artifacts past the newest ``keep_last``: ``[{path, where, sha_ok}]``."""
    if not isinstance(keep_last, int):
        return []
    man = read_manifest(pf.root, fid)
    sdir = state_dir(pf.root, fid)
    items = []
    for rel, e in (man.get("files") or {}).items():
        p = os.path.join(sdir, *rel.split("/"))
        if (e or {}).get("kind") == "report" and os.path.isfile(p):
            items.append((str((e or {}).get("updated_at") or ""), p, "state", e))
    for p, e in (man.get("external") or {}).items():
        if (e or {}).get("kind") == "report" and os.path.isfile(p):
            items.append((str((e or {}).get("updated_at") or ""), p, "external", e))
    items.sort(key=lambda t: (t[0], os.path.getmtime(t[1])), reverse=True)
    out = []
    for _ts, p, where, e in items[keep_last:]:
        ok = where == "state" or sha256_file(p) == (e or {}).get("sha256")
        out.append({"path": p, "where": where, "unchanged": ok})
    return out


def _weather_expired(path: str, cutoff) -> int:
    if cutoff is None or not os.path.isfile(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                ts = pd.to_datetime(json.loads(line).get("ts"), utc=True, errors="coerce")
            except ValueError:
                continue
            if not pd.isna(ts) and ts.tz_localize(None) < cutoff:
                n += 1
    return n


def plan_facility(pf, fid: str, now) -> dict:
    """What :func:`apply` would do for one facility (reads footers and JSON, never data rows)."""
    now = _now(now)
    eff = {c: v["rule"] for c, v in pf.effective_retention(fid).items()}
    held = fid in pf.legal_holds()
    out: dict = {
        "facility_id": fid,
        "held": held,
        "raw": [],
        "hourly": [],
        "daily": [],
        "skipped_legacy": [],
        "findings": [],
        "drift_versions": 0,
        "mv_versions": 0,
        "reports": [],
        "weather_audit_lines": 0,
    }
    if held:
        return out
    cut = {c: _cutoff(r, now) for c, r in eff.items()}
    raw_cut = cut["raw_trends"]
    if raw_cut is not None:
        parts = pf.store.partitions(facility_id=fid)
        legacy_years = {p["year"] for p in parts if p["legacy"]}
        mixed = {p["year"] for p in parts if not p["legacy"] and p["year"] in legacy_years}
        for p in parts:
            end = _month_end(p["year"], p["month"])
            if p["year"] in mixed:
                # a year holding both layouts is rolled up only once migrated: a month's rows
                # would otherwise be split across two rollups that replace each other
                if p["legacy"] and pd.Timestamp(year=p["year"], month=1, day=1) < raw_cut:
                    out["skipped_legacy"].append({"year": p["year"], "rows": p["rows"]})
                continue
            if end > raw_cut:
                if p["legacy"] and pd.Timestamp(year=p["year"], month=1, day=1) < raw_cut:
                    out["skipped_legacy"].append({"year": p["year"], "rows": p["rows"]})
                continue
            out["raw"].append(
                {"year": p["year"], "month": p["month"], "rows": p["rows"], "legacy": p["legacy"]}
            )
    for freq, cls in (("hourly", "hourly_rollups"), ("daily", "daily_rollups")):
        c = cut[cls]
        if c is None:
            continue
        for p in _rollup_store(pf, freq).partitions(facility_id=fid):
            if p["month"] is not None and _month_end(p["year"], p["month"]) <= c:
                out[freq].append({"year": p["year"], "month": p["month"], "rows": p["rows"]})
    sdir = state_dir(pf.root, fid)
    out["findings"] = _faults_expired(os.path.join(sdir, FAULTS_FILE), cut["findings"])
    out["drift_versions"] = _versions_trimmed(
        os.path.join(sdir, BASELINES_FILE), "baselines", eff["drift_baselines"].get("keep_versions")
    )
    out["mv_versions"] = _versions_trimmed(
        os.path.join(sdir, MV_BASELINES_FILE),
        "mv_baselines",
        eff["mv_baselines"].get("keep_versions"),
    )
    out["reports"] = _reports_expired(pf, fid, eff["reports"].get("keep_last"))
    out["weather_audit_lines"] = _weather_expired(
        os.path.join(sdir, WEATHER_AUDIT_FILE), cut["weather_audit"]
    )
    return out


def _is_empty(fp: dict) -> bool:
    return not any(
        fp[k]
        for k in (
            "raw",
            "hourly",
            "daily",
            "findings",
            "drift_versions",
            "mv_versions",
            "reports",
            "weather_audit_lines",
        )
    )


def plan(pf, *, facility_id=None, now=None) -> dict:
    """The whole retention plan (a dry run): per facility, plus offboarding archives now due."""
    now = _now(now)
    fids = [facility_id] if facility_id else _facility_ids(pf)
    facs = {fid: plan_facility(pf, fid, now) for fid in fids}
    due = []
    from ._cascade import grace_until

    for fid in fids:
        try:
            e = pf.facility(fid)
        except KeyError:
            continue
        if e.get("state") != "offboarding" or fid in pf.legal_holds():
            continue
        until = grace_until(pf, fid)
        if until is not None and until.replace(tzinfo=None) <= now.to_pydatetime():
            due.append(fid)
    return {
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": True,
        "facilities": facs,
        "held": sorted(f for f, p in facs.items() if p["held"]),
        "archive_due": due,
        "changes": sum(0 if _is_empty(p) else 1 for p in facs.values()) + len(due),
    }


# --------------------------------------------------------------------------- apply


def _counts(fp: dict) -> dict:
    return {
        "raw_partitions": len(fp["raw"]),
        "raw_rows": sum(p["rows"] for p in fp["raw"]),
        "hourly_partitions": len(fp["hourly"]),
        "daily_partitions": len(fp["daily"]),
        "findings": len(fp["findings"]),
        "drift_versions": fp["drift_versions"],
        "mv_versions": fp["mv_versions"],
        "reports": len([r for r in fp["reports"] if r["unchanged"]]),
        "weather_audit_lines": fp["weather_audit_lines"],
    }


def _apply_raw(pf, fid: str, p: dict, eff_cut: dict, problems: list) -> None:
    """Roll one expired raw partition up, verify, then prune it."""
    store = pf.store
    fdir = os.path.join(store.root, f"facility_id={fid}", f"year={p['year']}")
    src = fdir if p["legacy"] else os.path.join(fdir, f"month={p['month']}")
    snap = _snapshot(src, legacy=p["legacy"])
    rel = f"year={p['year']}" if p["legacy"] else f"year={p['year']}/month={p['month']}"
    covers = [f"{rel}/{name}" for name, _size, _mtime in snap]
    if p["legacy"]:
        raw = _read_legacy(fdir) if os.path.isdir(fdir) else pd.DataFrame()
    else:
        raw = _read_dir(src)
    if raw.empty:
        months = []
    else:
        ts = pd.to_datetime(raw["ts"])
        raw = raw.assign(_y=ts.dt.year, _m=ts.dt.month)
        months = sorted({(int(y), int(m)) for y, m in zip(raw["_y"], raw["_m"])})
    for y, m in months:
        chunk = raw[(raw["_y"] == y) & (raw["_m"] == m)]
        want = _raw_count(chunk)
        for freq, cls in (("hourly", "hourly_rollups"), ("daily", "daily_rollups")):
            c = eff_cut[cls]
            if c is not None and _month_end(y, m) <= c:
                continue  # this rollup would be expired at once: skip it
            root = os.path.join(pf.root, ROLLUPS_DIR, freq)
            got = _write_rollup(root, fid, y, m, _rollup(chunk, freq), covers=covers)
            if got["n"] != want:
                problems.append(
                    f"{fid} {y}-{m:02d}: {freq} rollup holds {got['n']} of {want} raw rows; "
                    "raw partition kept"
                )
                return
    if _snapshot(src, legacy=p["legacy"]) != snap:  # a write landed while we rolled it up
        problems.append(
            f"{fid} {p['year']}-{p['month'] or 'all'}: written to during the rollup; raw "
            "partition kept (the next run retries)"
        )
        return
    if p["legacy"]:
        for f in sorted(os.listdir(fdir)):
            if f.endswith(".parquet"):
                _swap.discard(os.path.join(fdir, f))
        store._invalidate_catalog()
        from ..resolve import clear_store_cache

        clear_store_cache(store.root, fid)
    else:
        store.drop_partition(fid, p["year"], p["month"])


def _snapshot(path: str, *, legacy: bool) -> tuple:
    """(name, size, mtime) of the part files directly in a partition directory (for a legacy
    year: its own files, not its month subdirectories)."""
    if not os.path.isdir(path):
        return ()
    out = []
    for n in sorted(os.listdir(path)):
        full = os.path.join(path, n)
        if n.endswith(".parquet") and os.path.isfile(full):
            st = os.stat(full)
            out.append((n, st.st_size, st.st_mtime_ns))
    return tuple(out)


def _apply_hourly(pf, fid: str, p: dict, daily_cut, problems: list) -> None:
    """Make sure the daily rollup covers an expired hourly partition, then prune it."""
    hroot = os.path.join(pf.root, ROLLUPS_DIR, "hourly")
    droot = os.path.join(pf.root, ROLLUPS_DIR, "daily")
    hourly = _read_dir(_part_path(hroot, fid, p["year"], p["month"]))
    want = int(hourly["n"].sum()) if len(hourly) else 0
    if daily_cut is None or _month_end(p["year"], p["month"]) > daily_cut:
        daily = _read_dir(_part_path(droot, fid, p["year"], p["month"]))
        have = int(daily["n"].sum()) if len(daily) else 0
        if have != want:
            got = _write_rollup(
                droot, fid, p["year"], p["month"], _rollup(hourly, "daily", weighted=True)
            )
            if got["n"] != want:
                problems.append(
                    f"{fid} {p['year']}-{p['month']:02d}: daily rollup holds {got['n']} of "
                    f"{want}; hourly partition kept"
                )
                return
    _rollup_store(pf, "hourly").drop_partition(fid, p["year"], p["month"])


def _trim_versions(path: str, list_key: str, keep_versions) -> int:
    if keep_versions == "all" or not os.path.isfile(path):
        return 0
    data = read_json(path)
    if "camber_redirect" in data:
        return 0
    keep_hist = max(0, int(keep_versions) - 1)
    dropped = 0
    for r in data.get(list_key) or []:
        hist = list(r.get("history") or [])
        if len(hist) > keep_hist:
            dropped += len(hist) - keep_hist
            r["history"] = hist[len(hist) - keep_hist :] if keep_hist else []
    if dropped:
        write_json(path, data)
    return dropped


def _apply_state(pf, fid: str, fp: dict, eff: dict, cut: dict) -> None:
    sdir = state_dir(pf.root, fid)
    if fp["findings"]:
        path = os.path.join(sdir, FAULTS_FILE)
        data = read_json(path)
        gone = set(_faults_expired(path, cut["findings"]))
        data["faults"] = [r for r in data.get("faults") or [] if r.get("fingerprint") not in gone]
        write_json(path, data)
    if fp["drift_versions"]:
        _trim_versions(
            os.path.join(sdir, BASELINES_FILE), "baselines", eff["drift_baselines"]["keep_versions"]
        )
    if fp["mv_versions"]:
        _trim_versions(
            os.path.join(sdir, MV_BASELINES_FILE),
            "mv_baselines",
            eff["mv_baselines"]["keep_versions"],
        )
    if fp["weather_audit_lines"]:
        path = os.path.join(sdir, WEATHER_AUDIT_FILE)
        keep = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ts = pd.to_datetime(json.loads(line).get("ts"), utc=True, errors="coerce")
                except ValueError:
                    keep.append(line)
                    continue
                if pd.isna(ts) or ts.tz_localize(None) >= cut["weather_audit"]:
                    keep.append(line)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    ext_gone = []
    for r in fp["reports"]:
        if r["unchanged"] and os.path.isfile(r["path"]):
            os.remove(r["path"])
            if r["where"] == "external":
                ext_gone.append(r["path"])
    if os.path.isdir(sdir) and (
        fp["findings"]
        or fp["drift_versions"]
        or fp["mv_versions"]
        or fp["reports"]
        or fp["weather_audit_lines"]
    ):
        if ext_gone:
            man = read_manifest(pf.root, fid)
            for p in ext_gone:
                (man.get("external") or {}).pop(p, None)
            write_json(os.path.join(sdir, MANIFEST_FILE), man)
        refresh_manifest(pf.root, fid)


def apply(pf, *, facility_id=None, now=None, reason: str) -> dict:
    """Carry the retention plan out under the lock (see the module docstring); returns it."""
    now = _now(now)
    with pf.lock():
        from ._cascade import recover

        recovered = recover(pf)
        result = plan(pf, facility_id=facility_id, now=now)
        result["dry_run"] = False
        result["recovered"] = recovered
        problems: list = []
        for fid, fp in result["facilities"].items():
            if fp["held"] or _is_empty(fp):
                continue
            eff = {c: v["rule"] for c, v in pf.effective_retention(fid).items()}
            cut = {c: _cutoff(r, now) for c, r in eff.items()}
            before = len(problems)
            pf._audit(
                "retention.apply",
                facility_id=fid,
                reason=reason,
                details={"now": result["now"], **_counts(fp)},
            )
            for p in fp["raw"]:
                _apply_raw(pf, fid, p, cut, problems)
            for p in fp["hourly"]:
                _apply_hourly(pf, fid, p, cut["daily_rollups"], problems)
            for p in fp["daily"]:
                _rollup_store(pf, "daily").drop_partition(fid, p["year"], p["month"])
            _apply_state(pf, fid, fp, eff, cut)
            if len(problems) > before:
                pf._audit(
                    "retention.incomplete",
                    facility_id=fid,
                    reason=reason,
                    details={"problems": problems[before:]},
                )
        archived = []
        for fid in result["archive_due"]:
            pf.archive(
                fid, reason=f"{reason} (offboarding grace period ended)", apply=True, now=now
            )
            archived.append(fid)
        result["archived"] = archived
        result["problems"] = problems
        return result
