"""The oracle: real capability facts on a small synthetic site, and the claim tuples they make.

:func:`collect` runs every capability the way ``tests/test_citations_340.py`` does, on a site
built from the RCx test fixture's trends (``tests/_rcx_fixture.py``) with neutral names: three
AHUs (``AHU-1`` with an economizer fault week, ``AHU-2`` with a reheat week, ``AHU-3`` plain), a
reference for ``AHU-1``'s supply air that reads 3 °F low (so ``AHU-1`` reads high) and one for
``AHU-2`` that agrees, and two meters on a second facility with an M&V config. No fact shape is
invented: every fact is what a capability returned.

:func:`claims` turns each fact into :class:`Claim` tuples (site, unit, point, rule, attribute,
value, measurement unit, sign, verdict, window, label) by kind. A claim is something the fact
states; :mod:`.realise` writes it as a sentence and :mod:`.mutate` changes one field of it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, replace

SITE = "site-a"
SITE_NAME = "Test site A"
MV_SITE = "mv-site"
UNITS = ("AHU-1", "AHU-2", "AHU-3")
METERS = ("Meter-1", "Meter-2")
ALLOWED = (SITE, MV_SITE)
REGISTRY_NAMES = {SITE: SITE_NAME, MV_SITE: "Metered site"}

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))


# ----------------------------------------------------------------------------- collection


def _meter(days: int = 150, seed: int = 0, scale: float = 2.0):
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * scale + rng.normal(0, 1, len(idx))
    if seed:  # the second meter uses less after the baseline: a saving to report
        e = np.where(idx >= pd.Timestamp("2024-04-01"), e * 0.9, e)
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


def _store(root: str):
    """The synthetic store and the two run configs (``(rcx_config, mv_config)``)."""
    sys.path.insert(0, os.path.join(REPO, "tests"))
    import _rcx_fixture as fx  # noqa: E402 - the tests' own deterministic trends

    from camber.model.roles import Role
    from camber.store import ParquetStore

    st = ParquetStore(os.path.join(root, "store"))
    frames = {
        "AHU-1": fx.ahu_frame(fault_week=1),
        "AHU-2": fx.ahu_frame(seed=1, reheat_week=2),
        "AHU-3": fx.ahu_frame(seed=2),
    }
    for equip, frame in frames.items():
        st.write_role_frame(frame, facility_id=SITE, equip=equip, equip_class="AHU")
    st.register_facility(SITE, name=SITE_NAME)
    for i, m in enumerate(METERS):
        st.write_role_frame(_meter(seed=i), facility_id=MV_SITE, equip=m, equip_class="METER")
    st.register_facility(MV_SITE, name=REGISTRY_NAMES[MV_SITE])
    refs = {}
    for equip, offset in (("AHU-1", -3.0), ("AHU-2", 0.0)):
        sat = frames[equip][Role.SUPPLY_AIR_TEMP]
        path = os.path.join(root, f"ref-{equip}.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("time,f\n")
            for ts, v in sat.items():
                fh.write(f"{ts.isoformat()},{v + offset:.3f}\n")
        refs[equip] = {"supply_air_temp": {"csv": path, "label": "handheld reference logger"}}
    cfg = fx.config(oat_reference={"fetch": "auto", "latitude": 40.0, "longitude": -75.0})
    cfg["site"] = SITE_NAME
    cfg["source"]["facility_id"] = SITE
    cfg["report"]["rcx"]["references"] = refs
    rcx = os.path.join(root, "rcx.json")
    with open(rcx, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    mv = os.path.join(root, "mv.json")
    with open(mv, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "source": {"kind": "store", "store": "store", "facility_id": MV_SITE},
                "equipment": [{"class": "METER"}],
                "mv": [
                    {
                        "class": "METER",
                        "role": "power",
                        "period": ["2024-01-01", "2024-03-31"],
                        "reporting_period": ["2024-04-01", None],
                    }
                ],
            },
            fh,
        )
    st.points()
    return st, (rcx, mv)


def collect(root: str) -> dict:
    """``{capability: [fact, ...]}`` from real capability calls on the synthetic site under
    ``root`` (a scratch directory), each fact as a client receives it (JSON values)."""
    from camber.capabilities import CallContext, default_registry
    from camber.capabilities._ledger import canonical

    st, configs = _store(root)
    allowed = frozenset(ALLOWED)
    reg = default_registry()
    ctx = CallContext(store=st.root, allowed_facilities=allowed, config=configs)
    out: dict = {}

    def call(name, args):
        out.setdefault(name, [])
        for f in reg.call(name, args, ctx).facts:
            if f not in out[name]:
                out[name].append(canonical(f))

    call("facilities", {})
    call("points", {"facility_id": SITE})
    call("coverage", {"facility_id": SITE})
    call("sensor_trust", {"facility_id": SITE})
    call("rule_run", {"facility_id": SITE})
    findings = [
        f["finding_id"]
        for f in out["rule_run"]
        if f.get("kind") == "finding" and f.get("value") in ("fault", "warn")
    ]
    for fid in findings[:4]:
        call("finding_detail", {"facility_id": SITE, "finding_id": fid})
    call("rcx_report", {"facility_id": SITE, "top_n": 5})
    call("mv_summary", {"facility_id": MV_SITE})
    call("drift", {"facility_id": SITE, "baseline": ["2026-03-02", "2026-03-15"]})
    call("dataset_search", {"query": "sensor"})
    call("dataset_entry", {"dataset_id": "at-30bldg-sensors"})
    call("rule_params", {"rules": ["economizer_high_limit", "leaking_valve"]})
    return out


def collect_totals(root: str) -> list:
    """(round 14) The finding facts that give energy totals (``totals: oa_cost_excess_cool_kbtu
    (oa cost excess cool) ... kBtu``): the synthetic site with ``AHU-1``'s supply airflow trended
    (5,000 cfm while its fan runs), so the outdoor-air rules price its excess outdoor air; a real
    ``rule_run`` call. Appended once to the frozen fact table, as ``rule_run`` facts."""
    from camber.capabilities import CallContext, default_registry
    from camber.capabilities._ledger import canonical
    from camber.model.roles import Role

    st, configs = _store(root)
    frame = st.read_role_frame(facility_id=SITE, equip="AHU-1")
    fan = frame[Role.SUPPLY_FAN_STATUS] if Role.SUPPLY_FAN_STATUS in frame else 1.0
    frame[Role.AIRFLOW] = 5000.0 * (fan > 0)
    st.write_role_frame(frame, facility_id=SITE, equip="AHU-1", equip_class="AHU")
    st.points()
    ctx = CallContext(store=st.root, allowed_facilities=frozenset(ALLOWED), config=configs)
    args = {"facility_id": SITE, "rules": ["outdoor_air_fraction", "economizer_high_limit"]}
    facts = default_registry().call("rule_run", args, ctx).facts
    return [canonical(f) for f in facts if f.get("kind") == "finding" and "; totals: " in f["text"]]


def load_or_collect(cache: str | None = None, root: str | None = None) -> dict:
    """:func:`collect`'s facts, read from ``cache`` when it exists, else collected under
    ``root`` (a temporary directory when none) and written to ``cache``."""
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cite-eval-", dir=root) as tmp:
        facts = collect(tmp)
    if cache:
        os.makedirs(os.path.dirname(os.path.abspath(cache)), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(facts, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return facts


# ----------------------------------------------------------------------------- claim tuples


@dataclass(frozen=True)
class Claim:
    """One thing a fact states, as a tuple of fields. ``attribute`` says what the claim is
    about (``verdict``, ``trust``, ``coverage``, ``hours``, ``count``, ...); ``value`` is its
    figure or word; ``munit`` the measurement unit; ``label`` what a count counts or the class
    a figure carries (``suspect``, ``fault``)."""

    fact_id: str
    capability: str
    kind: str
    attribute: str
    site: str = ""
    unit: str = ""
    point: str = ""
    rule: str = ""
    value: object = None
    munit: str = ""
    sign: str = ""  # "+" / "-" / ""
    verdict: str = ""
    window: tuple = ()
    label: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def key(self) -> tuple:
        """What makes two claims the same statement (the fact id aside)."""
        v = self.value
        if isinstance(v, float):
            v = round(v, 3)
        return (
            self.attribute,
            self.site,
            self.unit,
            self.point,
            self.rule,
            v,
            self.munit,
            self.sign,
            self.verdict,
            tuple(self.window),
            self.label,
            tuple(sorted(k for k in (self.extra or {}) if k in _FLAGS)),
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        d["window"] = list(self.window)
        return d


#: the writing flags of a mutant (see :mod:`.mutate`) that make it another statement
_FLAGS = ("neg", "quant", "period", "check", "site_subject")

_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _num(s: str):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _sign(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return "-" if v < 0 else "+"
    return ""


def _day(s) -> str:
    return str(s or "")[:10]


def _unit_of(f: dict) -> str:
    sub = f.get("subject") or {}
    return str(f.get("equip") or sub.get("equip") or "")


def _site_of(f: dict, default: str) -> str:
    sub = f.get("subject") or {}
    return str(f.get("facility_id") or sub.get("facility_id") or default)


def _rule_of_finding(fid: str) -> str:
    """``rule@equip`` (or ``family:rule@equip``) -> ``rule``."""
    head = str(fid or "").split("@", 1)[0]
    return head.split(":")[-1]


def claims(facts_by_cap: dict) -> list:
    """Every :class:`Claim` the facts make, in a stable order."""
    out: list = []
    for cap in sorted(facts_by_cap):
        for f in facts_by_cap[cap]:
            out.extend(_extract(cap, f))
    return out


def _extract(cap: str, f: dict) -> list:  # noqa: C901 - one branch per fact kind
    kind = f.get("kind", "")
    text = f.get("text", "")
    fid = f["id"]
    site = _site_of(f, SITE if cap != "mv_summary" else MV_SITE)
    unit = _unit_of(f)
    base = {"fact_id": fid, "capability": cap, "kind": kind, "site": site, "unit": unit}
    yr = re.search(r"baseline period ((?:19|20)\d\d)-", text)
    if yr:  # (round 11) the baseline's year, for the header_year form
        base["extra"] = {"year": yr.group(1)}
    out: list = []

    def add(**kw):
        out.append(Claim(**{**base, **kw}))

    if kind == "finding" and cap in ("rule_run", "finding_detail", "mv_summary", "drift"):
        rule = f.get("rule") or _rule_of_finding(f.get("finding_id", ""))
        add(attribute="verdict", rule=rule, verdict=str(f.get("value", "")))
        m = re.search(r"avoided energy ([-\d,.]+) \(([-\d.]+)%\)", text)
        if m:
            pct = _num(m.group(2))
            add(attribute="saving", rule=rule, value=pct, munit="%", sign=_sign(pct))
        m = re.search(r"over (\d+) reporting days", text)
        if m:
            add(attribute="reporting_days", rule=rule, value=_num(m.group(1)), munit="days")
        m = re.search(r"([\d.]+)% of (those samples|occupied hours|fan-on hours)", text)
        if m:
            add(
                attribute="share",
                rule=rule,
                value=_num(m.group(1)),
                munit="%",
                label=m.group(2),
            )
        # (round 14) its energy and cost totals: "totals: key (words) 6,182 kBtu, ..."
        totals = text.split("; totals: ", 1)[1].split("; ", 1)[0] if "; totals: " in text else ""
        for m in re.finditer(
            r"(\w+) \(([a-z0-9 ]+)\) (-?)(\$?)([\d,.]+)(?: (kBtu|MMBtu|Btu|kWh|MWh|therms))?",
            totals,
        ):
            add(
                attribute="total",
                rule=rule,
                value=_num(m.group(5)),
                munit=m.group(6) or "$",
                sign=m.group(3),
                label=m.group(2),
                extra={"key": m.group(1), "shown": m.group(0).split(") ", 1)[1]},
            )
    elif kind == "conditional_not_listed":
        for m in re.finditer(r'"([^"]+)" (\w+) \((ok|warn|fault)\)', text):
            add(attribute="verdict", unit=m.group(1), rule=m.group(2), verdict=m.group(3))
    elif kind == "condition_hours":
        m = re.search(r"flagged (\d+) hours", text)
        sub = f.get("subject") or {}
        if m:
            add(
                attribute="hours",
                unit=str(sub.get("equip") or unit),
                rule=_rule_of_finding(sub.get("finding", "")),
                value=_num(m.group(1)),
                munit="h",
            )
    elif kind == "point_trust":
        add(attribute="trust", point=f.get("role", ""), value=f.get("value"), munit="")
        m = re.search(r"\((trusted|suspect|untrusted)", text)
        if m:
            add(attribute="trust_class", point=f.get("role", ""), label=m.group(1))
    elif kind == "flat_point":
        add(
            attribute="flat",
            point=f.get("role", ""),
            value=f.get("value"),
            munit=str(f.get("unit", "")),
            verdict="flat",
            window=(_day(f.get("start")), _day(f.get("end"))),
            extra={"n_samples": f.get("n_samples")},
        )
    elif kind == "reference_comparison" and re.search(r", RMSE [\d.]+ ?(?:°F|°C):", text):
        # (round 14) a comparison listing its RMSE with its bias (appended to the frozen table
        # for the rmse cases only: its other claims are the frozen comparisons' own)
        sub = f.get("subject") or {}
        point = str(f.get("role") or sub.get("role") or "")
        m = re.search(r"bias ([-+]?[\d.]+) ?(°F|°C), RMSE ([\d.]+) ?(°F|°C):", text)
        if m:
            add(attribute="rmse", point=point, value=_num(m.group(3)), munit=m.group(4))
            add(attribute="rmse_bias", point=point, value=_num(m.group(1)), munit=m.group(2))
    elif kind == "reference_comparison":
        sub = f.get("subject") or {}
        add(
            attribute="reference",
            point=str(f.get("role") or sub.get("role") or ""),
            verdict=str(f.get("verdict") or f.get("value") or ""),
            label="reads high"
            if "reads high" in text
            else ("reads low" if "reads low" in text else ""),
        )
        m = re.search(r"bias ([-+]?[\d.]+) ?(°F|°C)", text)
        if m:
            b = _num(m.group(1))
            add(
                attribute="bias",
                point=str(f.get("role") or sub.get("role") or ""),
                value=b,
                munit=m.group(2),
                sign=_sign(b),
            )
    elif kind in ("coverage", "equip_coverage", "point_coverage"):
        extra = {"n_gaps": f.get("n_gaps")}
        m = re.search(r'worst "[^"]+" at ([\d.]+)%', text)
        if kind == "equip_coverage" and m:  # every point of the unit has at least this much
            extra["worst"] = _num(m.group(1))
        add(
            attribute="coverage",
            unit=unit if kind != "coverage" else "",
            point=f.get("role", "") if kind == "point_coverage" else "",
            value=f.get("value"),
            munit="%",
            extra=extra,
        )
    elif kind == "trust_summary":
        for lab in ("trusted", "suspect", "untrusted"):
            m = re.search(rf"(\d+) {lab}\b", text)
            if m:
                add(attribute="count", unit="", value=int(m.group(1)), label=lab + " points")
        m = re.search(r"(\d+) points judged", text)
        if m:
            add(attribute="count", unit="", value=int(m.group(1)), label="points judged")
    elif kind == "rule_run":
        for lab, n in (f.get("counts") or {}).items():
            add(attribute="count", unit="", value=int(n), label=f"{lab} results")
    elif kind == "evidence_hours":
        sub = f.get("subject") or {}
        add(
            attribute="hours",
            rule=_rule_of_finding(sub.get("finding", "")),
            value=f.get("value"),
            munit="h",
            extra={"n_spans": f.get("n_spans")},
        )
    elif kind == "evidence_span":
        m = re.search(r"span (\d+) of (\d+)", text)
        add(
            attribute="span",
            rule=_rule_of_finding(f.get("finding_id", "")),
            value=f.get("value"),
            munit="h",
            window=(_day(f.get("start")), _day(f.get("end"))),
            label=f"span {m.group(1)}" if m else "",
        )
    elif kind == "facility":
        m = re.search(r"(\d+) points on (\d+) equipment", text)
        if m:
            add(attribute="count", unit="", value=int(m.group(1)), label="points")
            add(attribute="count", unit="", value=int(m.group(2)), label="equipment")
        m = re.search(r"\): (\w+),", text)
        if m:
            add(attribute="state", unit="", verdict=m.group(1))
    elif kind == "point_count":
        add(attribute="count", unit="", value=f.get("value"), label="points")
    elif kind == "facility_count":
        add(attribute="count", site="", unit="", value=f.get("value"), label="facilities")
    elif kind == "mv_summary":
        m = re.search(r"(\d+) meter\(s\)", text)
        if m:
            add(attribute="count", unit="", value=int(m.group(1)), label="meters")
    elif kind == "drift":
        m = re.search(r"(\d+) fault and (\d+) warn", text)
        if m:
            add(attribute="count", unit="", value=int(m.group(1)), label="drift faults")
    elif kind == "issue":
        rank = (f.get("data") or {}).get("rank")
        m = re.search(r"severity (\w+)", text)
        add(attribute="rank", value=rank, verdict=m.group(1) if m else "", label="issue")
    elif kind == "hours" and cap == "rcx_report":
        m = re.search(r"Issue (\d+) ran (\d+) violation hours.*?([\d.]+)% of (\d+)", text)
        if m:
            add(
                attribute="issue_hours",
                value=_num(m.group(2)),
                munit="h",
                label=f"issue {m.group(1)}",
                extra={"pct": _num(m.group(3))},
            )
    elif kind == "rcx_report":
        add(attribute="count", unit="", value=f.get("value"), label="issues")
    elif kind == "dataset":
        add(
            attribute="licence",
            site="",
            unit=str(f.get("dataset_id", "")),
            value=str(f.get("value", "")),
            label="research-only" if "research_only" in text else "",
        )
    elif kind == "dataset_count":
        add(attribute="count", site="", unit="", value=f.get("value"), label="datasets")
    elif kind == "rule_inputs":
        # (round 14) what a rule needs and what it reads when mapped
        rule = str(f.get("rule", ""))
        for part in text.split(": ", 1)[-1].split("; "):
            m = re.match(r"(needs(?: one of)?|reads) (.+?)( when mapped)?$", part)
            if m is None:
                continue
            what = "reads" if m.group(1) == "reads" else "needs"
            for role in re.split(r", | and | or ", m.group(2)):
                add(attribute=f"input_{what}", rule=rule, label=role.strip())
    elif kind == "caveat" and f.get("of"):
        # (round 13) a caveat given for some of its rule's results: said of its units only
        said = text.split(" caveat", 1)[-1].split(": ", 1)[-1]
        units = list(f.get("equips") or ([f["equip"]] if f.get("equip") else []))
        add(
            attribute="caveat",
            rule=str(f.get("rule", "")),
            label=said,
            extra={"units": units, "of": f["of"]},
        )
    elif kind == "rule_params":
        for m in re.finditer(r"(\w+) = ([-\d.]+) (°F|%|fraction)", text):
            add(
                attribute="param",
                site="",
                unit="",
                rule=str(f.get("rule", "")),
                value=_num(m.group(2)),
                munit=m.group(3),
                label=m.group(1),
            )
    elif kind == "rule_count":
        add(attribute="count", site="", unit="", value=f.get("value"), label="rules")
    return out


def true_keys(all_claims: list) -> set:
    """Every claim's :meth:`Claim.key`: a mutant equal to one of these is true, not false."""
    return {c.key() for c in all_claims}


def with_fields(claim: Claim, **kw) -> Claim:
    return replace(claim, **kw)


def facts_index(facts_by_cap: dict) -> dict:
    """``{fact id: fact}`` over every capability."""
    return {f["id"]: f for fs in facts_by_cap.values() for f in fs}


if __name__ == "__main__":  # pragma: no cover - a quick look at the table
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", help="facts JSON to read or write")
    a = ap.parse_args()
    fb = load_or_collect(a.cache)
    for c in claims(fb):
        print(c.capability, c.kind, c.key())
