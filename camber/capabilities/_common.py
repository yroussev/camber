"""Helpers the built-in capabilities share (0.110, #278): schema pieces, JSON-safe values, the
facility run config a run capability starts from, finding rows and evidence-mask summaries.

Private module -- not public API.
"""

from __future__ import annotations

import datetime as _dt
import enum
import math
import os
from functools import lru_cache

import numpy as np
import pandas as pd

from . import CapabilityError
from ._sanitize import defang, name

#: an ISO date or date-time (``2024-03-01``, ``2024-03-01T06:00``, ``2024-03-01 06:00:00``)
DATE = {
    "type": "string",
    "pattern": (
        r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
        r"([T ]([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?)?$"
    ),
    "description": "an ISO date or date-time in the site's wall clock (e.g. 2024-03-01)",
}
TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
FACILITY = {**TEXT, "description": "a facility_id from the facilities capability"}
EQUIPS = {
    "type": "array",
    "items": TEXT,
    "minItems": 1,
    "maxItems": 200,
    "description": "only these equipment ids (exact match; see the points capability)",
}
SEVERITIES = ("fault", "warn", "info", "ok")
SEVERITY = {"type": "string", "enum": list(SEVERITIES)}
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: a finding row in a result (rule_run, drift, mv_summary)
FINDING_ROW = {
    "type": "object",
    "required": ["finding_id", "rule", "equip", "severity", "summary", "caveats", "metrics"],
    "additionalProperties": False,
    "properties": {
        "finding_id": {"type": "string", "description": "pass to finding_detail"},
        "rule": {"type": "string"},
        "equip": {"type": "string", "description": "'<fleet>' / 'fleet' for a fleet finding"},
        "severity": SEVERITY,
        "summary": {"type": "string"},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "metrics": {"type": "object", "description": "the finding's scalar metrics"},
        "fact": {"type": "string", "description": "the fact id that cites this finding"},
    },
}


def jsonable(x, depth: int = 0):
    """``x`` as plain JSON data: numpy and pandas scalars unwrapped, NaN / inf as ``None``,
    timestamps as ISO strings, tuples and sets as lists, mapping keys as strings."""
    if depth > 12:
        return str(x)
    if isinstance(x, enum.Enum):  # a Role is a str Enum: its value, not "Role.OAT"
        return jsonable(x.value, depth + 1)
    if x is None or x is pd.NaT or isinstance(x, (bool, str)):
        return None if x is pd.NaT else x
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, (pd.Timestamp, _dt.datetime, _dt.date)):
        return x.isoformat()
    if isinstance(x, (pd.Timedelta, _dt.timedelta)):
        return str(x)
    if isinstance(x, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        items = sorted(x, key=str) if isinstance(x, (set, frozenset)) else x
        return [jsonable(v, depth + 1) for v in items]
    return str(x)


def scalars(d) -> dict:
    """The scalar (number, string, boolean, null) entries of ``d``, JSON-safe, sorted by key."""
    out: dict = {}
    for k in sorted(d or {}, key=str):
        v = jsonable(d[k])
        if v is None or isinstance(v, (bool, int, float, str)):
            out[str(k)] = v
    return out


def parse_date(name: str, value) -> pd.Timestamp | None:
    """``value`` (a :data:`DATE` string, or ``None``) as a timestamp; ``invalid_params`` naming
    ``name`` for a date the pattern lets through but the calendar has not (``2025-02-30``)."""
    if not value:
        return None
    try:
        return pd.Timestamp(value)
    except (ValueError, OverflowError):
        raise CapabilityError(
            "invalid_params", f"{name} {value!r} is not a valid date", data={"argument": name}
        ) from None


def window(arguments: dict) -> tuple:
    """``(start, end)`` from ``arguments`` (either may be ``None``); ``invalid_params`` for a
    date that does not exist or an end before the start."""
    start, end = arguments.get("start"), arguments.get("end")
    t0, t1 = parse_date("start", start), parse_date("end", end)
    if t0 is not None and t1 is not None and t1 < t0:
        raise CapabilityError("invalid_params", "end is before start")
    return start, end


# --------------------------------------------------------------------------- run configs

#: config sections a rule run keeps from the caller's run config (everything else -- drift, M&V,
#: report, fault fold -- belongs to another capability)
RULE_SECTIONS = (
    "site",
    "facility_id",
    "workspace",
    "source",
    "mapping",
    "shared_oat",
    "equipment",
    "rules",
    "soo",
    "topology",
    "ventilation",
    "trust_gate",
    "resample",
    "site_elevation_ft",
    "units",
    "include_inactive",
)


def config_facility(config: dict) -> str:
    """The facility a config is about, read the way :func:`run_config_for` checks it."""
    source = config.get("source") or {}
    if source.get("kind") == "store":
        return str(source.get("facility_id") or "")
    return str(config.get("facility_id") or config.get("site") or "")


def caller_config(ctx, facility_id: str):
    """``(config, base_dir)`` of the caller's run config when it is about ``facility_id``, else
    ``(None, None)``. Never raises for a missing or unreadable config: the capability then
    builds its own."""
    from ..config import load_config

    if not ctx.config or not os.path.isfile(ctx.config):
        return None, None
    try:
        config = load_config(ctx.config)
    except Exception:  # noqa: BLE001 - a broken caller config is ignored, not leaked
        return None, None
    if not isinstance(config, dict) or config_facility(config) != facility_id:
        return None, None
    return config, os.path.dirname(os.path.abspath(ctx.config))


def store_config(ctx, facility_id: str) -> dict:
    """A store-backed run config for one facility: every stored equipment class, and the
    facility's weather equipment (if any) as the shared outdoor-air temperature."""
    from ..model.equipclass import equip_family
    from ._policy import open_store

    store = open_store(ctx)
    if facility_id not in store.facilities():
        raise CapabilityError("not_found", f"facility {facility_id!r} has no stored data")
    classes = store.equipment(facility_id=facility_id).get(facility_id) or {}
    config: dict = {
        "site": store.facility_name(facility_id),
        "source": {
            "kind": "store",
            "store": os.path.abspath(ctx.store),
            "facility_id": facility_id,
        },
        "equipment": [{"class": c} for c in sorted(set(classes.values()))],
        "rules": [],
    }
    oat_roles = {p.equip for p in store.points(facility_id=facility_id) if p.role == "oat"}
    weather = sorted(
        e for e, c in classes.items() if equip_family(c) == "weather" and e in oat_roles
    )
    if weather:
        config["shared_oat"] = {"equip": weather[0], "role": "oat"}
    return config


@lru_cache(maxsize=1)
def rule_names() -> tuple:
    """Every built-in rule name, sorted."""
    from ..rules.builtin import rule_names as names

    return tuple(sorted(names()))


def with_rules(config: dict, rules, params) -> dict:
    """``config`` running ``rules`` (default: its own, else every built-in rule) with
    ``params`` merged into each rule's entry."""
    from ..rules.param_docs import rule_params

    config = dict(config)
    entries = {}
    for e in config.get("rules") or []:
        name = e if isinstance(e, str) else e.get("name")
        entries[name] = e
    names = list(rules) if rules else (list(entries) or list(rule_names()))
    out = []
    for name in dict.fromkeys(names):
        e = entries.get(name, name)
        e = {"name": e} if isinstance(e, str) else dict(e)
        out.append(e)
    by_name = {e["name"]: e for e in out}
    for rule in sorted(params or {}):
        if rule not in by_name:
            raise CapabilityError(
                "invalid_params", f"params name rule {rule!r}, which this run does not run"
            )
        try:
            known = {p.name for p in rule_params(rule)}
        except KeyError:
            known = set()
        bad = sorted(set(params[rule]) - known)
        if bad:
            raise CapabilityError(
                "invalid_params",
                f"rule {rule!r} has no tunable parameter(s) {', '.join(bad)} (see rule_params)",
            )
        by_name[rule]["params"] = {**(by_name[rule].get("params") or {}), **params[rule]}
    config["rules"] = [e if e.get("params") else e["name"] for e in out]
    return config


def facility_config(ctx, facility_id: str, *, keep=RULE_SECTIONS) -> tuple:
    """``(config, base_dir, from_caller)``: the caller's run config for ``facility_id`` (only the
    ``keep`` sections) when it has one, else :func:`store_config`."""
    config, base_dir = caller_config(ctx, facility_id)
    if config is not None:
        return {k: v for k, v in config.items() if k in keep}, base_dir, True
    return store_config(ctx, facility_id), os.path.dirname(os.path.abspath(ctx.store)), False


def narrow(config: dict, *, equips=None, start=None, end=None) -> dict:
    """``config`` limited to ``equips`` and the ``start`` / ``end`` window (store sources)."""
    config = dict(config)
    if equips:
        config["equipment"] = [
            {**dict(e), "equip": sorted(set(equips) & set(e.get("equip") or equips))}
            for e in config.get("equipment") or []
        ]
    if start or end:
        source = dict(config.get("source") or {})
        if source.get("kind") != "store":
            raise CapabilityError(
                "invalid_params",
                "a start / end window needs a store-backed facility; this facility's run config "
                "reads trend folders",
            )
        if start:
            source["start"] = start
        if end:
            source["end"] = end
        config["source"] = source
    return config


def run(ctx, config: dict, base_dir: str):
    """:func:`run_config_for`, with a config the run refuses (an unknown rule, a bad parameter,
    a class with no equipment) reported as ``invalid_params``."""
    from ._policy import run_config_for

    try:
        return run_config_for(ctx, config, base_dir=base_dir)
    except CapabilityError:
        raise
    except (KeyError, ValueError, TypeError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        raise CapabilityError("invalid_params", f"the run was refused: {msg}") from None


# --------------------------------------------------------------------------- findings


def finding_ids(findings) -> list:
    """A stable id per finding: ``<rule>@<equip>``, with ``#2``, ``#3`` ... on repeats."""
    seen: dict = {}
    out = []
    for f in findings:
        base = f"{getattr(f, 'rule', '')}@{getattr(f, 'equip', '')}"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return out


def finding_row(fid: str, f) -> dict:
    """A finding as a :data:`FINDING_ROW`."""
    sev = str(getattr(f, "severity", "") or "info")
    return {
        "finding_id": fid,
        "rule": str(getattr(f, "rule", "")),
        "equip": str(getattr(f, "equip", "")),
        "severity": sev if sev in _RANK else "info",
        "summary": " ".join(str(getattr(f, "summary", "") or "").split()),
        "caveats": [str(c) for c in getattr(f, "caveats", None) or []],
        "metrics": scalars(getattr(f, "metrics", None)),
    }


def by_severity(rows: list) -> list:
    """``rows`` worst first, then by equipment, rule and id (deterministic)."""
    return sorted(
        rows, key=lambda r: (_RANK.get(r["severity"], 9), r["equip"], r["rule"], r["finding_id"])
    )


def severity_counts(rows: list) -> dict:
    """``{"fault": n, "warn": n, "info": n, "ok": n}``."""
    out = dict.fromkeys(SEVERITIES, 0)
    for r in rows:
        out[r["severity"]] = out.get(r["severity"], 0) + 1
    return out


def trust_declines(findings) -> tuple:
    """``([(rule, equip, roles)], floor)``: the checks the trust floor declined, and the floor
    they declined under (0.111, #297).

    A check declines when one of its inputs (``roles``), or its drift reference's, scores below
    the trust floor; it then reports an ``info`` finding, so a fault count says nothing about it.
    """
    out, floor = [], None
    for f in findings or ():
        m = getattr(f, "metrics", None) or {}
        if m.get("declined") is not True:
            continue
        roles = [str(r) for r in m.get("untrusted_roles") or ()]
        if roles or m.get("reason") == "reference_untrusted":
            out.append((str(getattr(f, "rule", "")), str(getattr(f, "equip", "")), roles))
            if isinstance(m.get("min_trust"), (int, float)):
                floor = max(floor or 0.0, float(m["min_trust"]))
    return sorted(out), floor


def trust_banner(declines: list, floor) -> str | None:
    """The NOTICE for :func:`trust_declines` (None when nothing was declined): how many checks,
    which, and on what inputs -- a declined check is not a pass."""
    if not declines:
        return None
    per_rule: dict = {}
    for rule, _equip, _roles in declines:
        per_rule[rule] = per_rule.get(rule, 0) + 1
    which = ", ".join(f"{r} on {n} equipment" for r, n in sorted(per_rule.items()))
    roles = sorted({r for _rule, _equip, rs in declines for r in rs})
    inputs = f"; untrusted input(s): {', '.join(roles)}" if roles else ""
    under = f" {floor:g}" if floor is not None else ""
    return (
        f"{len(declines)} check(s) declined because an input scored below the trust floor"
        f"{under} ({which}{inputs}). The fault and warn counts do not cover them: a declined "
        "check is not a pass. sensor_trust shows why each input scored low."
    )


COUNTS = {
    "type": "object",
    "required": list(SEVERITIES),
    "additionalProperties": False,
    "properties": {s: {"type": "integer", "minimum": 0} for s in SEVERITIES},
}


def cite(fact: dict) -> str:
    """A summary sentence carrying ``fact``'s own text and its id, so a reader of the text alone
    has the cited values: ``"AHU-1" economizer_high_limit (fault): ... [F2@rule_run].`` A
    citation-shaped token inside the fact's text is written in parentheses (#291)."""
    return f"{defang(str(fact['text'])).rstrip(' .;')} [{fact['id']}]."


def finding_fact(fact_id: str, row: dict) -> dict:
    """The fact that cites one finding row; its text names the equipment as a quoted value
    (#291), also where the rule's summary starts with it (``AHU-1: ...``)."""
    equip, summary = row["equip"], row["summary"]
    flat = " ".join(equip.split())
    if flat and summary.startswith(f"{flat}:"):
        summary = name(equip) + summary[len(flat) :]
    return {
        "id": fact_id,
        "kind": "finding",
        "value": row["severity"],
        "rule": row["rule"],
        "equip": row["equip"],
        "finding_id": row["finding_id"],
        "text": f"{name(equip)} {row['rule']} ({row['severity']}): {summary}",
        "metrics": row["metrics"],
    }


# --------------------------------------------------------------------------- masks


def _step_hours(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    step = pd.Series(index).diff().dropna().median()
    return max(float(step / pd.Timedelta(hours=1)), 1e-9)


def mask_summary(mask, *, max_spans: int = 20) -> dict:
    """A boolean evidence mask as hours: ``{"hours", "samples", "share", "spans": [{"start",
    "end", "hours"}, ...]}`` -- the longest ``max_spans`` contiguous runs, in time order."""
    m = pd.Series(mask).fillna(False).astype(bool)
    if not isinstance(m.index, pd.DatetimeIndex):
        m.index = pd.DatetimeIndex(pd.to_datetime(m.index))
    m = m.sort_index()
    step = _step_hours(m.index)
    n_on = int(m.sum())
    spans: list = []
    if n_on:
        run_id = (m != m.shift()).cumsum()
        for _k, seg in m[m].groupby(run_id[m]):
            spans.append(
                {
                    "start": seg.index[0].isoformat(),
                    "end": seg.index[-1].isoformat(),
                    "hours": round(len(seg) * step, 2),
                }
            )
    longest = sorted(spans, key=lambda s: (-s["hours"], s["start"]))[:max_spans]
    return {
        "hours": round(n_on * step, 2),
        "samples": n_on,
        "share": round(n_on / len(m), 4) if len(m) else 0.0,
        "n_spans": len(spans),
        "spans": sorted(longest, key=lambda s: s["start"]),
    }


MASK: dict = {
    "type": "object",
    "required": ["hours", "samples", "share", "n_spans", "spans"],
    "additionalProperties": False,
    "properties": {
        "hours": {"type": "number", "minimum": 0},
        "samples": {"type": "integer", "minimum": 0},
        "share": {"type": "number", "minimum": 0, "maximum": 1},
        "n_spans": {"type": "integer", "minimum": 0},
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["start", "end", "hours"],
                "additionalProperties": False,
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "hours": {"type": "number", "minimum": 0},
                },
            },
        },
    },
}
