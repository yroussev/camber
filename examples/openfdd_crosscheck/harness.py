"""G36 cross-check harness: CAMBER ``g36_afdd`` vs open-fdd (pandas and SQL engines) (#22 item 1).

Files and processes only. CAMBER never imports open-fdd: the pandas engine runs in its own
interpreter (``openfdd_pandas_driver.py``, launched as a subprocess with that venv's python) and
the SQL engine runs as ``fdd_cli`` inside a container built from the pinned open-fdd commit
(``Dockerfile.fdd_cli``). This module holds the pure parts the offline tests cover:

* the versioned role mapping (``role_map.json``) and tolerance profiles (``profiles.json``);
* turning a CAMBER role frame into each engine's input (column names, 0-1 scaling, the declared
  fallbacks and substitutions) and the SQL engine's CSV building tree + rule_tuning file;
* one **normaliser per engine** that turns its own output into per-equipment, per-fault-condition
  verdicts, each labelled with the engine, its profile and how its percentage is computed;
* scoring each engine separately with :mod:`camber.validation` (Wilson intervals), per FC and
  overall. Verdicts are never merged across engines.

"Not evaluated" is kept apart from "not detected": an FC the engine could not run (missing input,
equipment class, no applicable hours) is listed with its reason and left out of that FC's
confusion counts, never counted as a miss or a pass.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass

import pandas as pd

from camber.eval import Confusion
from camber.validation import metrics_with_ci

HERE = os.path.dirname(os.path.abspath(__file__))
FCS = tuple(f"FC{i}" for i in range(1, 16))

#: the pinned open-fdd source both open-fdd engines come from (see README "Versions")
OPENFDD_PIN = {
    "pypi_version": "4.4.9",
    "commit": "32a6d4479abed81f9d6d0ff426260cc8ad8e30e2",
    "commit_version_file": "3.5.58",
    "note": (
        "PyPI open-fdd 4.4.9 was released from this commit (its open_fdd/ tree is identical to "
        "the wheel); the repository's newest tag, v3.2.8, is older and predates it"
    ),
}

#: verdict rule applied identically to every engine's own percentage (see ``common_verdict``)
FIRE_PCT = 5.0
MIN_EVAL_HOURS = 24.0

ENGINE_CAMBER = "camber g36_afdd"
ENGINE_PANDAS = "open-fdd pandas"
ENGINE_SQL = "open-fdd sql"


def load_json(name: str) -> dict:
    """Read one of the harness's JSON config files (``role_map.json``, ``profiles.json``)."""
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- engine inputs
def _slug(col) -> str:
    return str(getattr(col, "value", col))


def openfdd_frame(frame: pd.DataFrame, role_map: dict, engine: str) -> tuple[pd.DataFrame, list]:
    """``(frame, applied)``: a CAMBER role frame renamed and scaled for one open-fdd engine.

    ``engine`` is ``"pandas"`` or ``"sql"``. Percent roles become 0-1 fractions. ``applied``
    lists every fallback / substitution the mapping used, so the results can say so.
    """
    if engine not in ("pandas", "sql"):
        raise ValueError(f"engine must be 'pandas' or 'sql', got {engine!r}")
    src = {_slug(c): pd.to_numeric(frame[c], errors="coerce") for c in frame.columns}
    out = pd.DataFrame(index=frame.index)
    applied: list = []
    for row in role_map["roles"]:
        s = src.get(row["camber"])
        if s is None or not s.notna().any():
            continue
        if row["scale"] == "percent_to_fraction":
            s = s / 100.0
        out[row[engine]] = s
    for fb in role_map.get("fallbacks", []):
        target = fb["target"][engine]
        s = src.get(fb["from_camber"])
        if target not in out.columns and s is not None and s.notna().any():
            out[target] = s
            applied.append(f"{target} <- {fb['from_camber']} ({fb['when']})")
    # substitutions apply as one group (e.g. both coil temperatures), or not at all
    has_heat = "heat_valve" in src and src["heat_valve"].notna().any()
    subs = [s for s in role_map.get("substitutions", []) if s["engine"] == engine]
    present = all(s["from_camber"] in src and src[s["from_camber"]].notna().any() for s in subs)
    if subs and present and not has_heat:
        for sub in subs:
            if sub["target"] not in out.columns:
                out[sub["target"]] = src[sub["from_camber"]]
                applied.append(f"{sub['target']} <- {sub['from_camber']} ({sub['when']})")
    return out, applied


def write_sql_tree(frames: dict, role_map: dict, root: str, *, grid_minutes: int) -> dict:
    """Write the SQL engine's CSV input, **one building per equipment**; returns
    ``{equip: applied}``.

    Layout per equipment ``e`` (``b = _safe(e)``): ``<root>/<b>/manifest.json`` and
    ``<root>/<b>/<b>/columns.csv`` + ``history_wide.csv`` (``timestamp_utc`` first). Column names
    are the SQL roles themselves, and columns.csv names the role again. One building per
    equipment matters: ``fdd_cli run-rules`` checks a rule's required roles against the
    building's column set (the union over its equipment), so equipment sharing a building would
    be reported as evaluated -- with zero fault hours -- on inputs only a neighbour has.
    """
    units = {r["sql"]: r["unit"] for r in role_map["roles"]}
    notes = {}
    for equip, frame in frames.items():
        b = _safe(equip)
        bdir = os.path.join(root, b)
        edir = os.path.join(bdir, b)
        os.makedirs(edir, exist_ok=True)
        with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"grid_minutes": int(grid_minutes)}, fh)
        df, applied = openfdd_frame(frame, role_map, "sql")
        notes[equip] = applied
        with open(os.path.join(edir, "columns.csv"), "w", encoding="utf-8") as fh:
            fh.write("column,point_role,point_name,units\n")
            for c in df.columns:
                fh.write(f"{c},{c},{c},{units.get(c, '')}\n")
        idx = pd.DatetimeIndex(df.index)
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        out = df.copy()
        out.insert(0, "timestamp_utc", idx.strftime("%Y-%m-%dT%H:%M:%SZ"))
        out.to_csv(os.path.join(edir, "history_wide.csv"), index=False)
    return notes


def _safe(equip: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in equip)


def sql_tuning_yaml(overrides: dict) -> str:
    """open-fdd ``rule_tuning/defaults.yaml`` text for one profile (``{rule_id: {param: v}}``)."""
    if not overrides:
        return "rules: {}\n"
    lines = ["rules:"]
    for rule_id, params in overrides.items():
        lines.append(f"  {rule_id}:")
        lines.extend(f"    {k}: {float(v)!r}" for k, v in params.items())
    return "\n".join(lines) + "\n"


def sql_rule_overrides(profile: dict, role_map: dict) -> dict:
    """A profile's SQL overrides keyed by the SQL engine's rule ids (FC13 -> FC13-SAT-HIGH)."""
    ids = role_map["fault_conditions"]
    return {ids[fc]["sql"]: p for fc, p in profile.get("sql", {}).items()}


def docker_commands(
    image: str, tree: str, work: str, profile: str, *, buildings: list, tuning_file: str | None
) -> list:
    """``[ingest, run]``: the two ``docker run`` argv lists for one SQL-engine profile.

    Each container loops over ``buildings`` (one per equipment, see :func:`write_sql_tree`) with
    ``/bin/sh``. Both run with ``--network none``. The ingest container reads the CSV tree
    read-only and writes Parquet to ``<work>/parquet``; the run container reads that Parquet and
    the tuning file read-only and writes only ``<work>/results-<profile>/<building>/``.
    """
    for b in buildings:
        if not b or _safe(b) != b:
            raise ValueError(f"unsafe building name {b!r}")
    names = " ".join(buildings)
    base = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh"]
    ingest = base + [
        "-v",
        f"{os.path.abspath(tree)}:/data:ro",
        "-v",
        f"{os.path.abspath(os.path.join(work, 'parquet'))}:/parquet",
        image,
        "-c",
        f"set -e; for b in {names}; do openfdd_cli ingest --data-root /data --building $b "
        "--out /parquet/$b > /dev/null; done",
    ]
    run = base + [
        "-v",
        f"{os.path.abspath(os.path.join(work, 'parquet'))}:/parquet:ro",
        "-v",
        f"{os.path.abspath(os.path.join(work, f'results-{profile}'))}:/out",
    ]
    if tuning_file:
        run += ["-v", f"{os.path.abspath(tuning_file)}:/opt/open-fdd/rule_tuning/defaults.yaml:ro"]
    run += [
        image,
        "-c",
        f"set -e; for b in {names}; do mkdir -p /out/$b; openfdd_cli run-rules --parquet "
        "/parquet/$b --rules-dir /opt/open-fdd/sql_rules --out /out/$b --unit-system imperial "
        "> /out/$b.report.json; done",
    ]
    return [ingest, run]


# --------------------------------------------------------------------------- verdicts
@dataclass
class Verdict:
    """One engine's result for one fault condition on one equipment (never merged)."""

    engine: str
    profile: str
    equip: str
    fc: str
    evaluated: bool
    reason: str | None = None  # why not evaluated
    fault_hours: float | None = None
    eval_hours: float | None = None  # the engine's own denominator, in hours
    denominator: str = ""  # how that denominator is defined
    native_fired: bool | None = None  # the engine's own alarm, by its own rule

    def as_dict(self) -> dict:
        """Plain-dict form for the results JSON."""
        return asdict(self)


def _hours(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def normalise_camber(finding: dict, equip: str, profile: str = "camber_defaults") -> list:
    """Verdicts from a ``g36_afdd`` Finding (``Finding.as_dict()`` or an object with metrics)."""
    metrics = finding.get("metrics") if isinstance(finding, dict) else finding.metrics
    metrics = metrics or {}
    if metrics.get("declined"):
        reason = f"run declined: {metrics.get('reason')}"
        return [Verdict(ENGINE_CAMBER, profile, equip, fc, False, reason) for fc in FCS]
    flagged = set(metrics.get("flagged_fcs") or [])
    out = []
    for fc in FCS:
        v = (metrics.get("fc") or {}).get(fc) or {"status": "missing", "reason": "no result"}
        if v.get("status") != "evaluated":
            out.append(
                Verdict(
                    ENGINE_CAMBER,
                    profile,
                    equip,
                    fc,
                    False,
                    f"{v.get('status')}: {v.get('reason')}",
                )
            )
            continue
        out.append(
            Verdict(
                ENGINE_CAMBER,
                profile,
                equip,
                fc,
                True,
                fault_hours=_hours(v.get("hours")),
                eval_hours=_hours(v.get("applicable_hours")),
                denominator="fan-on hours in the FC's G36 operating states, outside ModeDelay",
                native_fired=fc in flagged,
            )
        )
    return out


_PANDAS_SKIP = {
    "SKIPPED_MISSING_ROLES": "missing roles",
    "NOT_APPLICABLE_EQUIPMENT_TYPE": "not applicable to the equipment type",
    "SKIPPED_EQUIPMENT_OFF": "equipment not proven on",
    "ERROR": "engine error",
}


def normalise_pandas(records: list, role_map: dict, profile: str, poll_seconds: float) -> list:
    """Verdicts from the pandas driver's records (``RuleResult.to_dict()`` + ``equip``)."""
    by_rule = {v["pandas"]: fc for fc, v in role_map["fault_conditions"].items()}
    out = []
    for r in records:
        fc = by_rule.get(r.get("rule_id"))
        if fc is None:
            continue
        status = r.get("status")
        equip = r["equip"]
        if status not in ("FAULT", "PASS"):
            why = _PANDAS_SKIP.get(status, str(status))
            miss = r.get("missing_roles") or []
            if miss:
                why += ": " + ", ".join(miss)
            out.append(Verdict(ENGINE_PANDAS, profile, equip, fc, False, why))
            continue
        active = (r.get("metrics") or {}).get("active_sample_count")
        n = active if active is not None else r.get("sample_count")
        eval_h = None if n is None else float(n) * float(poll_seconds) / 3600.0
        out.append(
            Verdict(
                ENGINE_PANDAS,
                profile,
                equip,
                fc,
                True,
                fault_hours=_hours(r.get("fault_hours")),
                eval_hours=eval_h,
                denominator="samples inside the rule's operational gate (fan proven on, after "
                "the startup delay), as hours",
                native_fired=status == "FAULT",
            )
        )
    return out


def normalise_sql(
    bodies: dict, role_map: dict, profile: str, equips: list, fan_on_hours: dict
) -> list:
    """Verdicts from the SQL engine's per-rule result files (``{rule_id: parsed JSON}``).

    A rule file holds ``rows`` (one per equipment) or a skip marker. When a row carries only
    ``fault_hours`` (no ``fault_pct`` / ``total_hours``), the denominator is the equipment's
    fan-on hours in the exported frame, and the verdict says so.
    """
    ids = role_map["fault_conditions"]
    names = {_safe(e): e for e in equips}
    out = []
    for fc in FCS:
        body = bodies.get(ids[fc]["sql"])
        if body is None:
            for e in equips:
                out.append(Verdict(ENGINE_SQL, profile, e, fc, False, "no result file"))
            continue
        if body.get("skipped") or body.get("status") == "SKIPPED_MISSING_ROLES":
            why = "missing roles: " + ", ".join(body.get("missing_roles") or [])
            out.extend(Verdict(ENGINE_SQL, profile, e, fc, False, why) for e in equips)
            continue
        if body.get("error"):
            why = f"engine error: {body['error']}"
            out.extend(Verdict(ENGINE_SQL, profile, e, fc, False, why) for e in equips)
            continue
        rows = {
            names.get(str(r.get("equipment_id")), str(r.get("equipment_id"))): r
            for r in body.get("rows", [])
        }
        for e in equips:
            r = rows.get(e)
            if r is None:
                out.append(Verdict(ENGINE_SQL, profile, e, fc, False, "no row for this equipment"))
                continue
            fh = _hours(r.get("fault_hours"))
            pct = _hours(r.get("fault_pct"))
            if _hours(r.get("total_hours")) is not None:
                eval_h, den = _hours(r.get("total_hours")), "total_hours reported by the rule"
            elif pct and fh is not None:
                eval_h, den = 100.0 * fh / pct, "fault_hours / fault_pct reported by the rule"
            else:
                eval_h = fan_on_hours.get(e)
                den = "fan-on hours of the exported frame (the rule reports fault hours only)"
            out.append(
                Verdict(
                    ENGINE_SQL,
                    profile,
                    e,
                    fc,
                    True,
                    fault_hours=fh,
                    eval_hours=eval_h,
                    denominator=den,
                    native_fired=bool(fh and fh > 0),
                )
            )
    return out


def common_verdict(v: Verdict, *, fire_pct: float = FIRE_PCT, min_hours: float = MIN_EVAL_HOURS):
    """``(state, reason)`` under the shared rule, applied to each engine's OWN percentage.

    ``state`` is ``"fired"`` (fault hours >= ``fire_pct`` % of the engine's evaluated hours),
    ``"not_fired"`` or ``"not_evaluated"`` (the engine did not run the FC, or evaluated it on
    fewer than ``min_hours`` hours).
    """
    if not v.evaluated:
        return "not_evaluated", v.reason
    if v.eval_hours is None or v.eval_hours < min_hours:
        return "not_evaluated", f"evaluated on fewer than {min_hours:g} h"
    pct = 100.0 * (v.fault_hours or 0.0) / v.eval_hours
    return ("fired" if pct >= fire_pct else "not_fired"), None


def native_verdict(v: Verdict):
    """``(state, reason)`` by the engine's own alarm (CAMBER: flagged FC; open-fdd: FAULT)."""
    if not v.evaluated:
        return "not_evaluated", v.reason
    return ("fired" if v.native_fired else "not_fired"), None


# --------------------------------------------------------------------------- scoring
def _rates(c: Confusion) -> dict:
    ci = metrics_with_ci(c)
    out: dict = {"tp": c.tp, "fn": c.fn, "fp": c.fp, "tn": c.tn}
    for key, short in (("true_positive_rate", "tpr"), ("false_positive_rate", "fpr")):
        r = ci[key]
        out[short] = None if r.n == 0 else r.rate
        out[f"{short}_ci"] = None if r.n == 0 else [r.lo, r.hi]
    return out


def score(verdicts: list, labels: dict, *, rule=common_verdict) -> dict:
    """Score each (engine, profile) separately: per FC, overall (any FC), per fault type.

    ``labels`` maps equipment -> fault type (``""`` = fault-free). Equipment absent from
    ``labels`` is ignored. ``rule`` is :func:`common_verdict` or :func:`native_verdict`.
    """
    groups: dict = {}
    for v in verdicts:
        if v.equip in labels:
            groups.setdefault((v.engine, v.profile), []).append(v)
    out: dict = {}
    for (engine, profile), vs in sorted(groups.items()):
        per_fc: dict = {}
        state: dict = {}
        for v in vs:
            st, why = rule(v)
            state[(v.equip, v.fc)] = st
            d = per_fc.setdefault(v.fc, {"_c": [0, 0, 0, 0], "not_evaluated": {}})
            if st == "not_evaluated":
                d["not_evaluated"][v.equip] = why
                continue
            pos = bool(labels[v.equip])
            fired = st == "fired"
            d["_c"][(0 if fired else 1) if pos else (2 if fired else 3)] += 1
        fc_out = {}
        for fc in FCS:
            d = per_fc.get(fc, {"_c": [0, 0, 0, 0], "not_evaluated": {}})
            tp, fn, fp, tn = d["_c"]
            fc_out[fc] = {
                **_rates(Confusion(tp=tp, fp=fp, fn=fn, tn=tn)),
                "not_evaluated": d["not_evaluated"],
            }
        # overall: an equipment is detected when any evaluated FC fired; with no FC evaluated it
        # is not evaluated at all
        tp = fn = fp = tn = 0
        by_type: dict = {}
        not_eval = []
        for equip, truth in sorted(labels.items()):
            sts = [state[(equip, fc)] for fc in FCS if (equip, fc) in state]
            ev = [s for s in sts if s != "not_evaluated"]
            if not ev:
                not_eval.append(equip)
                continue
            fired = "fired" in ev
            if truth:
                tp, fn = tp + fired, fn + (not fired)
                t = by_type.setdefault(truth, [0, 0])
                t[0 if fired else 1] += 1
            else:
                fp, tn = fp + fired, tn + (not fired)
        overall = _rates(Confusion(tp=tp, fp=fp, fn=fn, tn=tn))
        overall["not_evaluated"] = not_eval
        types = {
            k: _rates(Confusion(tp=a, fp=0, fn=b, tn=0)) for k, (a, b) in sorted(by_type.items())
        }
        for t in types.values():
            for k in ("fp", "tn", "fpr", "fpr_ci"):
                t.pop(k, None)
        out[f"{engine} [{profile}]"] = {
            "per_fc": fc_out,
            "overall": overall,
            "by_fault_type": types,
        }
    return out


# --------------------------------------------------------------------------- probes
#: Synthetic one-day frames that isolate one engine behaviour each. ``expect`` maps
#: "<engine> [<profile>]" -> whether the FC should fire (engine's own alarm). The SQL entries were
#: first predicted from a static reading of the pinned source, then confirmed by running fdd_cli
#: (fixtures/sql_probe_results.json).
PROBES = {
    "fc13_sat_1p5_over_sp_full_cooling": {
        "fc": "FC13",
        "what": "full cooling at minimum OA, SAT 1.5 F above its setpoint: inside the G36 SAT "
        "tolerance (2 F), outside open-fdd's default (1.15 F)",
        "frame": {"cool_valve": 100.0, "oa_damper": 0.0, "sat_offset_sp": 1.5},
        "expect": {
            "camber g36_afdd [camber_defaults]": False,
            "open-fdd pandas [openfdd_defaults]": True,
            "open-fdd pandas [g36]": False,
            "open-fdd sql [openfdd_defaults]": True,
            "open-fdd sql [g36]": True,  # eps_sat is not reachable (profiles.json)
        },
    },
    "fc13_sat_3_over_sp_half_cooling": {
        "fc": "FC13",
        "what": "cooling valve at 50 % (not full), minimum OA, SAT 3 F above its setpoint: G36 "
        "FC13 is a full-cooling test",
        "frame": {"cool_valve": 50.0, "oa_damper": 0.0, "sat_offset_sp": 3.0},
        "expect": {
            "camber g36_afdd [camber_defaults]": False,
            "open-fdd pandas [openfdd_defaults]": True,  # clg_full_min default 0.01
            "open-fdd pandas [g36]": True,
            "open-fdd sql [openfdd_defaults]": False,  # clg_full_min default 0.9
            "open-fdd sql [g36]": False,
        },
    },
    "fc9_oat_4_over_sp_free_cooling": {
        "fc": "FC9",
        "what": "free cooling (damper 50 %, valve shut), OAT 4 F above the SAT setpoint: G36 "
        "fires above SATSP + 5 F (eps OAT 5 + eps SAT 2 - fan heat 2); open-fdd's default above "
        "SATSP + 1.75 F",
        "frame": {"cool_valve": 0.0, "oa_damper": 50.0, "oat_offset_sp": 4.0},
        "expect": {
            "camber g36_afdd [camber_defaults]": False,
            "open-fdd pandas [openfdd_defaults]": True,
            "open-fdd pandas [g36]": False,
            "open-fdd sql [openfdd_defaults]": True,
            "open-fdd sql [g36]": True,  # the EPS_MAT FC9 reads stays at 1.15
        },
    },
    "fc8_sat_3p5_over_mat_free_cooling": {
        "fc": "FC8",
        "what": "free cooling (damper 50 %, valve shut), SAT 3.5 F above MAT: outside open-fdd's "
        "default band (|SAT - 0.55 - MAT| > 1.63 F), inside the G36 one (|SAT - 2 - MAT| > "
        "5.39 F). A positive control: FC8's tolerances are reachable in both open-fdd engines, so "
        "the G36 profile must silence it, showing the override mechanism is applied",
        "frame": {
            "cool_valve": 0.0,
            "oa_damper": 50.0,
            "oat_offset_sp": 4.0,
            "sat_offset_mat": 3.5,
        },
        "expect": {
            "camber g36_afdd [camber_defaults]": False,
            "open-fdd pandas [openfdd_defaults]": True,
            "open-fdd pandas [g36]": False,
            "open-fdd sql [openfdd_defaults]": True,
            "open-fdd sql [g36]": False,
        },
    },
}


def probe_frame(spec: dict, *, periods: int = 96, freq: str = "15min") -> pd.DataFrame:
    """A CAMBER role frame (slug columns) for one probe: fan on, steady values, one day."""
    idx = pd.date_range("2024-07-01", periods=periods, freq=freq)
    sp = 55.0
    mat = 71.5  # minimum OA: the mixed air is nearly all return air
    sat = sp + spec.get("sat_offset_sp", 0.0)
    oat = sp + spec.get("oat_offset_sp", 25.0)
    if spec.get("oa_damper", 0.0) >= 40.0 and spec.get("cool_valve", 0.0) == 0.0:
        # free cooling: mixed air sits between OAT and RAT and the supply air follows it
        mat = (oat + 72.0) / 2.0
        sat = mat + spec.get("sat_offset_mat", 0.5)
    cols = {
        "supply_air_temp": sat,
        "supply_air_temp_sp": sp,
        "mixed_air_temp": mat,
        "return_air_temp": 72.0,
        "oat": oat,
        "cool_valve": spec.get("cool_valve", 0.0),
        "oa_damper": spec.get("oa_damper", 0.0),
        "supply_fan_status": 1.0,
        "supply_fan_speed": 80.0,
    }
    return pd.DataFrame({k: float(v) for k, v in cols.items()}, index=idx)


# --------------------------------------------------------------------------- report
def _fmt(rate, ci) -> str:
    if rate is None:
        return "n/a"
    return f"{rate:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]"


def markdown(results: dict) -> str:
    """The Markdown summary written next to the results JSON."""
    lines = [
        "# G36 cross-check: CAMBER g36_afdd vs open-fdd",
        "",
        "Generated by `examples/openfdd_crosscheck/run_crosscheck.py`. Each engine is scored on "
        "its own; nothing is merged. Rates carry Wilson 95% intervals. Verdict rule: "
        f"{results['verdict_rule']}.",
        "",
        "## Versions",
        "",
    ]
    for k, v in results["versions"].items():
        lines.append(f"- **{k}**: {v}")
    lines += ["", "## Data", ""]
    for d in results["datasets"]:
        lines.append(
            f"- `{d['id']}` ({d['licence']}): {d['n_labelled']} labelled runs "
            f"({d['n_fault_free']} fault-free), window `{results['window']}`"
            + (
                f"; CAMBER template params {d['camber_template_params']}"
                if d.get("camber_template_params")
                else ""
            )
        )
    for group, engines in results["scores"]["common"].items():
        lines += ["", f"## {group}"]
        lines += _engine_tables(engines)
    native = results["scores"].get("native", {}).get("pooled", {})
    if native:
        lines += [
            "",
            "## Engines' own alarms (pooled, any FC)",
            "",
            "| engine [profile] | TPR [95% CI] | FPR [95% CI] | TP/FN/FP/TN |",
            "|---|---|---|---|",
        ]
        for name, sc in native.items():
            o = sc["overall"]
            lines.append(
                f"| {name} | {_fmt(o['tpr'], o['tpr_ci'])} | {_fmt(o['fpr'], o['fpr_ci'])} | "
                f"{o['tp']}/{o['fn']}/{o['fp']}/{o['tn']} |"
            )
    if results.get("not_run"):
        lines += ["", "## Not run", ""]
        lines += [f"- **{k}**: {v}" for k, v in results["not_run"].items()]
    if results.get("caveats"):
        lines += ["", "## Caveats", ""]
        lines += [f"- {c}" for c in results["caveats"]]
    return "\n".join(lines) + "\n"


def _count(x) -> int:
    return x if isinstance(x, int) else len(x)


def _engine_tables(engines: dict) -> list:
    lines: list = []
    for name, sc in engines.items():
        lines += [
            "",
            f"### {name}",
            "",
            "| FC | TPR [95% CI] | FPR [95% CI] | TP/FN/FP/TN | not evaluated |",
            "|---|---|---|---|---|",
        ]
        for fc in FCS:
            r = sc["per_fc"][fc]
            ne = r["not_evaluated"]
            if "counts_by_reason" in ne:  # collapsed (the default; --keep-verdicts keeps them)
                ne = {
                    f"{why}#{i}": why for why, n in ne["counts_by_reason"].items() for i in range(n)
                }
            reasons = sorted({str(x) for x in ne.values()})
            ne_txt = f"{len(ne)}: {'; '.join(reasons)}" if ne else ""
            lines.append(
                f"| {fc} | {_fmt(r['tpr'], r['tpr_ci'])} | {_fmt(r['fpr'], r['fpr_ci'])} | "
                f"{r['tp']}/{r['fn']}/{r['fp']}/{r['tn']} | {ne_txt} |"
            )
        o = sc["overall"]
        lines.append(
            f"| **any FC** | {_fmt(o['tpr'], o['tpr_ci'])} | {_fmt(o['fpr'], o['fpr_ci'])} | "
            f"{o['tp']}/{o['fn']}/{o['fp']}/{o['tn']} | {_count(o['not_evaluated']) or ''} |"
        )
        if sc["by_fault_type"]:
            lines += [
                "",
                "Detection (any FC) by fault type: "
                + "; ".join(
                    f"{k} {v['tp']}/{v['tp'] + v['fn']}" for k, v in sc["by_fault_type"].items()
                ),
            ]
    return lines


def standard_caveats(window: str, datasets: list) -> list:
    """The caveats every results file carries."""
    out = [
        "Small samples: each labelled run is one case, so the Wilson intervals are wide; read "
        "differences between engines through the intervals, not the point rates.",
        "Simulated data (LBNL FDD datasets, CC-BY-4.0, fetched from the publisher; never "
        "redistributed). Fault labels are per run, not per hour: a run counts as detected when "
        "the FC fires anywhere in it, including in seasons where the fault has no symptom.",
        "Tolerance profiles change tolerances only; ModeDelay, confirm/AlarmDelay windows and "
        "the valve/damper thresholds that select operating states stay at each engine's "
        "defaults.",
    ]
    if window == "month":
        out.append(
            "Month windows: each (run, month) is scored as a case. Months of one run are not "
            "independent, so the intervals are narrower than the evidence supports."
        )
    if "lbnl-ddahu" in datasets:
        out.append(
            "lbnl-ddahu is a dual-duct AHU: G36 §5.16.14 is written for single-duct units. The "
            "mapping reads the cold-deck discharge as SAT, the cold-deck setpoint as the SAT "
            "setpoint and the hot-deck valve as the heating valve, so 'SAT below MAT while "
            "heating' is the design there, not a fault, and the SAT setpoint tests (FC7, FC9, "
            "FC11, FC13) judge the cold deck only; the hot deck's own setpoint is not mapped."
        )
    return out
