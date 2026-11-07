"""The ``mv_summary`` capability: the M&V results of a configured project (0.110, #278).

Runs only the ``mv`` section of the caller's run config for the facility (the same code path
as ``camber mv run``: :func:`camber.config.run_config` reads any frozen baseline versions and
never writes them) through :func:`run_config_for`, so nothing is saved and a billing entry
fetches no weather.
"""

from __future__ import annotations

import re

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    COUNTS,
    EQUIPS,
    FACILITY,
    FINDING_ROW,
    by_severity,
    caller_config,
    caveat_facts,
    check_selection,
    cite,
    finding_fact,
    finding_ids,
    finding_row,
    narrow,
    run,
    severity_counts,
    subject,
)
from ._page import OFFSET, limit_arg, page
from ._policy import open_store

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        "facility_id": FACILITY,
        "equips": EQUIPS,
        "claim": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
            "description": "optional claim to check: its figures become claimed_figure facts, "
            "never measured results",
        },
        "limit": limit_arg(5, "meters"),
        "offset": OFFSET,
    },
}

_KINDS = ("mv_baseline", "mv_savings", "mv_trigger", "mv_method_proposal", "other")

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "entries", "meters", "kinds", "counts", "declined", "findings"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "entries": {"type": "integer", "minimum": 0, "description": "mv entries configured"},
        "meters": {"type": "array", "items": {"type": "string"}},
        "kinds": {
            "type": "object",
            "required": list(_KINDS),
            "additionalProperties": False,
            "properties": {k: {"type": "integer", "minimum": 0} for k in _KINDS},
        },
        "counts": COUNTS,
        "declined": {"type": "integer", "minimum": 0},
        "findings": {
            "type": "array",
            "items": FINDING_ROW,
            "description": "mv_baseline (model fit and G14 statistics), mv_savings (avoided "
            "energy, savings %, uncertainty), mv_trigger and mv_method_proposal findings, of "
            "the meters on this page; each finding's metrics give its meter's baseline_start / "
            "baseline_end and reporting_start / reporting_end dates when known",
        },
    },
}

#: config sections an M&V run does not need
_DROP = ("rules", "drift", "report", "faults", "soo", "ventilation", "topology")
_LISTED = 10
#: meters on a page by default, each with all its findings (0.112, #333: every meter before;
#: 0112-cite10: 4, not 5, with each caveat a fact)
_PAGE = 4
#: the caveat an mv_savings finding carries when its baseline fails acceptance
_INFO_ONLY = "this saving is for information only"
#: the unit of a saving when the run config has no ``units`` block
_METER_UNITS = (
    "the meter's own units (its trended rate summed over hours; the run config names no "
    "energy unit)"
)


#: a figure in a claim: a number with its unit, if one follows (or a leading $)
_FIGURE = re.compile(
    r"(?<![A-Za-z0-9\-.])(\$)?(\d[\d,]*(?:\.\d+)?)\s*"
    r"(%|percent\b|kwh\b|mwh\b|gwh\b|kbtu\b|mmbtu\b|therms?\b|kw\b|mw\b|tons?\b)?",
    re.IGNORECASE,
)
#: a claim's unit words as a fact writes them
_UNITS = {
    "%": "%",
    "percent": "%",
    "kwh": "kWh",
    "mwh": "MWh",
    "gwh": "GWh",
    "kbtu": "kBtu",
    "mmbtu": "MMBtu",
    "therm": "therms",
    "kw": "kW",
    "mw": "MW",
    "ton": "tons",
}
#: the figures of one claim given a fact
_CLAIM_FIGURES = 10


#: where a claim's clause ends: a comma, semicolon or colon, or a full stop before a space
_CLAUSE_END = re.compile(r"[,;:]|\.(?=\s|$)")


def _claim_direction(claim: str, start: int, end: int):
    """``"down"`` or ``"up"``: the direction the claim's words give the figure at
    ``claim[start:end]`` (a saving, a reduction; an increase), when its clause says one and only
    one (0.112, #328); else None."""
    from ..agent.verify import _DIRECTION

    cuts = [m.start() for m in _CLAUSE_END.finditer(claim)]
    a = max((c + 1 for c in cuts if c < start), default=0)
    b = min((c for c in cuts if c >= end), default=len(claim))
    clause = claim[a:b]
    found = {_DIRECTION.get(w) for w in re.findall(r"[a-z]+", clause.lower())} - {None, "waste"}
    return found.pop() if len(found) == 1 else None


def _claim_facts(fid: str, claim: str) -> list:
    """One fact per figure the caller's claim states (0.112, #294): kind ``claimed_figure``,
    ``basis`` ``claimed``, so a sentence quoting the claim's own number can be cited, and a
    citation check can tell it from a measured result (it must never support one). Only the
    figure and its unit are written, never the claim's words; the direction the claim gives it
    (a saving: ``down``) is a field (#328), so "the claim states a 12% saving" can be cited."""
    out: list = []
    seen: set = set()
    for m in _FIGURE.finditer(claim or ""):
        dollar, raw, unit = m.group(1), m.group(2).rstrip(","), (m.group(3) or "").lower()
        unit = "$" if dollar else _UNITS.get(unit.rstrip("s") if unit != "%" else unit)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if (value, unit) in seen:
            continue
        seen.add((value, unit))
        shown = {"$": f"${raw}", "%": f"{raw}%", None: raw}.get(unit, f"{raw} {unit}")
        out.append(
            {
                "id": f"A{len(out) + 1}@mv_summary",
                "kind": "claimed_figure",
                "basis": "claimed",
                "value": value,
                "unit": unit,
                "subject": subject(fid),
                "text": f"The claim under check states {shown}: the claim's own figure, not a "
                "measured or computed result",
            }
        )
        direction = _claim_direction(claim, m.start(), m.end()) if unit else None
        if direction:
            out[-1]["direction"] = direction
        if len(out) >= _CLAIM_FIGURES:
            break
    return out


def _day(x) -> str | None:
    """``x`` (a date, a date-time or its text) as ``YYYY-MM-DD``; None when it is not one."""
    import pandas as pd

    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(t) else str(t.date())


def _periods(findings) -> dict:
    """``{meter: {"baseline": [start, end], "reporting": [start, end]}}``: the windows each
    meter's baseline was fitted on and its saving measured over, as dates (0.112, #333), from
    its ``mv_baseline`` and ``mv_savings`` findings; a window that is not known is left out."""
    out: dict = {}
    for f in findings or ():
        m = getattr(f, "metrics", None) or {}
        key = {"mv_baseline": "window", "mv_savings": "reporting_period"}.get(f.rule)
        win = m.get(key) if key else None
        if not (isinstance(win, (list, tuple)) and len(win) == 2):
            continue
        days = [_day(w) for w in win]
        if None not in days:
            what = "baseline" if f.rule == "mv_baseline" else "reporting"
            out.setdefault(str(f.equip), {})[what] = days
    return out


def _span(days: list) -> str:
    """``2016-01-01 to 2016-12-31``, with ``(the year 2016)`` when it is one calendar year."""
    a, b = days
    year = a[:4] if a[5:] == "01-01" and b[5:] == "12-31" and a[:4] == b[:4] else ""
    return f"{a} to {b}" + (f" (the year {year})" if year else "")


def _with_periods(row: dict, fact: dict, periods: dict) -> None:
    """Give a finding of a meter its meter's baseline and reporting windows: dates in the row's
    metrics, and in its fact's fields and text (0.112, #333), so a claim's periods can be
    checked and cited."""
    p = periods.get(row["equip"]) or {}
    said = []
    for what in ("baseline", "reporting"):
        days = p.get(what)
        if not days or (what == "reporting" and row["rule"] == "mv_baseline"):
            continue
        row["metrics"][f"{what}_start"], row["metrics"][f"{what}_end"] = days
        fact[f"{what}_period"] = list(days)
        said.append(f"{what} period {_span(days)}")
    if said:
        fact["text"] += "; " + ", ".join(said)


def mv_summary(ctx, arguments: dict) -> CapabilityResult:
    """The M&V findings of the caller's configured project for one allowed facility."""
    fid = arguments["facility_id"]
    config, base_dir, config_notice = caller_config(ctx, fid)
    if config is None:
        raise CapabilityError(
            "invalid_params",
            f"no M&V project is configured for {fid!r}: the caller's run config does not name it",
        )
    if not config.get("mv"):
        raise CapabilityError(
            "invalid_params", f"the run config for {fid!r} has no 'mv' section (no M&V project)"
        )
    config = {k: v for k, v in config.items() if k not in _DROP}
    config["rules"] = []
    # 0.111 (#296): a meter id the facility does not store is not_found, not "0 meters"
    check_selection(open_store(ctx), fid, {"equips": arguments.get("equips")})
    config = narrow(config, equips=arguments.get("equips"))
    res = run(ctx, config, base_dir)
    rows = by_severity([finding_row(i, f) for i, f in zip(finding_ids(res.findings), res.findings)])
    kinds = dict.fromkeys(_KINDS, 0)
    for r in rows:
        kinds[r["rule"] if r["rule"] in kinds else "other"] += 1
    declined = sum(1 for r in rows if r["metrics"].get("declined") is True)
    meters = sorted({r["equip"] for r in rows})
    periods = _periods(res.findings)
    # 0.111 (#304) / 0.112 (#333): the notices count every meter, the page shows some
    saved = [r for r in rows if r["rule"] == "mv_savings" and not r["metrics"].get("declined")]
    unitless = sum(1 for r in saved if not r["metrics"].get("energy_unit"))
    info_only = sum(1 for r in saved if any(_INFO_ONLY in c for c in r["caveats"]))
    # 0.112 (#333): a page of meters, worst first, each with all its findings
    shown = set(
        page(
            list(dict.fromkeys(r["equip"] for r in rows)),
            arguments,
            default=_PAGE,
            what="meters",
            order="worst first",
            more="or name meters in equips",
        )
    )
    counts, n_rows = severity_counts(rows), len(rows)
    rows = [r for r in rows if r["equip"] in shown]
    facts = [
        {
            "id": "F1@mv_summary",
            "kind": "mv_summary",
            "facility_id": fid,
            "value": n_rows,
            "text": f"M&V for {fid}: {len(meters)} meter(s), {kinds['mv_baseline']} baseline "
            f"fit(s), {kinds['mv_savings']} savings result(s), {kinds['mv_trigger']} "
            f"trigger(s); {declined} result(s) declined",
        }
    ]
    text = [
        f"M&V for {fid} covers {len(meters)} meter(s) with {kinds['mv_savings']} savings "
        f"result(s), {declined} declined [F1@mv_summary]."
    ]
    for i, r in enumerate(rows, start=2):
        fact_id = f"F{i}@mv_summary"
        r["fact"] = fact_id
        fact = finding_fact(fact_id, r, fid)
        # 0.111 (#304): a saving says what unit it is in and whether it may be relied on
        if r["rule"] == "mv_savings" and not r["metrics"].get("declined"):
            if not r["metrics"].get("energy_unit"):
                fact["text"] += f"; energy in {_METER_UNITS}"
            if any(_INFO_ONLY in c for c in r["caveats"]):
                fact["text"] += (
                    "; the baseline does not meet G14 acceptance, so this saving is for "
                    "information only"
                )
        _with_periods(r, fact, periods)
        facts.append(fact)
        if i - 1 <= _LISTED and r["rule"] in ("mv_baseline", "mv_savings", "mv_trigger"):
            text.append(cite(facts[-1]))
    # 0112-cite10: the findings' caveats (the method a saving defaulted to, the G14 kernel's
    # band, the days dropped), one fact per caveat with the meters it was given for
    facts.extend(caveat_facts("mv_summary", rows, fid))
    facts.extend(_claim_facts(fid, arguments.get("claim") or ""))
    data = {
        "facility_id": fid,
        "entries": len(config.get("mv") or []),
        "meters": meters,
        "kinds": kinds,
        "counts": counts,
        "declined": declined,
        "findings": rows,
    }
    banners = [config_notice] if config_notice else []
    if info_only:
        banners.append(
            f"{info_only} saving(s) rest on a baseline that does not meet G14 acceptance: for "
            "information only, not a verified saving."
        )
    if unitless:
        banners.append(
            f"{unitless} saving(s) are in {_METER_UNITS}; add a units block to the run config "
            "to report kWh or kBtu."
        )
    if declined:
        banners.append(
            f"{declined} M&V result(s) declined to state a figure; read their declined_reason "
            "before quoting any saving."
        )
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="mv_summary",
        title="M&V summary",
        description=(
            "Measurement and verification (IPMVP / ASHRAE Guideline 14) results of the facility's"
            " configured M&V project: per meter the baseline model and its fit (R2, CV(RMSE), "
            "NMBE), avoided energy and savings % with uncertainty, rebaseline triggers, and what "
            "declined and why. Use it to check a savings claim. Needs a run config with an mv "
            "section; nothing is saved or fetched. Cost: seconds. Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=mv_summary,
    ),
)
