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
from ._sanitize import defang, name, plain, prose

#: an ISO date or date-time (``2024-03-01``, ``2024-03-01T06:00``, ``2024-03-01 06:00:00``)
DATE = {
    "type": "string",
    "pattern": (
        r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
        r"([T ]([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?)?$"
    ),
    "description": "ISO date or date-time, site wall clock",
}
TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
FACILITY = {**TEXT, "description": "a facility_id from facilities"}
EQUIPS = {
    "type": "array",
    "items": TEXT,
    "minItems": 1,
    "maxItems": 200,
    "description": "only these equipment ids (exact; see points)",
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
        "equip": {
            "type": "string",
            "description": "'<fleet>' for a fleet finding (one finding over many units; its "
            "metrics name them)",
        },
        "severity": SEVERITY,
        "summary": {"type": "string"},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "metrics": {"type": "object", "description": "the finding's scalar metrics"},
        "fact": {"type": "string", "description": "the fact id that cites this finding"},
        "conditional_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "present when the finding rests on an input whose configured sensor "
            "reference disagrees (report.rcx.references; sensor_trust shows the comparison): "
            "the sensor problems it is conditional on. Treat such a fault as a possible sensor "
            "problem first",
        },
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


# --------------------------------------------------------------------------- selections

#: stored names a not_found message lists (the error's data lists up to :data:`_AVAILABLE`)
_SHOWN = 10
_AVAILABLE = 50


def _listing(values, *, sent: bool = False) -> str:
    """Up to :data:`_SHOWN` of ``values`` as quoted spans, with how many more there are;
    ``sent``: values the caller sent, quoted but redacted as the tool's own words (#307)."""
    from ._sanitize import echo

    values = sorted(values)
    shown = ", ".join((echo if sent else name)(v) for v in values[:_SHOWN])
    more = len(values) - _SHOWN
    return f"{shown} and {more} more" if more > 0 else (shown or "none")


def _safe(values, *, sent: bool = False) -> list:
    """``values`` as error data: sorted, at most :data:`_AVAILABLE`, each one line and cut. A
    value from the data (not ``sent`` by the caller) is marked as a span, so the registry's
    redaction leaves its slashes alone (#307); the marks go before the error leaves it."""
    from ._sanitize import CLOSE, MAX_NAME, OPEN, clip, one_line

    out = [clip(defang(one_line(v)), MAX_NAME, key=v) for v in sorted(values)[:_AVAILABLE]]
    return out if sent else [f"{OPEN}{v}{CLOSE}" for v in out]


#: the ``equip`` of a fleet rule's finding (one finding over many equipment)
FLEET = "<fleet>"
#: :data:`FLEET` as the defanged text of 0.111 before #318 showed it; accepted as its alias
FLEET_SHOWN = "\u2039fleet\u203a"


def check_selection(store, facility_id: str, arguments: dict) -> None:
    """``not_found`` for an ``equips`` / ``equip`` id, a ``roles`` / ``role`` or an
    ``equip_class`` in ``arguments`` that ``facility_id`` does not store, naming what it does.

    0.111 (#296): a selection that matched nothing used to read as a clean answer ("0 faults on
    AHU-1"). Nothing is checked for a facility the store does not hold (a folder-backed config).
    """
    if facility_id not in store.facilities():
        return
    classes = store.equipment(facility_id=facility_id).get(facility_id) or {}
    roles = None
    for arg, what, plural in (
        ("equips", "equipment id", "equipment ids"),
        ("equip", "equipment id", "equipment ids"),
        ("roles", "point role", "point roles"),
        ("role", "point role", "point roles"),
        ("equip_class", "equipment class", "equipment classes"),
    ):
        want = arguments.get(arg)
        if want is None:
            continue
        want = [want] if isinstance(want, str) else list(want)
        if arg.startswith("role"):
            if roles is None:
                roles = {str(k.role) for k in store.points(facility_id=facility_id)}
            have = roles
        else:
            have = set(classes.values()) if arg == "equip_class" else set(classes)
        unknown = [w for w in dict.fromkeys(want) if w not in have]
        if unknown:
            # #304: a fleet finding's equip is no stored equipment; its metrics name the units
            fleet = (
                f"; {FLEET!r} is the equipment of a fleet finding (one finding over many units), "
                "not stored equipment: use the units its metrics name"
                if (FLEET in unknown or FLEET_SHOWN in unknown) and not arg.startswith("role")
                else ""
            )
            raise CapabilityError(
                "not_found",
                f"facility {facility_id!r} stores no {what} {_listing(unknown, sent=True)}; its "
                f"{plural} are {_listing(have)} (exact match; see the points capability){fleet}",
                data={
                    "argument": arg,
                    "unknown": _safe(unknown, sent=True),
                    "available": _safe(have),
                },
            )


def check_window(store, facility_id: str, start, end) -> None:
    """``not_found`` when the ``start`` / ``end`` window holds no stored sample of
    ``facility_id``, giving its first and last stored sample (0.111, #296). Nothing is checked
    for a facility the store does not hold, or one with no stored sample at all."""
    if (not start and not end) or facility_id not in store.facilities():
        return
    lo, hi = store._ts_span(facility_id)
    if lo is None:
        return

    def naive(t):
        t = pd.Timestamp(t)
        return t.tz_localize(None) if t.tzinfo is not None else t

    lo, hi = naive(lo), naive(hi)
    t0 = naive(start) if start else None
    t1 = naive(end) if end else None
    if (t1 is not None and t1 < lo) or (t0 is not None and t0 > hi):
        raise CapabilityError(
            "not_found",
            f"facility {facility_id!r} has no stored sample from {start or 'its first sample'} "
            f"to {end or 'its last sample'}: its data runs from {lo.isoformat()} to "
            f"{hi.isoformat()}",
            data={
                "argument": "start" if t0 is not None and t0 > hi else "end",
                "first": lo.isoformat(),
                "last": hi.isoformat(),
            },
        )


def check_run_selection(ctx, facility_id: str, arguments: dict, config: dict) -> None:
    """:func:`check_selection` and :func:`check_window` for a run capability, and
    ``not_found`` for an ``equips`` id the store holds but ``config`` runs no rule on (its class
    is not among the config's equipment, or the config lists other ids of that class)."""
    from ._policy import open_store

    store = open_store(ctx)
    check_selection(store, facility_id, arguments)
    start, end = arguments.get("start"), arguments.get("end")
    check_window(store, facility_id, start, end)
    equips = arguments.get("equips")
    source = config.get("source") or {}
    if not equips or source.get("kind") != "store" or facility_id not in store.facilities():
        return
    classes = store.equipment(facility_id=facility_id).get(facility_id) or {}
    covered = set()
    for e in config.get("equipment") or []:
        named = e.get("equip")
        covered |= {q for q, c in classes.items() if c == e.get("class")} & (
            set(named) if named else set(classes)
        )
    left = [q for q in dict.fromkeys(equips) if q not in covered]
    if left:
        kinds = sorted({str(e.get("class")) for e in config.get("equipment") or []})
        raise CapabilityError(
            "not_found",
            f"the run config for {facility_id!r} runs no rule on equipment {_listing(left)} "
            f"(it covers the equipment classes {_listing(kinds)})",
            data={"argument": "equips", "unknown": _safe(left), "available": _safe(covered)},
        )


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
    from ..config import _config_facility

    return _config_facility(config)


def caller_config(ctx, facility_id: str) -> tuple:
    """``(config, base_dir, notice)``: the caller's run config about ``facility_id`` (``notice``
    ``None``), else the packaged config template of the catalog dataset the facility was
    ingested from (``notice`` the banner saying so, see :func:`dataset_template`), else
    ``(None, None, None)``.

    ``camber mcp`` checks every ``--config`` at startup (0.111, #296) and its calls read the
    configs as loaded then (#308), so an edit to the file while the server runs changes nothing.
    For another caller a config that cannot be read is skipped, never leaked."""
    from ._policy import config_paths, read_config

    for path in config_paths(ctx):
        try:
            config = read_config(ctx, path)  # #308: camber mcp's startup snapshot
        except Exception:  # noqa: BLE001 - a config that does not load is skipped, not leaked
            continue
        if isinstance(config, dict) and config_facility(config) == facility_id:
            return config, os.path.dirname(os.path.abspath(path)), None
    config, dataset_id = dataset_template(ctx, facility_id)
    if config is None:
        return None, None, None
    notice = (
        f"No run config names this facility, so this run uses the config template packaged for "
        f"catalog dataset {name(dataset_id)} (the one `camber datasets config` writes): its "
        "equipment classes, rules and parameters, not a config tuned for your own use of the data."
    )
    return config, os.path.dirname(os.path.abspath(ctx.store)), notice


def dataset_template(ctx, facility_id: str) -> tuple:
    """``(config, dataset_id)``: the in-package config template of the catalog dataset
    ``facility_id`` was ingested from, pointed at ``ctx.store``; ``(None, None)`` when there is
    none (not a dataset facility, an unknown dataset, or a dataset without a template).

    0.111 (#296): without it a catalog dataset ran every built-in rule at its defaults, and its
    ranking misled (a fault-free unit could rank first). The template is read from the package:
    no network, nothing written. The dataset is the one the facility's registry entry records,
    else the one catalog entry its name matches."""
    from .._servable import _is_dataset_name, catalog_entries_for, read_registry

    if not ctx.store or not _is_dataset_name(facility_id):
        return None, None
    # outside the try: a registry that cannot be read just now is unavailable, never a run
    # without the template (0.112, #324)
    entries, problem = read_registry(ctx.store)
    if problem is not None:
        return None, None
    try:
        from ..datasets import get
        from ..datasets._ops import build_config

        block = (entries.get(facility_id) or {}).get("dataset")
        did = block.get("dataset_id") if isinstance(block, dict) else None
        if did:
            entry = get(str(did))
        else:
            matches = catalog_entries_for(facility_id)
            if len(matches) != 1:
                return None, None
            entry = matches[0]
        if not (entry.suggested_analyses or {}).get("config_template"):
            return None, None
        config = build_config(entry, os.path.abspath(ctx.store), facility_id=facility_id)
    except Exception:  # noqa: BLE001 - no usable template: the capability builds its own
        return None, None
    config.pop("_comment", None)
    return config, entry.id


def store_config(ctx, facility_id: str) -> dict:
    """A store-backed run config for one facility: every stored equipment class that names a
    known family (every class when none does), and the facility's weather equipment (if any) as
    the shared outdoor-air temperature.

    #314: a class no family recognises (a ``LOGGER``) ran every rule on its roles alone and
    could grade checks it did not evaluate ``warn``; as a catalog dataset's template does, the
    default runs no rule on it, and a run config that names its class still can."""
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
        "equipment": [{"class": c} for c in _default_classes(classes.values())],
        "rules": [],
    }
    oat_roles = {p.equip for p in store.points(facility_id=facility_id) if p.role == "oat"}
    weather = sorted(
        e for e, c in classes.items() if equip_family(c) == "weather" and e in oat_roles
    )
    if weather:
        config["shared_oat"] = {"equip": weather[0], "role": "oat"}
    return config


def _default_classes(classes) -> list:
    """The classes a store-backed default runs: those with a known family, else all of them."""
    from ..model.equipclass import equip_family

    every = sorted({str(c) for c in classes})
    known = [c for c in every if equip_family(c) is not None]
    return known or every


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


#: the banner of a run with no run config: neither the caller's nor a dataset's template
DEFAULT_NOTICE = (
    "No run config names this facility, so every equipment class it stores ran with every "
    "built-in rule (or the rules asked for) at their default parameters; a class CAMBER does "
    "not recognise runs only when no class is recognised (a run config can name it)."
)


def facility_config(ctx, facility_id: str, *, keep=RULE_SECTIONS) -> tuple:
    """``(config, base_dir, from_config, notice)``: the run config :func:`caller_config` finds
    for ``facility_id`` (only the ``keep`` sections), else :func:`store_config`.
    ``from_config`` is whether a run config (the caller's or a dataset template) is used;
    ``notice`` is the banner the result carries (``None`` for the caller's own config)."""
    config, base_dir, notice = caller_config(ctx, facility_id)
    if config is not None:
        return {k: v for k, v in config.items() if k in keep}, base_dir, True, notice
    base_dir = os.path.dirname(os.path.abspath(ctx.store))
    return store_config(ctx, facility_id), base_dir, False, DEFAULT_NOTICE


#: an equipment id no store holds (see :func:`narrow`)
_NO_EQUIP = "\x00"


def narrow(config: dict, *, equips=None, start=None, end=None) -> dict:
    """``config`` limited to ``equips`` and the ``start`` / ``end`` window (store sources)."""
    config = dict(config)
    if equips:
        out = []
        for e in config.get("equipment") or []:
            named = e.get("equip")
            named = [named] if isinstance(named, str) else named
            # an entry none of ``equips`` is named in keeps its class (a drift family names it)
            # but discovers nothing: an empty list would mean all of the class (#296)
            keep = sorted(set(equips) & set(named or equips))
            out.append({**dict(e), "equip": keep or [_NO_EQUIP]})
        config["equipment"] = out
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
        "summary": plain(" ".join(str(getattr(f, "summary", "") or "").split())),
        "caveats": [plain(str(c)) for c in getattr(f, "caveats", None) or []],
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
    """``([(rule, equip, roles, scores)], floor)``: the checks the trust floor declined, and the
    floor they declined under (0.111, #297). ``scores`` maps each declined input to the trust it
    scored (0.111, #311; empty for a drift reference's decline).

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
            scores = {
                str(k): float(v)
                for k, v in (m.get("trust") or {}).items()
                if isinstance(v, (int, float))
            }
            out.append((str(getattr(f, "rule", "")), str(getattr(f, "equip", "")), roles, scores))
            if isinstance(m.get("min_trust"), (int, float)):
                floor = max(floor or 0.0, float(m["min_trust"]))
    return sorted(out, key=lambda d: d[:3]), floor


def _score_range(values: list) -> str:
    lo, hi = min(values), max(values)
    return f"trust {lo:.2f}" if f"{lo:.2f}" == f"{hi:.2f}" else f"trust {lo:.2f} to {hi:.2f}"


def trust_banner(declines: list, floor) -> str | None:
    """The NOTICE for :func:`trust_declines` (None when nothing was declined): how many checks,
    which, and on what inputs with the trust they scored -- a declined check is not a pass."""
    if not declines:
        return None
    per_rule: dict = {}
    scored: dict = {}
    for rule, _equip, roles, scores in declines:
        per_rule[rule] = per_rule.get(rule, 0) + 1
        for r in roles:
            scored.setdefault(r, [])
            if r in scores:
                scored[r].append(scores[r])
    which = ", ".join(f"{r} on {n} equipment" for r, n in sorted(per_rule.items()))
    roles = [f"{r} ({_score_range(v)})" if v else r for r, v in sorted(scored.items())]
    inputs = f"; untrusted input(s): {', '.join(roles)}" if roles else ""
    under = f" {floor:g}" if floor is not None else ""
    return (
        f"{len(declines)} check(s) declined because an input scored below the trust floor"
        f"{under} ({which}{inputs}). The fault and warn counts do not cover them: a declined "
        "check is not a pass, and those checks were not judged. sensor_trust over the same "
        "window reports these scores and the flags behind them."
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


@lru_cache(maxsize=1)
def fleet_rules() -> frozenset:
    """The names of the built-in fleet rules, whose one finding is on :data:`FLEET`."""
    from ..rules.builtin import builtin_registry, is_fleet

    reg = builtin_registry()
    return frozenset(n for n in reg.names() if is_fleet(reg.get(n)))


def is_fleet_id(rule: str, equip: str) -> bool:
    """True when ``equip`` is the fleet pseudo-equipment of a finding of ``rule``: a fleet
    rule's :data:`FLEET`, not stored equipment that happens to have that id (0.111, #320)."""
    return equip == FLEET and rule in fleet_rules()


def summary_text(equip: str, summary: str, *, own: bool = False) -> str:
    """A rule's one-line summary of a finding on ``equip`` as a fact's text writes it: the
    equipment it starts with (``AHU-1: ...``) as a quoted name, the rest as the rule's prose."""
    summary = " ".join(str(summary or "").split())
    flat = " ".join(equip.split())
    # #314: the rest of the summary is the rule's text about the data (it may name more units,
    # zones or points): a span, so a name with a slash in it is not taken for a path
    if flat and summary.startswith(f"{flat}:"):
        return name(equip, own=own) + ":" + prose(summary[len(flat) + 1 :])
    return prose(summary)


#: the most null metrics a finding fact names (the rest are counted)
_NULLS_NAMED = 6


def _metric_flags(metrics: dict) -> str:
    """What a finding's metrics say beyond its summary that hosts state from the JSON
    (0112-cite13): its true/false flags (``fan_gated true``, ``method_declared false``) and the
    metrics it left null (``null: oa_cost_excess_cool_kbtu, ...``). Empty when it
    has none."""
    keys = [k for k in sorted(metrics) if not k.startswith("_")]
    parts = [f"{k} {str(metrics[k]).lower()}" for k in keys if isinstance(metrics[k], bool)]
    nulls = [k for k in keys if metrics[k] is None]
    if nulls:
        more = len(nulls) - _NULLS_NAMED
        parts.append(
            "null: " + ", ".join(nulls[:_NULLS_NAMED]) + (f" and {more} more" if more > 0 else "")
        )
    return "; ".join(parts)


def finding_fact(fact_id: str, row: dict, facility_id: str | None = None) -> dict:
    """The fact that cites one finding row; its text names the equipment as a quoted value
    (#291), also where the rule's summary starts with it (``AHU-1: ...``). A finding with
    insufficient data says it was not judged, and the fact names its facility (0.112, #328), so
    a sentence can say either."""
    equip = row["equip"]
    own = is_fleet_id(row["rule"], equip)  # 0.111 (#320)
    summary = summary_text(equip, row["summary"], own=own)
    text = f"{name(equip, own=own)} {row['rule']} ({row['severity']}): {summary}"
    if str(row["summary"]).startswith("insufficient data"):
        text = text.rstrip(" .;") + ": this check was not judged"
    if row.get("conditional_on"):  # 0.111 (#313)
        text = text.rstrip(" .;") + "; conditional on " + "; ".join(row["conditional_on"])
    flags = _metric_flags(row.get("metrics") or {})
    if flags:
        text = text.rstrip(" .;") + "; " + flags
    fact = {
        "id": fact_id,
        "kind": "finding",
        "value": row["severity"],
        "rule": row["rule"],
        "equip": row["equip"],
        "finding_id": row["finding_id"],
        "text": text,
        "metrics": row["metrics"],
    }
    if facility_id is not None:
        fact["facility_id"] = facility_id
    return fact


# --------------------------------------------------------------------------- notes as facts
# 0112-cite10: what a result's JSON says beyond its facts -- a finding's caveats, its labels
# (the string-valued metrics, such as a missed cause or an airflow source) and the samples it
# masked, the rules a run skipped for missing inputs and the basis of a rule's parameters --
# each issued as a fact of its own. Hosts read the whole result and cited the nearest fact for
# these (the real-host baseline of PLAN-CITE-EVAL: 31 of 39 kept false sentences), so a sentence
# stating one now has a fact that states it to cite, and is checked against that fact.

#: the metrics of a finding counted as masked samples (``n_masked_fan_off``, ...)
_MASKED = "n_masked"
#: the longest label value a labels fact gives in full
_LABEL_LEN = 160


def caveat_facts(
    context: str, rows: list, facility_id: str | None = None, totals: dict | None = None
) -> list:
    """The facts of the caveats of finding ``rows``, kind ``caveat``, ``V<k>@<context>``: one
    per rule and caveat text, naming every unit it was given for (a caveat a rule gives each of
    its findings is one fact), in the rule's words (``"AHU-1" leaking_valve caveat: judged on
    supply air minus mixed air ...``): what the rule says may make its findings read wrong, or
    what it left out. ``totals`` (rule: its results on every page): a caveat given for fewer
    of a rule's results says so, ``(given for 3 of its 145 results)``, and carries ``of`` (the
    rule's results), so a sentence must name the units it is for (0112-cite13)."""
    groups: dict = {}
    for r in rows:
        for c in r.get("caveats") or []:
            c = " ".join(str(c).split())
            if c:
                groups.setdefault((r["rule"], c), []).append(r)
    out = []
    for k, ((rule, caveat), of) in enumerate(groups.items(), start=1):
        units = list(dict.fromkeys(r["equip"] for r in of))
        named = ", ".join(name(u, own=is_fleet_id(rule, u)) for u in units)
        total = (totals or {}).get(rule) or 0
        part = f" (given for {len(of)} of its {total} results)" if total > len(of) else ""
        fact = {
            "id": f"V{k}@{context}",
            "kind": "caveat",
            "value": "caveat",  # a word, not a figure a sentence could match
            "rule": rule,
            "finding_ids": [r["finding_id"] for r in of],
            "text": f"{named} {rule} caveat{part}: {prose(caveat).rstrip(' .;')}",
        }
        if part:
            fact["of"] = total
        if len(units) == 1:
            fact["equip"] = units[0]
            fact["subject"] = subject(facility_id, units[0], finding=of[0]["finding_id"])
        else:
            fact["equips"] = units
        out.append(fact)
    return out


def finding_labels(fact_id: str, row: dict, facility_id: str | None = None) -> dict | None:
    """The fact of a finding row's labels, kind ``finding_labels`` (None when it has none): its
    string-valued metrics (``missed_cause``, ``oa_cost_airflow``, ``fan_gate``, an M&V
    ``method`` or ``kernel``) and the samples it masked (``n_masked_*``), each as ``key
    "value"`` (a value is quoted: it may name a zone or a point from the data)."""
    metrics = row.get("metrics") or {}
    labels = [
        f"{k} {name(v, limit=_LABEL_LEN)}"
        for k, v in sorted(metrics.items())
        if isinstance(v, str) and v.strip() and not k.startswith("_")
    ]
    masked = [
        f"{k} {v:g}"
        for k, v in sorted(metrics.items())
        if k.startswith(_MASKED) and isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not labels and not masked:
        return None
    equip = row["equip"]
    own = is_fleet_id(row["rule"], equip)
    parts = []
    if labels:
        parts.append("labels: " + "; ".join(labels))
    if masked:
        parts.append("samples masked: " + "; ".join(masked))
    return {
        "id": fact_id,
        "kind": "finding_labels",
        "value": "labels",
        "rule": row["rule"],
        "equip": equip,
        "finding_id": row["finding_id"],
        "subject": subject(facility_id, equip, finding=row["finding_id"]),
        "text": f"{name(equip, own=own)} {row['rule']} " + "; ".join(parts),
    }


#: the skipped rules a skipped-rules fact names (the rest are counted)
_SKIPS_NAMED = 20


def skipped_fact(fact_id: str, skips: list, facility_id: str) -> dict | None:
    """The fact naming the rules a run skipped for missing inputs, kind ``skipped_rules`` (None
    when it skipped none): per rule, the inputs it lacked and the equipment it was skipped on
    (``skips``: ``{"rule", "equip", "reason", "missing"}`` rows, every page)."""
    if not skips:
        return None
    per: dict = {}
    for s in skips:
        k = (s["rule"], tuple(s.get("missing") or ()))
        per.setdefault(k, []).append(s.get("equip") or "")
    named = []
    for (rule, missing), equips in sorted(per.items())[:_SKIPS_NAMED]:
        units = [e for e in equips if e]
        on = ""
        if units:
            listed = ", ".join(name(e) for e in units[:5])
            more = f" and {len(units) - 5} more" if len(units) > 5 else ""
            on = f" on {listed}{more}"
        lacks = f" (missing {', '.join(prose(m) for m in missing)})" if missing else ""
        named.append(f"{rule}{on}{lacks}")
    more = f"; and {len(per) - _SKIPS_NAMED} more" if len(per) > _SKIPS_NAMED else ""
    rules = sorted({s["rule"] for s in skips})
    return {
        "id": fact_id,
        "kind": "skipped_rules",
        "facility_id": facility_id,
        "value": len(rules),
        "rules": rules,
        "text": f"{len(rules)} rule(s) skipped for missing inputs on {facility_id}, so not "
        f"judged: {'; '.join(named)}{more}",
    }


def parameter_basis_fact(fact_id: str, rule: str, params: list, row: dict, facility_id=None):
    """The fact of the basis a rule gives for its parameters' values, kind
    ``parameter_basis`` (None when none gives one): ``max_starts_per_day: CAMBER judgment: a
    generic cycling limit ...``."""
    said = [
        f"{p['name']}: {prose(' '.join(str(p['basis']).split())).rstrip(' .;')}"
        for p in params
        if isinstance(p.get("basis"), str) and p["basis"].strip()
    ]
    if not said:
        return None
    return {
        "id": fact_id,
        "kind": "parameter_basis",
        "value": "basis",
        "rule": rule,
        "subject": subject(facility_id, row["equip"], finding=row["finding_id"]),
        "text": f"{rule} parameter basis: " + "; ".join(said),
    }


def note_facts(
    context: str, rows: list, facility_id: str | None = None, labels=None, totals=None
) -> list:
    """The caveat facts of ``rows`` (:func:`caveat_facts`, with ``totals``) and the labels facts
    (``M<k>@<context>``, :func:`finding_labels`) of the rows ``labels`` keeps (all when None)."""
    out = caveat_facts(context, rows, facility_id, totals)
    m = 0
    for r in rows:
        if labels is None or labels(r):
            fact = finding_labels(f"M{m + 1}@{context}", r, facility_id)
            if fact is not None:
                m += 1
                out.append(fact)
    return out


# --------------------------------------------------------------------------- structured facts
# 0.112 (#294): a fact added for a figure a model is asked to state carries it as fields too --
# ``value`` with its ``unit``, the ``subject`` it is about (facility, equipment, point) and, where
# one applies, a ``verdict`` (a closed set of words per kind) -- so a citation check can test
# the subject and the unit, not only that the digits appear somewhere in the fact.


def subject(facility_id=None, equip=None, role=None, **more) -> dict:
    """The ``subject`` of a fact: the facility, equipment and point (role) it is about, only the
    ones given (``more``: e.g. ``condition``)."""
    out = {"facility_id": facility_id, "equip": equip, "role": role, **more}
    return {k: str(v) for k, v in out.items() if v is not None}


def when(ts) -> str:
    """A time stamp as a fact's text writes it: ``2025-05-14 06:00`` (its ISO form is a field)."""
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


@lru_cache(maxsize=1)
def _percent_slugs() -> frozenset:
    from ..units import PERCENT_ROLES

    return frozenset(str(r.value) for r in PERCENT_ROLES)


def role_unit(role) -> str | None:
    """The unit CAMBER's rules read a point of ``role`` in (0.112, #294): ``°F`` for a
    temperature or temperature setpoint, ``%`` for a valve, damper, speed or humidity (see
    :mod:`camber.units`), else ``None`` (the point's own units, which the store does not record).
    The adapters convert to these at ingest, so a bias or a constant reading is in this unit."""
    slug = str(jsonable(role))
    if slug in _percent_slugs():
        return "%"
    if slug == "oat" or "temp" in slug.split("_") or slug in ("cool_sp", "heat_sp"):
        return "°F"
    return None


def with_unit(x: float, unit: str | None, fmt: str = "g") -> str:
    """``x`` formatted with ``fmt`` and its unit (``+3.0 °F``, ``0 %``), or alone."""
    return f"{x:{fmt}} {unit}" if unit else f"{x:{fmt}}"


#: a point needs this many samples before "it never moves over the window" is said of it
FLAT_MIN_SAMPLES = 24


def flat_roles(frame, roles=None) -> dict:
    """``{role slug: {"value", "n_samples", "start", "end"}}`` of the numeric points of ``frame``
    that never move over it: every valid sample reads the same value (``roles``: only these
    slugs). A point with fewer than :data:`FLAT_MIN_SAMPLES` valid samples is left out."""
    out: dict = {}
    if frame is None or getattr(frame, "empty", True):
        return out
    for col in frame.columns:
        slug = str(jsonable(col))
        if roles is not None and slug not in roles:
            continue
        s = pd.to_numeric(frame[col], errors="coerce").dropna()
        if len(s) < FLAT_MIN_SAMPLES or not isinstance(s.index, pd.DatetimeIndex):
            continue
        first = float(s.iloc[0])
        if float(s.max()) - float(s.min()) > 1e-9 * max(1.0, abs(first)):
            continue
        out[slug] = {
            "value": round(first, 6),
            "n_samples": int(len(s)),
            "start": s.index.min().isoformat(),
            "end": s.index.max().isoformat(),
        }
    return out


def flat_fact(fact_id: str, facility_id: str, equip: str, role: str, info: dict, why="") -> dict:
    """The fact that the point ``role`` of ``equip`` never moves over the window (``info`` from
    :func:`flat_roles`); ``why`` says what rests on it (``"leaking_valve reads it"``)."""
    v, unit = info["value"], role_unit(role)
    tail = f"; {why}" if why else ""
    return {
        "id": fact_id,
        "kind": "flat_point",
        "equip": equip,
        "role": role,
        "value": v,
        "unit": unit,
        "verdict": "flat",
        "subject": subject(facility_id, equip, role),
        "n_samples": info["n_samples"],
        "start": info["start"],
        "end": info["end"],
        "text": f"{name(equip)} {name(role)} never moves over the window: all "
        f"{info['n_samples']:,} valid samples from {when(info['start'])} to {when(info['end'])} "
        f"read {with_unit(v, unit)}, so it is flat for the whole window{tail}",
    }


def notice_facts(banners: list, context: str) -> list:
    """One fact per notice of a result (0.112, #294), ``N1@<context>`` (``context`` carries the
    call's token), kind ``notice``: the server's own NOTICE lines, so a draft that repeats one
    can cite it. A notice states a condition of the result (a cut list, a declined check, a
    licence); it is not a measured value."""
    return [
        {
            "id": f"N{k}@{context}",
            "kind": "notice",
            "basis": "notice",
            "value": "notice",
            "text": str(b),
        }
        for k, b in enumerate(banners, start=1)
        if isinstance(b, str) and b.strip()
    ]


# --------------------------------------------------------------------------- sensor references


def reference_spec(ctx, facility_id: str) -> tuple:
    """``(references, base_dir)``: the ``report.rcx.references`` block of the run config
    :func:`caller_config` finds for ``facility_id`` (``None`` when it has none) and the
    directory its relative paths resolve against (0.111, #313)."""
    config, base_dir, _notice = caller_config(ctx, facility_id)
    rcx = ((config or {}).get("report") or {}).get("rcx") or {}
    refs = rcx.get("references") if isinstance(rcx, dict) else None
    return (refs if isinstance(refs, dict) and refs else None), base_dir


def reference_findings(ctx, facility_id: str, run) -> tuple:
    """``(findings, notes)``: the configured sensor references of ``facility_id`` compared on
    ``run``'s frames, as ``rcx_report`` compares them (one ``sensor_drift:<role>`` finding each,
    a passing one included). ``notes`` say what could not be compared (0.111, #313)."""
    spec, base_dir = reference_spec(ctx, facility_id)
    if not spec:
        return [], []
    from ..report.rcx import _reference_findings

    try:
        return _reference_findings(run, spec, base_dir or ".")
    except ValueError as e:
        return [], [f"The configured sensor references could not be read: {e}"]


def reference_notes_banner(notes, equips=None) -> str | None:
    """The NOTICE of the configured sensor references that could not be compared (0.111, #320),
    from the ``notes`` of :func:`reference_findings` (with ``equips``: only theirs), or ``None``.
    A finding on such a point is not conditional on its reference: nothing was compared."""
    from ._sanitize import shown

    kept = [
        n
        for n in notes
        if equips is None
        or not n.startswith("Sensor reference for ")
        or any(f"for {e} " in n or f"for {e}:" in n for e in equips)
    ]
    if not kept:
        return None
    return (
        f"{len(kept)} configured sensor reference(s) could not be compared, so the findings that "
        "read those points are not checked against them: "
        + "; ".join(shown(n).rstrip(" .") for n in kept)
        + ". sensor_trust shows the configured comparisons."
    )


def reference_conditions(findings, ref_findings, registry) -> list:
    """Per finding of ``findings``, the labels of the reference warnings (warn or fault) it is
    conditional on: a configured reference for one of its inputs, on its equipment, disagrees.
    The same test ``rcx_report`` makes an issue conditional with (0.111, #313)."""
    from ..rules.triage import _rule_roles, _taints, sensor_causes

    causes = sensor_causes(ref_findings)
    if not causes:
        return [[] for _f in findings]
    rmap = {n: registry.get(n) for n in registry.names()} if registry is not None else {}
    out: list = []
    for f in findings:
        m = getattr(f, "metrics", None) or {}
        if m.get("declined") is True:
            out.append([])
            continue
        roles = _rule_roles(rmap, str(getattr(f, "rule", "")))
        equip = str(getattr(f, "equip", ""))
        out.append([plain(c.label()) for c in causes if _taints(c, equip, roles)])
    return out


def conditional_banner(n: int, hidden: dict | None = None, elsewhere: int = 0) -> str | None:
    """The NOTICE of ``n`` listed findings conditional on a disagreeing sensor reference (#313).

    ``hidden`` (0.111, #318) counts, by severity, the conditional results the ``min_severity``
    floor left out of the list, and ``elsewhere`` (#320) the conditional findings on other pages
    of it: they are named, never counted as listed findings."""
    hidden = {k: int(v) for k, v in (hidden or {}).items() if v}
    if not n and not hidden and not elsewhere:
        return None
    items, hows = [], []
    if elsewhere:
        items.append(f"{elsewhere} finding(s) on other pages of this list")
        hows.append("another offset or a larger limit lists them")
    if hidden:
        items.append(
            ", ".join(f"{v} {k}" for k, v in hidden.items()) + " result(s) below min_severity"
        )
        hows.append("a lower min_severity lists them")
    more = (
        " and ".join(items)
        + f" rest on such an input too and are not listed ({'; '.join(hows)})"
        + ("; a pass measured with that input is conditional as well." if hidden else ".")
    )
    if not n:
        return (
            "No listed finding rests on an input whose configured sensor reference disagrees "
            f"with it, but {more} sensor_trust shows the comparison."
        )
    return (
        f"{n} listed finding(s) rest on an input whose configured sensor reference disagrees "
        "with it (conditional_on names the comparison): treat each as a possible sensor problem "
        "first and check that sensor before acting on the equipment. sensor_trust shows the "
        "comparison." + (f" {more}" if items else "")
    )


# --------------------------------------------------------------------------- masks


def _step_hours(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    step = pd.Series(index).diff().dropna().median()
    return max(float(step / pd.Timedelta(hours=1)), 1e-9)


def mask_summary(
    mask, *, max_spans: int = 20, events=None, judged=None, basis: str | None = None
) -> dict:
    """A boolean evidence mask as hours: ``{"hours", "samples", "judged_samples", "judged_basis",
    "share", "n_spans", "spans": [{"start", "end", "hours"}, ...]}`` -- the longest ``max_spans``
    contiguous runs, in time order. A run breaks where the mask's own samples are more than one
    and a half intervals apart (#298): a mask over the occupied hours alone does not join Monday
    evening to Tuesday morning. With ``events`` (a bool Series of counted event instants, e.g.
    starts) the result also has ``events``, their count.

    ``share`` is of the ``judged_samples``: the samples the rule judged (#310). ``judged``, a
    bool Series, names them (the rule's own mask: fan-on, occupied, running ...), and ``basis``
    says what they are ("fan-on samples"); with no ``judged`` the mask's own index is the judged
    set. A flagged sample outside ``judged`` is counted in it, so the share never exceeds 1."""
    m = pd.Series(mask).fillna(False).astype(bool)
    if not isinstance(m.index, pd.DatetimeIndex):
        m.index = pd.DatetimeIndex(pd.to_datetime(m.index))
    m = m.sort_index()
    step = _step_hours(m.index)
    n_on = int(m.sum())
    if judged is None:
        n_judged = len(m)
    else:
        j = pd.Series(judged).fillna(False).astype(bool)
        if not isinstance(j.index, pd.DatetimeIndex):
            j.index = pd.DatetimeIndex(pd.to_datetime(j.index))
        on = j.index[j.to_numpy()].union(m.index[m.to_numpy()])
        n_judged = int((~on.duplicated()).sum())
    spans: list = []
    if n_on:
        gap = pd.Series(m.index, index=m.index).diff() > pd.Timedelta(hours=1.5 * step)
        run_id = ((m != m.shift()) | gap).cumsum()
        for _k, seg in m[m].groupby(run_id[m]):
            spans.append(
                {
                    "start": seg.index[0].isoformat(),
                    "end": seg.index[-1].isoformat(),
                    "hours": round(len(seg) * step, 2),
                }
            )
    longest = sorted(spans, key=lambda s: (-s["hours"], s["start"]))[:max_spans]
    out = {
        "hours": round(n_on * step, 2),
        "samples": n_on,
        "judged_samples": n_judged,
        "judged_basis": str(basis or "samples"),
        "share": round(n_on / n_judged, 4) if n_judged else 0.0,
        "n_spans": len(spans),
        "spans": sorted(longest, key=lambda s: s["start"]),
    }
    if events is not None:
        out["events"] = int(pd.Series(events).fillna(False).astype(bool).sum())
    return out


MASK: dict = {
    "type": "object",
    "required": [
        "hours",
        "samples",
        "judged_samples",
        "judged_basis",
        "share",
        "n_spans",
        "spans",
    ],
    "additionalProperties": False,
    "properties": {
        "hours": {"type": "number", "minimum": 0},
        "samples": {"type": "integer", "minimum": 0},
        "judged_samples": {
            "type": "integer",
            "minimum": 0,
            "description": "the samples the rule judged, its own mask (fan-on, occupied, "
            "running ...): share is of these, not of the whole window",
        },
        "judged_basis": {
            "type": "string",
            "description": "what the judged samples are, e.g. 'fan-on samples' or 'occupied "
            "samples'",
        },
        "share": {"type": "number", "minimum": 0, "maximum": 1},
        "events": {
            "type": "integer",
            "minimum": 0,
            "description": "for a rule that counts events (starts, reversals): how many it "
            "counted; hours are the time those events cover, not events x the interval",
        },
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
