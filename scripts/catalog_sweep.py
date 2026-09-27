"""Full-catalog local validation sweep: every catalog dataset, end to end (maintainer tool).

    python scripts/catalog_sweep.py --local-root examples/_data --out /scratch/sweep
    python scripts/catalog_sweep.py --out /scratch/sweep --only lbnl-fcu,lbnl-boiler
    python scripts/catalog_sweep.py --out /scratch/sweep --skip-done          # resume
    python scripts/catalog_sweep.py --out /scratch/sweep --compare old/summary.json
    python scripts/catalog_sweep.py --out /scratch/sweep --summarize-only     # rebuild summary

The pre-release check for the dataset catalog (``camber/datasets/catalog.json``). For each entry,
on the ``full`` subset by default, it:

1. **fetch** -- seeds ``$CAMBER_DATA_DIR`` from local copies (hard links, else copies, into the
   ``<id>/downloads/`` layout) and runs the real :func:`camber.datasets.fetch`, which verifies
   every file's pinned size + sha256 without downloading. The network is refused unless
   ``--download-missing`` is given; files with no local copy are listed.
2. **ingest** -- into one store inside a portfolio workspace (``<out>/workspace``), then a second
   ``camber datasets ingest`` that must skip on the unchanged content hash. Records rows,
   facilities, equipment, time span, store bytes, warnings, quirks, duplicates and a per-role
   quality pass (empty / flat series, percent roles outside 0-100).
3. **run** -- ``camber datasets config`` + ``camber run``; ``camber drift run`` when the template
   has a drift section (none freezes or accepts a baseline). Findings by rule x equipment x
   severity, declines and not-evaluated (rule, equipment) pairs.
4. **score** -- ``camber datasets score`` on labelled entries (overall, per detector, per fault
   type, each with a Wilson 95% interval) and a re-score on the LBNL benchmark's scenario set
   where the two overlap, compared with ``examples/lbnl_fdd/benchmark-baseline.json``.
5. **fault-free trips** -- every warn/fault on a fault-free scenario, with its metrics and a
   reviewed classification (:data:`KNOWN_TRIPS`; anything new is "unclear (unreviewed)").
6. **reports** -- ``camber report --layout rcx`` and the audit layout for one fault-free and one
   faulted equipment; checks exit code, provenance/licence block, and that no G36 verdict appears
   without a declared sequence (citations are not verdicts, #36). Build time and size recorded.
   ``--skip-rcx ID,...`` builds only the audit layout for the named entries: the RCx report's
   representative-week search (``select_week``) is slow on long runs -- one ``at-30bldg-sensors``
   sensor (23 months) takes many minutes -- until #35 is fixed; the skip is recorded.
7. **BDG2** -- the M&V baseline path for every ingested site (template meters, plus every other
   cleaned meter class), acceptance rates with Wilson intervals against
   ``examples/bdg2/benchmark-baseline.json``.

Outputs ``<out>/summary.json`` and ``<out>/summary.md``; per-dataset artefacts and CLI logs sit in
``<out>/datasets/<id>/``. ``--skip-done`` resumes (a dataset whose ``result.json`` completed on the
same subset is not redone), ``--only`` limits the sweep, ``--compare`` diffs a previous summary.
Results are deterministic for the same data and code; only timings and timestamps vary.

Dev tooling: not part of the package and not run in CI. Needs the local data (tens of GB).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import urllib.error

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from camber import __version__  # noqa: E402
from camber import datasets as ds  # noqa: E402
from camber.datasets._fetch import ChecksumMismatch, FetchError, download  # noqa: E402
from camber.datasets._labels import score_records  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.validation import wilson_interval  # noqa: E402

SCHEMA = 1
FIRED = ("warn", "fault")
LBNL_BASELINE = os.path.join(REPO, "examples", "lbnl_fdd", "benchmark-baseline.json")
BDG2_BASELINE = os.path.join(REPO, "examples", "bdg2", "benchmark-baseline.json")

# The LBNL benchmark family label of each catalog entry whose default subset *is* the benchmark's
# scenario set (examples/lbnl_fdd/benchmark.py FAMILIES).
BENCH_FAMILY = {
    "lbnl-sdahu": "SDAHU (single-duct AHU)",
    "lbnl-fcu": "FCU (fan-coil unit)",
    "lbnl-ddahu": "DDAHU (dual-duct AHU)",
}

# The faulted scenario each report pair uses (the fault-free one is found from the labels).
REPORT_FAULTED = {
    "lbnl-sdahu": "AHU__damper_stuck_025",
    "lbnl-fcu": "FCU__OADMPRStuck_100",
    "lbnl-ddahu": "DDAHU__DMPRStuck_OA_100",
    "lbnl-fpu": "PFPU__VAVDMPRStuck_50pct",
    "lbnl-chiller": "PLANT__coolingtower_fouling_095",
    "lbnl-boiler": "PLANT__boiler_PI",
    "ornl-frp-vav": "RTU_VAV_205__d3_stuck_000",
}

# Reviewed classifications of rules that trip on a fault-free scenario, keyed (dataset, rule).
# "design" = a plausible genuine characteristic of the simulated design; "assumption" = a likely
# CAMBER assumption or mapping error (see issue #23); "unclear" = not settled. Anything not listed
# is reported as "unclear (unreviewed)" so a new trip is never silently accepted.
KNOWN_TRIPS: dict = {
    ("lbnl-ddahu", "simultaneous_heat_cool"): (
        "design",
        "a dual-duct AHU heats the hot deck and cools the cold deck at the same time by design; "
        "the rule assumes a single-path unit, so on this class it measures the design, not a "
        "fault (it fires on 53 of 56 runs)",
    ),
    ("lbnl-ddahu", "outdoor_air_fraction"): (
        "assumption",
        "single-mixing-box OA fraction against an assumed 20% design minimum on a dual-duct "
        "unit; the LBNL benchmark already records this as its DDAHU transferability gap "
        "(FPR 1.0); the design minimum is undocumented (issue #23)",
    ),
    ("ornl-frp-vav", "unmet_setpoint_hours"): (
        "design",
        "real weather, not a fault: on the set-3 fault-free day (2023-12-14) the room-205 box's "
        "room overheated to 26 C in the afternoon from solar gain through its south and west "
        "windows, as the data descriptor's technical validation reports",
    ),
}

# A G36 verdict (a finding judged against G36) is fine only when qualified as a reference / declined
# verdict. Citations are not verdicts: a table column headed like _CITE_HEADERS (the action plan's
# "Cite", an ECM "Standard" / "Reference") and a section citation ("G36 §5.16") are skipped (#36).
_G36_OK = (
    "reference",
    "declined",
    "not a verdict",
    "no site sequence",
    "not the unit",
    "not evaluated",
)


class StopSweep(RuntimeError):
    """A hard stop (e.g. free disk would drop below the floor)."""


class _NoNetwork:
    """An opener for :func:`camber.datasets.fetch` that refuses every request."""

    def open(self, request, timeout=None):  # noqa: D401 - opener protocol
        url = getattr(request, "full_url", request)
        raise urllib.error.URLError(f"network disabled by catalog_sweep ({url})")


# --------------------------------------------------------------------------- small helpers


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _free_gb(path: str) -> float:
    probe = os.path.abspath(path)
    while not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    return shutil.disk_usage(probe).free / 1e9


def _dir_bytes(path: str, *, unique_only: bool = False) -> int:
    """Bytes under ``path``; ``unique_only`` skips files hard-linked from elsewhere."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            if unique_only and st.st_nlink > 1:
                continue
            total += st.st_size
    return total


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _ci(k: int, n: int) -> list:
    if not n:
        return [None, None]
    lo, hi = wilson_interval(k, n)
    return [round(lo, 4), round(hi, 4)]


def _r(x, nd: int = 4):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return None if f != f else round(f, nd)


def _scalar_metrics(metrics: dict, limit: int = 14) -> dict:
    out = {}
    for k in sorted(metrics or {}):
        v = metrics[k]
        if isinstance(v, (bool, int, float, str)) or v is None:
            out[k] = _r(v) if isinstance(v, float) else v
        if len(out) >= limit:
            break
    return out


class Ctx:
    """Paths, flags and the CLI environment shared by every step."""

    def __init__(self, args):
        self.out = os.path.abspath(args.out)
        self.cache = os.path.abspath(args.data_dir or os.path.join(self.out, "cache"))
        self.workspace = os.path.join(self.out, "workspace")
        self.store = os.path.join(self.workspace, "store")
        self.local_roots = [os.path.abspath(p) for p in (args.local_root or [])]
        self.subset = args.subset
        self.download_missing = args.download_missing
        self.keep_extracted = args.keep_extracted
        self.force_ingest = args.force_ingest
        # research-only (NC/ND) entries are fetched / ingested only with an explicit acceptance
        self.accept_noncommercial = bool(getattr(args, "accept_noncommercial", False))
        self.min_free_gb = args.min_free_gb
        self.skip_reports = args.skip_reports
        self.skip_rcx = {x.strip() for x in (getattr(args, "skip_rcx", "") or "").split(",") if x}
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["CAMBER_DATA_DIR"] = self.cache
        env["MPLBACKEND"] = "Agg"
        env.pop("CAMBER_PORTFOLIO", None)
        self.env = env
        self._index: dict | None = None

    def ddir(self, dataset_id: str) -> str:
        return os.path.join(self.out, "datasets", dataset_id)

    def disk_guard(self, needed_bytes: int, what: str) -> dict:
        free = _free_gb(self.out)
        after = free - needed_bytes / 1e9
        rec = {"free_gb_before": round(free, 1), "needed_gb": round(needed_bytes / 1e9, 1)}
        if after < self.min_free_gb:
            raise StopSweep(
                f"{what}: free disk {free:.1f} GB - {needed_bytes / 1e9:.1f} GB needed would "
                f"leave {after:.1f} GB (< the {self.min_free_gb:g} GB floor); stopping"
            )
        return rec

    def local_index(self) -> dict:
        """``{size: [path, ...]}`` over every ``--local-root``."""
        if self._index is None:
            idx: dict = {}
            for root in self.local_roots:
                for dirpath, _dirs, files in os.walk(root, followlinks=True):
                    for fn in files:
                        p = os.path.join(dirpath, fn)
                        try:
                            idx.setdefault(os.path.getsize(p), []).append(p)
                        except OSError:
                            pass
            self._index = {k: sorted(v) for k, v in idx.items()}
        return self._index


def cli(ctx: Ctx, args: list, log: str) -> dict:
    """Run ``camber <args>`` from this checkout; append the transcript to ``log``."""
    cmd = [sys.executable, "-m", "camber.cli", *[str(a) for a in args]]
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=REPO, env=ctx.env, capture_output=True, text=True)
    dt = time.monotonic() - t0
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"$ camber {' '.join(str(a) for a in args)}\n[exit {p.returncode}, {dt:.1f}s]\n")
        fh.write(p.stdout)
        if p.stderr:
            fh.write("--- stderr ---\n" + p.stderr)
        fh.write("\n")
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "seconds": round(dt, 1)}


def _cli_error(res: dict) -> str:
    tail = (res["stderr"] or res["stdout"]).strip().splitlines()[-25:]
    return "\n".join(tail)


def _parse_json_stdout(text: str):
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    return json.loads(text[start:]) if start >= 0 else None


# --------------------------------------------------------------------------- 1. fetch


def _local_source(ctx: Ctx, f: dict) -> str | None:
    cands = ctx.local_index().get(int(f.get("size") or -1), [])
    if not cands:
        return None
    base = os.path.basename(f["name"])
    named = [c for c in cands if os.path.basename(c) == base]
    return (named or cands)[0]


def step_fetch(ctx: Ctx, entry, subset: str) -> dict:
    """Seed the cache from local copies, then verify through the real fetch path."""
    files = entry.subset_files(subset)
    seeded, missing = {}, []
    for f in files:
        dest = os.path.join(ctx.cache, entry.id, "downloads", *f["name"].split("/"))
        if os.path.isfile(dest):
            seeded[f["name"]] = {"source": "already in cache", "how": "present"}
            continue
        src = _local_source(ctx, f)
        if src is None:
            missing.append(f["name"])
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            os.link(src, dest)
            how = "hard link"
        except OSError:
            shutil.copy2(src, dest)
            how = "copy"
        seeded[f["name"]] = {"source": src, "how": how}
    network = bool(missing and ctx.download_missing)
    rec: dict = {"missing_locally": missing, "network_used": network, "files": {}}
    t0 = time.monotonic()
    try:
        if entry.manual:  # never downloaded: verify the seeded copies like `ingest --from-dir`
            from camber.datasets._ops import adopt_local_files

            ddir = os.path.join(ctx.cache, entry.id, "downloads")
            res = adopt_local_files(entry, ddir, subset=subset, data_dir=ctx.cache)
        else:
            res = ds.fetch(
                entry.id,
                subset=subset,
                data_dir=ctx.cache,
                opener=None if network else _NoNetwork(),
                accept_noncommercial=ctx.accept_noncommercial,
            )
        for f in res.files:
            rec["files"][f["name"]] = {
                "status": "pass",
                "bytes": f["bytes"],
                "sha256": f["sha256"],
                "downloaded": not f["skipped"],
                **seeded.get(f["name"], {}),
            }
        rec["warnings"] = list(res.warnings)
        rec["ok"] = True
    except Exception as exc:  # per-file diagnosis through the same verified download path
        rec["ok"] = False
        rec["error"] = f"{type(exc).__name__}: {exc}"
        for f in files:
            dest = os.path.join(ctx.cache, entry.id, "downloads", *f["name"].split("/"))
            try:
                out = download(
                    f["url"], dest, size=f.get("size"), sha256=f.get("sha256"), opener=_NoNetwork()
                )
                st = {"status": "pass", "bytes": out.bytes, "sha256": out.sha256}
            except ChecksumMismatch as e:
                st = {"status": "fail (checksum)", "detail": str(e)}
            except FetchError:
                st = {"status": "missing (not local; network refused)"}
            rec["files"][f["name"]] = {**st, **seeded.get(f["name"], {})}
    rec["seconds"] = round(time.monotonic() - t0, 1)
    rec["pass"] = sum(1 for v in rec["files"].values() if v["status"] == "pass")
    rec["fail"] = len(files) - rec["pass"]
    return rec


# --------------------------------------------------------------------------- 2. ingest


def _quality(st: ParquetStore, fid: str) -> dict:
    """Per-role stats over every equipment of a facility, plus flags for suspicious series."""
    from camber.units import PERCENT_ROLES

    pct = {r.value for r in PERCENT_ROLES}
    df = st.read_long(facility_id=fid, columns=["ts", "equip", "role", "value"])
    if df.empty:
        return {"empty": True}
    g = df.groupby(["equip", "role"], observed=True)["value"]
    s = g.agg(["size", "count", "nunique", "median", "min", "max"]).reset_index()
    flags: dict = {"empty_series": {}, "flat_series": {}, "percent_out_of_range": {}}
    for row in s.itertuples(index=False):
        role, eq = str(row.role), str(row.equip)
        if row.count == 0:
            flags["empty_series"].setdefault(role, []).append(eq)
        elif row.nunique <= 1:
            flags["flat_series"].setdefault(role, []).append(eq)
        if role in pct and row.count and (row.max > 100.5 or row.min < -0.5):
            flags["percent_out_of_range"].setdefault(role, []).append(
                f"{eq} [{row.min:.3g}, {row.max:.3g}]"
            )
    for k in flags:
        flags[k] = {r: {"n": len(v), "equip": sorted(v)[:8]} for r, v in sorted(flags[k].items())}
    roles = {}
    for role, grp in s.groupby("role", observed=True):
        roles[str(role)] = {
            "equip": int(len(grp)),
            "valid_frac": _r(grp["count"].sum() / max(1, grp["size"].sum()), 3),
            "median_of_medians": _r(grp["median"].median(), 3),
            "min": _r(grp["min"].min(), 3),
            "max": _r(grp["max"].max(), 3),
        }
    ts = df["ts"]
    return {
        "rows_long": int(len(df)),
        "start": str(ts.min()),
        "end": str(ts.max()),
        "days": _r((ts.max() - ts.min()).total_seconds() / 86400, 1),
        "equip": int(df["equip"].nunique()),
        "roles": roles,
        "flags": flags,
    }


def _facilities(st: ParquetStore, dataset_id: str) -> dict:
    return {
        fid: (m.get("dataset") or {})
        for fid, m in sorted(st.facilities_meta().items())
        if (m.get("dataset") or {}).get("dataset_id") == dataset_id
    }


def step_ingest(ctx: Ctx, entry, subset: str, log: str) -> dict:
    extracted = sum(int(f.get("extracted_size") or 0) for f in entry.subset_files(subset))
    est = int((entry.store_bytes(subset) or 0) * 4)
    rec: dict = {"disk": ctx.disk_guard(extracted + est, f"{entry.id} ingest")}
    t0 = time.monotonic()
    msgs: list = []
    res = ds.ingest(
        entry.id,
        ctx.store,
        subset=subset,
        data_dir=ctx.cache,
        force=ctx.force_ingest,
        progress=msgs.append,
        accept_noncommercial=ctx.accept_noncommercial,
    )
    rec["seconds"] = round(time.monotonic() - t0, 1)
    rec["first_ingest_skipped"] = res.skipped
    rec["content_hash"] = res.content_hash
    rec["rows"] = res.rows
    rec["equipment"] = res.equipment
    rec["facilities"] = list(res.facilities)
    rec["warnings"] = list(res.warnings)
    rec["notes"] = list(res.notes)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("[ingest progress]\n" + "\n".join(msgs) + "\n")
    rec["extracted_bytes"] = _dir_bytes(os.path.join(ctx.cache, entry.id, "extracted"))
    st = ParquetStore(ctx.store)
    before = _facilities(st, entry.id)
    # idempotency: the CLI re-ingest must skip on the unchanged content hash
    again = cli(
        ctx,
        [
            "datasets",
            "ingest",
            entry.id,
            "--store",
            ctx.store,
            "--subset",
            subset,
            "--dir",
            ctx.cache,
            "--quiet",
        ],
        log,
    )
    after = _facilities(ParquetStore(ctx.store), entry.id)
    unchanged = all(
        before[f].get("ingested_at") == after.get(f, {}).get("ingested_at") for f in before
    )
    rec["second_ingest"] = {
        "rc": again["rc"],
        "skipped": again["rc"] == 0 and "skipped" in again["stdout"] and unchanged,
        "seconds": again["seconds"],
        "stdout": again["stdout"].strip()[:400],
    }
    if res.skipped:  # a resumed run: take the counts from the registry
        rec["rows"] = sum(int(m.get("rows") or 0) for m in after.values())
        rec["equipment"] = sum(int(m.get("equipment") or 0) for m in after.values())
    fac = {}
    for fid, m in after.items():
        fac[fid] = {
            "rows": m.get("rows"),
            "equipment": m.get("equipment"),
            "store_bytes": _dir_bytes(os.path.join(ctx.store, f"facility_id={fid}")),
            "duplicates": m.get("duplicates") or {},
            "quirks": m.get("quirks") or [],
            "labels": m.get("labels") or {},
            "onsets": m.get("onsets") or {},
        }
    rec["facility"] = fac
    rec["store_bytes"] = sum(v["store_bytes"] for v in fac.values())
    rec["store_bytes_estimate"] = entry.store_bytes(subset)
    if entry.id != "bdg2":
        t1 = time.monotonic()
        rec["quality"] = {fid: _quality(ParquetStore(ctx.store), fid) for fid in fac}
        rec["quality_seconds"] = round(time.monotonic() - t1, 1)
    else:
        rec["quality"] = _bdg2_quality(ParquetStore(ctx.store), fac)
    return rec


def _bdg2_quality(st: ParquetStore, fac: dict) -> dict:
    """A light per-site pass for BDG2 (hundreds of meters): counts only, no per-role table."""
    out = {}
    for fid in fac:
        eq = st.equipment(facility_id=fid).get(fid, {})
        by_cls: dict = {}
        for cls in eq.values():
            by_cls[cls] = by_cls.get(cls, 0) + 1
        out[fid] = {"equip_by_class": dict(sorted(by_cls.items()))}
    return out


# --------------------------------------------------------------------------- 3. config + run


def _rule_names(cfg: dict) -> list:
    return [r if isinstance(r, str) else r["name"] for r in cfg.get("rules") or []]


def _config_equips(ctx: Ctx, cfg: dict) -> list:
    fid = cfg["source"]["facility_id"]
    eqs = ParquetStore(ctx.store).equipment(facility_id=fid).get(fid, {})
    out = []
    for spec in cfg.get("equipment") or []:
        names = spec.get("equip")
        names = {names} if isinstance(names, str) else set(names or [])
        out += [e for e, c in eqs.items() if c == spec["class"] and (not names or e in names)]
    return sorted(set(out))


def _summarize_findings(findings: list, rules: list, equips: list) -> dict:
    by = {}
    sev: dict = {}
    declined, caveats = [], 0
    seen = set()
    for f in findings:
        rule, eq, s = f["rule"], f["equip"], f["severity"]
        by.setdefault(rule, {}).setdefault(s, 0)
        by[rule][s] += 1
        sev[s] = sev.get(s, 0) + 1
        seen.add((rule, eq))
        caveats += len(f.get("caveats") or [])
        if (f.get("metrics") or {}).get("declined"):
            declined.append({"rule": rule, "equip": eq, "summary": f.get("summary", "")[:200]})
    not_eval = [{"rule": r, "equip": e} for r in rules for e in equips if (r, e) not in seen]
    return {
        "n": len(findings),
        "by_severity": dict(sorted(sev.items())),
        "by_rule": {k: dict(sorted(v.items())) for k, v in sorted(by.items())},
        "declined": sorted(declined, key=lambda d: (d["rule"], d["equip"])),
        "not_evaluated": not_eval,
        "caveats": caveats,
    }


def _matrix(findings: list) -> dict:
    """``{equip: {rule: severity}}`` (declined findings read as ``declined``)."""
    out: dict = {}
    for f in findings:
        s = "declined" if (f.get("metrics") or {}).get("declined") else f["severity"]
        out.setdefault(f["equip"], {})[f["rule"]] = s
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def step_run(ctx: Ctx, entry, ddir: str, log: str, *, facility: str | None = None) -> dict:
    """Generate the template config (CLI) and run it (CLI); drift too when the template has it."""
    tag = f"_{facility}" if facility else ""
    cfg_path = os.path.join(ddir, f"config{tag}.json")
    args = ["datasets", "config", entry.id, "--store", ctx.store, "--out", cfg_path]
    if facility:
        args += ["--facility", facility]
    c = cli(ctx, args, log)
    if c["rc"] != 0:
        raise RuntimeError(f"camber datasets config failed:\n{_cli_error(c)}")
    cfg = _read_json(cfg_path)
    run_dir = os.path.join(ddir, f"run{tag}")
    r = cli(ctx, ["run", cfg_path, "--out", run_dir], log)
    if r["rc"] != 0:
        raise RuntimeError(f"camber run failed:\n{_cli_error(r)}")
    fpath = os.path.join(run_dir, "findings.json")
    findings = _read_json(fpath, [])
    rules = _rule_names(cfg)
    rec = {
        "config": cfg_path,
        "findings_path": fpath,
        "seconds": r["seconds"],
        "rules": rules,
        "mv_sections": len(cfg.get("mv") or []),
        "equipment": _config_equips(ctx, cfg),
        "summary": _summarize_findings(findings, rules, _config_equips(ctx, cfg)),
        "matrix": _matrix(findings),
    }
    if cfg.get("drift"):
        d = cli(ctx, ["drift", "run", cfg_path, "--out", os.path.join(ddir, f"drift{tag}")], log)
        rec["drift"] = {"rc": d["rc"], "seconds": d["seconds"], "stdout": d["stdout"][-600:]}
    else:
        rec["drift"] = "no drift section in the template (nothing to run)"
    return rec


# --------------------------------------------------------------------------- 4. score


def _by_fault_type(records: list) -> dict:
    out: dict = {}
    for r in records:
        t = r["truth"] or "fault-free"
        d = out.setdefault(t, {"n": 0, "fired": 0})
        d["n"] += 1
        d["fired"] += bool(r["fired"])
    for t, d in out.items():
        if t == "fault-free":
            d.update(
                {"fp": d["fired"], "tn": d["n"] - d["fired"], "fpr_ci": _ci(d["fired"], d["n"])}
            )
        else:
            d.update(
                {"tp": d["fired"], "fn": d["n"] - d["fired"], "tpr_ci": _ci(d["fired"], d["n"])}
            )
        d["rate"] = _r(d["fired"] / d["n"]) if d["n"] else None
    return dict(sorted(out.items()))


def _compact_rates(r: dict) -> dict:
    keys = ("n", "tp", "fp", "tn", "fn", "tpr", "fpr", "accuracy")
    out = {k: _r(r.get(k)) for k in keys if k in r}
    for k in ("tpr_ci", "fpr_ci"):
        if r.get(k):
            out[k] = [_r(x) for x in r[k]]
    return out


def step_score(ctx: Ctx, entry, run: dict, ingest: dict, log: str) -> dict:
    s = cli(
        ctx,
        [
            "datasets",
            "score",
            entry.id,
            "--store",
            ctx.store,
            "--findings",
            run["findings_path"],
            "--json",
        ],
        log,
    )
    if s["rc"] != 0:
        raise RuntimeError(f"camber datasets score failed:\n{_cli_error(s)}")
    res = _parse_json_stdout(s["stdout"])
    records = res["records"]
    targets = res.get("targets") or {}
    rec = {
        "seconds": s["seconds"],
        "n": res["n"],
        "suite": res["suite"],
        "targets": targets,
        "overall": _compact_rates(res["overall"]),
        "correct_diagnosis": _r(res.get("correct_diagnosis")),
        "per_detector": {k: _compact_rates(v) for k, v in sorted(res["per_detector"].items())},
        "by_fault_type": _by_fault_type(records),
        "records": [
            {"equip": r["equip"], "truth": r["truth"] or "", "fired": r["fired"]} for r in records
        ],
    }
    fam = BENCH_FAMILY.get(entry.id)
    if fam:
        rec["benchmark"] = _bench_compare(entry, records, targets, ingest, fam)
    if entry.id == "lbnl-chiller":
        rec["benchmark"] = _chiller_compare(entry, run)
    return rec


def _bench_compare(entry, records: list, targets: dict, ingest: dict, fam: str) -> dict:
    """Re-score on the benchmark's scenario set (the entry's default runs) vs the baseline."""
    fid = entry.ingest.get("facility")
    dupes = (ingest.get("facility", {}).get(fid) or {}).get("duplicates") or {}
    want = set()
    for run in entry.runs("default"):
        rid = dupes.get(run["id"], run["id"])  # a byte-identical duplicate scores as its canon
        want.add(f"{run['equip']}__{rid.split('__', 1)[-1]}")
    sub = [r for r in records if r["equip"] in want]
    sc = score_records(sub, targets)
    ours = {
        "tpr": _r(sc["overall"]["tpr"]),
        "fpr": _r(sc["overall"]["fpr"]),
        "accuracy": _r(sc["overall"]["accuracy"]),
        "correct_diagnosis": _r(sc["correct_diagnosis"]),
    }
    base = _read_json(LBNL_BASELINE, {})
    theirs = {k: base.get(f"{fam}.{k}") for k in ours}
    return {
        "family": fam,
        "scenarios": sorted(r["equip"] for r in sub),
        "catalog": ours,
        "baseline": theirs,
        "match": all(
            theirs[k] is None or ours[k] is None or abs(ours[k] - theirs[k]) <= 0.02 for k in ours
        ),
        "fired": {r["equip"]: r["fired"] for r in sub},
    }


def _load_lbnl_benchmark():
    path = os.path.join(REPO, "examples", "lbnl_fdd", "benchmark.py")
    spec = importlib.util.spec_from_file_location("_lbnl_benchmark", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _chiller_compare(entry, run: dict) -> dict:
    """Per-detector TPR/FPR with the benchmark's positive-prefix scoring, on the catalog path."""
    bench = _load_lbnl_benchmark()
    csv_of = {
        f"{r['equip']}__{r['id'].split('__', 1)[-1]}": os.path.basename(r["member"])
        for r in entry.runs("full")
    }
    findings = _read_json(run["findings_path"], [])
    out = {}
    for name, det in sorted(bench.CHILLER_DETECTORS.items()):
        tp = fn = fp = tn = declined = 0
        for f in findings:
            if f["rule"] != name or f["equip"] not in csv_of:
                continue
            if (f.get("metrics") or {}).get("declined"):
                declined += 1
                continue
            fired = f["severity"] in FIRED
            if any(csv_of[f["equip"]].startswith(p) for p in det["positive"]):
                tp, fn = tp + fired, fn + (not fired)
            else:
                fp, tn = fp + fired, tn + (not fired)
        out[name] = {
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "tn": tn,
            "declined": declined,
            "tpr": _r(tp / (tp + fn)) if tp + fn else None,
            "fpr": _r(fp / (fp + tn)) if fp + tn else None,
            "tpr_ci": _ci(tp, tp + fn),
            "fpr_ci": _ci(fp, fp + tn),
        }
    return {"family": "chiller (benchmark positive-prefix scoring)", "catalog": out}


# --------------------------------------------------------------------------- 5. fault-free trips


def fault_free_trips(entry, run: dict, ingest: dict) -> list:
    labels = {}
    for m in ingest.get("facility", {}).values():
        labels.update(m.get("labels") or {})
    ff = {eq for eq, lab in labels.items() if not lab}
    out = []
    for f in _read_json(run["findings_path"], []):
        if f["equip"] in ff and f["severity"] in FIRED:
            cls, note = KNOWN_TRIPS.get((entry.id, f["rule"]), ("unclear (unreviewed)", ""))
            out.append(
                {
                    "rule": f["rule"],
                    "equip": f["equip"],
                    "severity": f["severity"],
                    "summary": f.get("summary", "")[:300],
                    "metrics": _scalar_metrics(f.get("metrics") or {}),
                    "classification": cls,
                    "note": note,
                }
            )
    return sorted(out, key=lambda d: (d["equip"], d["rule"]))


# --------------------------------------------------------------------------- 6. reports


def _html_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)


_CITE_HEADERS = {
    "cite",
    "citation",
    "citations",
    "standard",
    "standards",
    "reference",
    "references",
}


def _without_citations(html: str) -> str:
    """``html`` with the cells of citation columns emptied (a table whose header row names the
    column ``Cite`` / ``Standard`` / ``Reference``): those cells cite a source for an action; they
    are never a verdict."""
    from html.parser import HTMLParser

    out: list = []

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.tables: list = []  # per open table: [header texts, column index, in-header-row]
            self.cell: list | None = None  # text of the open <th>/<td>
            self.skip = 0

        def _raw(self):
            return self.get_starttag_text() or ""

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.tables.append([[], -1, False])
            elif self.tables and tag == "tr":
                self.tables[-1][1] = -1
            elif self.tables and tag in ("th", "td"):
                t = self.tables[-1]
                t[1] += 1
                if tag == "th":
                    self.cell = []
                elif t[1] < len(t[0]) and t[0][t[1]] in _CITE_HEADERS:
                    self.skip += 1
            out.append(self._raw())

        def handle_endtag(self, tag):
            if self.tables and tag == "th" and self.cell is not None:
                self.tables[-1][0].append(" ".join("".join(self.cell).split()).lower())
                self.cell = None
            elif self.tables and tag == "td":
                t = self.tables[-1]
                if self.skip and t[1] < len(t[0]) and t[0][t[1]] in _CITE_HEADERS:
                    self.skip -= 1
            elif tag == "table" and self.tables:
                self.tables.pop()
            out.append(f"</{tag}>")

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)
            if not self.skip:
                out.append(data)

        def handle_entityref(self, name):
            self.handle_data(f"&{name};")

        def handle_charref(self, name):
            self.handle_data(f"&#{name};")

    parser = P()
    parser.feed(html)
    parser.close()
    return "".join(out)


def g36_verdicts(html: str) -> list:
    """Unqualified G36 verdicts in a report: G36 mentions outside citations (see
    :func:`_without_citations` and a section citation such as ``G36 §5.16``) with no reference /
    declined qualifier within 220 characters."""
    text = _html_text(_without_citations(html))
    bad = []
    for m in re.finditer(r"G36", text):
        if re.match(r"\s*(§|section\b)", text[m.end() : m.end() + 12]):
            continue  # a standards citation ("G36 §5.16.4"), not a verdict
        win = text[max(0, m.start() - 220) : m.end() + 220].lower()
        if not any(q in win for q in _G36_OK):
            bad.append(text[max(0, m.start() - 120) : m.end() + 120].strip())
    return bad


def check_report(entry, html: str) -> dict:
    text = _html_text(html)
    lic = entry.licence
    doi = entry.dois[0] if entry.dois else None
    g36_bad = g36_verdicts(html)
    return {
        "has_data_source_block": "Data source" in text,
        "licence_shown": lic in text,
        "doi_shown": (doi in text) if doi else None,
        "doi_count": text.count(doi) if doi else None,
        "share_alike_note": ("share-alike" in text.lower()) if "-SA" in lic.upper() else None,
        "research_only_banner": "NON-COMMERCIAL / RESEARCH USE ONLY" in text,
        "g36_mentions": text.count("G36"),
        "g36_unqualified": g36_bad[:5],
    }


def _report_ok(c: dict, *, share_alike: bool, research_only: bool = False) -> bool:
    """The licence block is complete, and the NC/ND banner shows exactly on research-only data."""
    return bool(
        c["has_data_source_block"]
        and c["licence_shown"]
        and c["doi_shown"] is not False
        and (c["share_alike_note"] if share_alike else True)
        and c["research_only_banner"] == research_only
        and not c["g36_unqualified"]
    )


def _recheck_reports(ctx: Ctx, entry, rec: dict) -> None:
    """Re-run :func:`check_report` on a result's built reports (the checks may have changed)."""
    for key, v in ((rec.get("steps") or {}).get("reports") or {}).items():
        html = os.path.join(ctx.ddir(entry.id), "reports", f"{key}.html")
        if v.get("rc") == 0 and os.path.isfile(html):
            with open(html, encoding="utf-8") as fh:
                v["checks"] = check_report(entry, fh.read())
            v["ok"] = _report_ok(
                v["checks"],
                share_alike="-SA" in entry.licence.upper(),
                research_only=entry.research_only,
            )


def _report_pair(entry, labels: dict) -> list:
    ff = sorted(eq for eq, lab in labels.items() if not lab)
    faulted = REPORT_FAULTED.get(entry.id)
    if faulted not in labels:
        faulted = next((eq for eq, lab in sorted(labels.items()) if lab), None)
    return [e for e in (ff[:1] + [faulted]) if e]


def _first_equipment(ctx: Ctx, run: dict) -> list:
    """An unlabelled entry's report subject: the first equipment of the run's facility."""
    cfg = _read_json(run["config"])
    fid = cfg["source"]["facility_id"]
    eqs = ParquetStore(ctx.store).equipment(facility_id=fid).get(fid, {})
    return sorted(eqs)[:1]


RCX_SKIPPED = "RCx not built (--skip-rcx): select_week is slow on long or large runs (#35)"


def step_reports(ctx: Ctx, entry, run: dict, equips: list, ddir: str, log: str) -> dict:
    base = _read_json(run["config"])
    out = {}
    layouts = ("audit",) if entry.id in ctx.skip_rcx else ("rcx", "audit")
    for eq in equips:
        cfg = json.loads(json.dumps(base))
        for spec in cfg.get("equipment") or []:
            spec["equip"] = [eq]
        cpath = os.path.join(ddir, "reports", f"config_{eq}.json")
        _write_json(cpath, cfg)
        for layout in layouts:
            html = os.path.join(ddir, "reports", f"{eq}.{layout}.html")
            r = cli(ctx, ["report", cpath, "--out", html, "--layout", layout], log)
            rec = {"rc": r["rc"], "seconds": r["seconds"], "stdout": r["stdout"].strip()[-300:]}
            if r["rc"] == 0 and os.path.isfile(html):
                with open(html, encoding="utf-8") as fh:
                    body = fh.read()
                rec["bytes"] = len(body.encode("utf-8"))
                rec["checks"] = check_report(entry, body)
                c = rec["checks"]
                rec["ok"] = _report_ok(
                    c, share_alike="-SA" in entry.licence.upper(), research_only=entry.research_only
                )
            else:
                rec["ok"] = False
                rec["error"] = _cli_error(r)
            out[f"{eq}.{layout}"] = rec
    return out


# --------------------------------------------------------------------------- 7. BDG2 M&V


def _mv_rows(findings: list) -> list:
    rows = []
    for f in findings:
        if f["rule"] != "mv_baseline":
            continue
        m = f.get("metrics") or {}
        meter = f["equip"].rsplit("__", 1)[-1]
        rows.append(
            {
                "equip": f["equip"],
                "meter": meter,
                "declined": bool(m.get("declined")),
                "accept": bool(m.get("accept")),
                "cv_rmse": m.get("cv_rmse"),
                "model": m.get("model"),
                "why": f.get("summary", "") if m.get("declined") else "",
            }
        )
    return rows


def _mv_rollup(rows: list) -> dict:
    out: dict = {}
    for meter in sorted({r["meter"] for r in rows} | {"pooled"}):
        rs = rows if meter == "pooled" else [r for r in rows if r["meter"] == meter]
        fitted = [r for r in rs if not r["declined"]]
        acc = sum(r["accept"] for r in fitted)
        cvs = [r["cv_rmse"] for r in fitted if isinstance(r["cv_rmse"], (int, float))]
        cvs = [c for c in cvs if c == c]
        models: dict = {}
        for r in fitted:
            models[r["model"]] = models.get(r["model"], 0) + 1
        out[meter] = {
            "meters": len(rs),
            "declined": len(rs) - len(fitted),
            "fitted": len(fitted),
            "accepted": acc,
            "acceptance_rate": _r(acc / len(fitted)) if fitted else None,
            "acceptance_ci": _ci(acc, len(fitted)),
            "median_cv_rmse": _r(statistics.median(cvs)) if cvs else None,
            "models": dict(sorted(models.items())),
        }
    return out


def step_bdg2(ctx: Ctx, entry, ingest: dict, ddir: str, log: str) -> dict:
    """Template M&V run per site, plus an extended run over every other meter class."""
    template_rows, extended_rows, sites, errors = [], [], {}, []
    for fid in sorted(ingest["facility"]):
        try:
            run = step_run(ctx, entry, ddir, log, facility=fid)
            rows = _mv_rows(_read_json(run["findings_path"], []))
            template_rows += rows
            sites[fid] = {"seconds": run["seconds"], "template": _mv_rollup(rows)["pooled"]}
            cfg = _read_json(run["config"])
            eqs = ParquetStore(ctx.store).equipment(facility_id=fid).get(fid, {})
            have = {c for c in eqs.values()} - {"WEATHER"} - {e["class"] for e in cfg["equipment"]}
            if have:
                ext = json.loads(json.dumps(cfg))
                ext["equipment"] = [{"class": c} for c in sorted(have)]
                period = cfg["mv"][0]["period"]
                ext["mv"] = [
                    {"class": c, "role": "energy_rate", "period": period, "interval": "daily"}
                    for c in sorted(have)
                ]
                ext.pop("report", None)
                epath = os.path.join(ddir, f"config_{fid}_extended.json")
                _write_json(epath, ext)
                edir = os.path.join(ddir, f"run_{fid}_extended")
                r = cli(ctx, ["run", epath, "--out", edir], log)
                if r["rc"] != 0:
                    raise RuntimeError(f"extended M&V run failed:\n{_cli_error(r)}")
                erows = _mv_rows(_read_json(os.path.join(edir, "findings.json"), []))
                extended_rows += erows
                sites[fid]["extended_seconds"] = r["seconds"]
                sites[fid]["extended"] = _mv_rollup(erows)["pooled"]
        except Exception as exc:
            errors.append({"facility": fid, "error": f"{exc}", "traceback": traceback.format_exc()})
    base = _read_json(BDG2_BASELINE, {})
    tmpl = _mv_rollup(template_rows)
    compare = {}
    for meter in ("chilledwater", "electricity", "pooled"):
        ours = tmpl.get(meter) or {}
        compare[meter] = {
            "catalog_rate": ours.get("acceptance_rate"),
            "catalog_ci": ours.get("acceptance_ci"),
            "catalog_n_fitted": ours.get("fitted"),
            "catalog_median_cv_rmse": ours.get("median_cv_rmse"),
            "baseline_rate": base.get(f"{meter}.acceptance_rate"),
            "baseline_ci": [
                base.get(f"{meter}.acceptance_ci_lo"),
                base.get(f"{meter}.acceptance_ci_hi"),
            ],
            "baseline_n": base.get(f"{meter}.n_buildings"),
            "baseline_median_cv_rmse": base.get(f"{meter}.median_cv_rmse"),
        }
    declines: dict = {}
    for r in template_rows + extended_rows:
        if r["declined"]:
            why = r["why"].split("--", 1)[-1].strip()
            why = re.sub(r"\d+", "N", why)
            declines[why] = declines.get(why, 0) + 1
    return {
        "sites": sites,
        "template": tmpl,
        "extended": _mv_rollup(extended_rows),
        "benchmark_compare": compare,
        "decline_reasons": dict(sorted(declines.items())),
        "errors": errors,
    }


# --------------------------------------------------------------------------- per dataset


def sweep_dataset(ctx: Ctx, entry) -> dict:
    subset = ctx.subset if ctx.subset in entry.subsets else "default"
    ddir = ctx.ddir(entry.id)
    os.makedirs(ddir, exist_ok=True)
    log = os.path.join(ddir, "cli.log")
    rec: dict = {
        "id": entry.id,
        "subset": subset,
        "licence": entry.licence,
        "labelled": bool(entry.labeled_faults),
        "started_at": _now(),
        "steps": {},
        "failures": [],
        "timings": {},
    }
    t_all = time.monotonic()

    def step(name, fn, *a, **kw):
        t0 = time.monotonic()
        try:
            out = fn(*a, **kw)
            rec["steps"][name] = out
            return out
        except StopSweep:
            raise
        except Exception as exc:
            rec["failures"].append(
                {
                    "step": name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
            return None
        finally:
            rec["timings"][name] = round(time.monotonic() - t0, 1)

    try:
        fetch = step("fetch", step_fetch, ctx, entry, subset)
        ingest = step("ingest", step_ingest, ctx, entry, subset, log) if fetch else None
        if ingest and entry.id == "bdg2":
            step("bdg2_mv", step_bdg2, ctx, entry, ingest, ddir, log)
            fids = sorted(ingest["facility"])
            fox = "ds-bdg2-fox" if "ds-bdg2-fox" in fids else (fids[0] if fids else None)
            if fox and not ctx.skip_reports:
                step("reports", _bdg2_reports, ctx, entry, fox, ddir, log)
        elif ingest:
            run = step("run", step_run, ctx, entry, ddir, log)
            if run and entry.labeled_faults:
                step("score", step_score, ctx, entry, run, ingest, log)
                step("fault_free_trips", fault_free_trips, entry, run, ingest)
            if run and not ctx.skip_reports:
                labels = {}
                for m in ingest["facility"].values():
                    labels.update(m.get("labels") or {})
                pair = _report_pair(entry, labels) or _first_equipment(ctx, run)
                step("reports", step_reports, ctx, entry, run, pair, ddir, log)
                if entry.id in ctx.skip_rcx:
                    rec["reports_note"] = RCX_SKIPPED
        if not ctx.keep_extracted:
            shutil.rmtree(os.path.join(ctx.cache, entry.id, "extracted"), ignore_errors=True)
        rec["status"] = "done" if not rec["failures"] else "done_with_failures"
    except StopSweep as exc:
        rec["status"] = "stopped"
        rec["failures"].append({"step": "disk", "error": str(exc), "traceback": ""})
    rec["seconds"] = round(time.monotonic() - t_all, 1)
    rec["finished_at"] = _now()
    _write_json(os.path.join(ddir, "result.json"), rec)
    return rec


def _bdg2_reports(ctx: Ctx, entry, fid: str, ddir: str, log: str) -> dict:
    run = {"config": os.path.join(ddir, f"config_{fid}.json")}
    eqs = ParquetStore(ctx.store).equipment(facility_id=fid).get(fid, {})
    pick = []
    for cls in ("CHILLEDWATER_METER", "ELECTRICITY_METER"):
        cands = sorted(e for e, c in eqs.items() if c == cls)
        pick += cands[:1]
    cfg = _read_json(run["config"])
    for spec in cfg["equipment"]:
        spec["equip"] = [e for e in pick if eqs.get(e) == spec["class"]]
    one = os.path.join(ddir, f"config_{fid}_report.json")
    _write_json(one, cfg)
    out = {}
    for layout in ("rcx", "audit"):
        html = os.path.join(ddir, "reports", f"{fid}.{layout}.html")
        os.makedirs(os.path.dirname(html), exist_ok=True)
        r = cli(ctx, ["report", one, "--out", html, "--layout", layout], log)
        rec = {"rc": r["rc"], "seconds": r["seconds"], "equip": pick}
        if r["rc"] == 0 and os.path.isfile(html):
            with open(html, encoding="utf-8") as fh:
                body = fh.read()
            rec["bytes"] = len(body.encode("utf-8"))
            rec["checks"] = check_report(entry, body)
            c = rec["checks"]
            rec["ok"] = _report_ok(c, share_alike="-SA" in entry.licence.upper())
        else:
            rec["ok"] = False
            rec["error"] = _cli_error(r)
        out[f"{fid}.{layout}"] = rec
    return out


# --------------------------------------------------------------------------- benchmarks


def start_benchmarks(ctx: Ctx) -> list:
    """Start the two example benchmarks (read-only on examples/_data) with ``--json``.

    They run concurrently with the sweep; :func:`finish_benchmarks` collects them.
    """
    bdir = os.path.join(ctx.out, "benchmarks")
    os.makedirs(bdir, exist_ok=True)
    jobs = []
    for name, script in (
        ("lbnl", "examples/lbnl_fdd/benchmark.py"),
        ("bdg2", "examples/bdg2/benchmark.py"),
    ):
        dest = os.path.join(bdir, f"{name}.json")
        log = open(os.path.join(bdir, f"{name}.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, script, "--json", dest],
            cwd=REPO,
            env=ctx.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        jobs.append((name, proc, dest, time.monotonic(), log))
    return jobs


def finish_benchmarks(ctx: Ctx, jobs: list) -> dict:
    out = {}
    for name, proc, dest, t0, log in jobs:
        rc = proc.wait()
        log.close()
        out[name] = {
            "rc": rc,
            "seconds": round(time.monotonic() - t0, 1),
            "metrics": _read_json(dest, {}),
        }
    _write_json(os.path.join(ctx.out, "benchmarks", "benchmarks.json"), out)
    return out


# --------------------------------------------------------------------------- summary


def _portfolio_state(ctx: Ctx) -> dict:
    log = os.path.join(ctx.out, "portfolio.log")
    st = cli(ctx, ["portfolio", "status", "--workspace", ctx.workspace, "--json"], log)
    fl = cli(ctx, ["facility", "list", "--workspace", ctx.workspace, "--json"], log)
    au = cli(ctx, ["portfolio", "audit", "--workspace", ctx.workspace, "--json"], log)
    status = _parse_json_stdout(st["stdout"]) if st["rc"] == 0 else None
    facs = _parse_json_stdout(fl["stdout"]) if fl["rc"] == 0 else None
    audit = _parse_json_stdout(au["stdout"]) if au["rc"] == 0 else None
    outputs = {}
    for fid in sorted(facs or {}):
        sh = cli(ctx, ["facility", "show", fid, "--workspace", ctx.workspace, "--json"], log)
        if sh["rc"] == 0:
            man = (_parse_json_stdout(sh["stdout"]) or {}).get("manifest") or {}
            outputs[fid] = len(man.get("files") or {}) + len(man.get("external") or {})
    actions: dict = {}
    for a in audit or []:
        actions[a.get("action")] = actions.get(a.get("action"), 0) + 1
    return {
        "rc": [st["rc"], fl["rc"], au["rc"]],
        "facilities": len(facs or {}),
        "by_state": (status or {}).get("by_state"),
        "unregistered": (status or {}).get("unregistered"),
        "audit_actions": dict(sorted(actions.items())),
        "recorded_outputs": outputs,
    }


def _headline(rec: dict) -> dict:
    """The comparable, deterministic headline of one dataset result."""
    s = rec.get("steps", {})
    f, g, r = s.get("fetch") or {}, s.get("ingest") or {}, s.get("run") or {}
    sc = s.get("score") or {}
    reps = s.get("reports") or {}
    mv = (s.get("bdg2_mv") or {}).get("template") or {}
    return {
        "status": rec.get("status"),
        "fetch_pass": f.get("pass"),
        "fetch_fail": f.get("fail"),
        "rows": g.get("rows"),
        "equipment": g.get("equipment"),
        "store_bytes": g.get("store_bytes"),
        "idempotent": (g.get("second_ingest") or {}).get("skipped"),
        "findings": (r.get("summary") or {}).get("by_severity"),
        "overall": {k: (sc.get("overall") or {}).get(k) for k in ("tpr", "fpr", "accuracy")}
        if sc
        else None,
        "benchmark_match": (sc.get("benchmark") or {}).get("match"),
        "fault_free_trips": sorted(
            f"{t['equip']}:{t['rule']}:{t['severity']}" for t in s.get("fault_free_trips") or []
        ),
        "reports_ok": sorted(k for k, v in reps.items() if v.get("ok")),
        "reports_bad": sorted(k for k, v in reps.items() if not v.get("ok")),
        "mv_acceptance": {k: v.get("acceptance_rate") for k, v in mv.items()} if mv else None,
        "failures": [x["step"] for x in rec.get("failures") or []],
    }


def compare(prev: dict, cur: dict) -> list:
    changes = []
    pd_, cd = prev.get("datasets") or {}, cur.get("datasets") or {}
    for did in sorted(set(pd_) | set(cd)):
        if did not in pd_:
            changes.append(f"{did}: new in this run")
            continue
        if did not in cd:
            changes.append(f"{did}: missing from this run")
            continue
        a, b = _headline(pd_[did]), _headline(cd[did])
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if k == "store_bytes" and va and vb and abs(va - vb) <= 0.05 * va:
                continue
            if va != vb:
                changes.append(
                    f"{did}.{k}: {json.dumps(va, default=str)} -> {json.dumps(vb, default=str)}"
                )
    return changes


def _fmt_rate(x, ci=None) -> str:
    if x is None:
        return "n/a"
    s = f"{x:.0%}"
    if ci and ci[0] is not None:
        s += f" [{ci[0]:.0%}-{ci[1]:.0%}]"
    return s


def _mb(n) -> str:
    if n is None:
        return "n/a"
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def render_md(summary: dict) -> str:
    L = [f"# CAMBER catalog sweep ({summary['subset']} subset)", ""]
    L.append(
        f"camber {summary['camber_version']} at `{summary['git_commit']}`; generated "
        f"{summary['generated_at']}; total runtime {summary['totals']['seconds'] / 60:.1f} min; "
        f"disk used {summary['totals']['disk_gb']:.2f} GB (excluding hard-linked downloads)."
    )
    L.append("")
    L.append(
        "| dataset | status | fetch pass/fail | rows | equip | store | ingest s | idempotent "
        "| findings (fault/warn/info/ok) | score | FF trips | reports ok |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for did, rec in summary["datasets"].items():
        h = _headline(rec)
        s = rec.get("steps", {})
        fs = h["findings"] or {}
        fnd = "/".join(str(fs.get(k, 0)) for k in ("fault", "warn", "info", "ok")) if fs else "-"
        sc = s.get("score")
        score = "-"
        if sc:
            o = sc["overall"]
            score = f"TPR {_fmt_rate(o.get('tpr'))} FPR {_fmt_rate(o.get('fpr'))} (n={sc['n']})"
        if h["mv_acceptance"]:
            score = "M&V pooled " + _fmt_rate(h["mv_acceptance"].get("pooled"))
        reps = s.get("reports") or {}
        L.append(
            f"| {did} | {h['status']} | {h['fetch_pass']}/{h['fetch_fail']} | "
            f"{h['rows'] if h['rows'] is not None else '-'} | {h['equipment'] or '-'} | "
            f"{_mb(h['store_bytes'])} | {(s.get('ingest') or {}).get('seconds', '-')} | "
            f"{h['idempotent']} | {fnd} | {score} | {len(h['fault_free_trips'])} | "
            f"{len(h['reports_ok'])}/{len(reps)} |"
        )
    if summary.get("changes") is not None:
        L += ["", "## Changes vs the previous summary", ""]
        L += [f"- {c}" for c in summary["changes"]] or ["- none"]
    for did, rec in summary["datasets"].items():
        s = rec.get("steps", {})
        L += ["", f"## {did}", ""]
        L.append(
            f"- subset `{rec['subset']}`, licence {rec['licence']}, {rec['seconds']} s total; "
            f"timings {json.dumps(rec['timings'])}"
        )
        f = s.get("fetch") or {}
        if f:
            miss = f.get("missing_locally") or []
            L.append(
                f"- fetch: {f.get('pass')} pass / {f.get('fail')} fail; missing locally: "
                f"{', '.join(miss) or 'none'}; network used: {f.get('network_used')}"
            )
        g = s.get("ingest") or {}
        if g:
            L.append(
                f"- ingest: {g.get('rows')} rows, {g.get('equipment')} equipment, "
                f"{len(g.get('facilities') or [])} facilit(ies), store {_mb(g.get('store_bytes'))}"
                f" (catalog estimate {_mb(g.get('store_bytes_estimate'))}), extracted "
                f"{_mb(g.get('extracted_bytes'))}; second ingest skipped: "
                f"{(g.get('second_ingest') or {}).get('skipped')}"
            )
            for w in g.get("warnings") or []:
                L.append(f"  - warning: {w}")
            for n in g.get("notes") or []:
                L.append(f"  - note: {n}")
            for fid, q in (g.get("quality") or {}).items():
                if "flags" in q:
                    L.append(f"  - {fid}: {q.get('start')} .. {q.get('end')} ({q.get('days')} d)")
                    for kind, roles in q["flags"].items():
                        for role, v in roles.items():
                            L.append(
                                f"    - {kind}: {role} on {v['n']} equip "
                                f"(e.g. {', '.join(v['equip'][:3])})"
                            )
        r = s.get("run") or {}
        if r:
            sm = r["summary"]
            L.append(
                f"- run: rules {r['rules']}; findings {sm['by_severity']}; "
                f"{len(sm['declined'])} declined; {len(sm['not_evaluated'])} not evaluated; "
                f"drift: {r['drift'] if isinstance(r['drift'], str) else r['drift']['rc']}"
            )
            for rule, sev in sm["by_rule"].items():
                L.append(f"  - {rule}: {sev}")
            ne: dict = {}
            for x in sm["not_evaluated"]:
                ne.setdefault(x["rule"], []).append(x["equip"])
            for rule, eqs in ne.items():
                L.append(
                    f"  - not evaluated: {rule} on {len(eqs)} equip (e.g. {', '.join(eqs[:3])})"
                )
        sc = s.get("score")
        if sc:
            o = sc["overall"]
            L.append(
                f"- score (n={sc['n']}, suite {sc['suite']}): TPR "
                f"{_fmt_rate(o.get('tpr'), o.get('tpr_ci'))}, FPR "
                f"{_fmt_rate(o.get('fpr'), o.get('fpr_ci'))}, accuracy "
                f"{_fmt_rate(o.get('accuracy'))}, correct diagnosis "
                f"{_fmt_rate(sc.get('correct_diagnosis'))}"
            )
            for t, d in sc["by_fault_type"].items():
                ci = d.get("tpr_ci") or d.get("fpr_ci")
                L.append(f"  - {t}: {d['fired']}/{d['n']} fired ({_fmt_rate(d['rate'], ci)})")
            for det, c in sc["per_detector"].items():
                L.append(
                    f"  - detector {det}: TPR {_fmt_rate(c.get('tpr'), c.get('tpr_ci'))} "
                    f"FPR {_fmt_rate(c.get('fpr'), c.get('fpr_ci'))} (n={c.get('n')})"
                )
            b = sc.get("benchmark")
            if b:
                L.append(
                    f"  - benchmark ({b['family']}): catalog {b['catalog']} vs baseline "
                    f"{b.get('baseline')}; match: {b.get('match')}"
                )
        trips = s.get("fault_free_trips") or []
        if trips:
            L.append("- fault-free trips:")
            for t in trips:
                L.append(
                    f"  - {t['equip']} {t['rule']} [{t['severity']}] "
                    f"({t['classification']}) {t['summary'][:160]}"
                )
        mv = s.get("bdg2_mv")
        if mv:
            for meter, v in mv["template"].items():
                L.append(
                    f"- M&V template {meter}: {v['accepted']}/{v['fitted']} accepted "
                    f"({_fmt_rate(v['acceptance_rate'], v['acceptance_ci'])}), "
                    f"{v['declined']} declined, median CV(RMSE) {v['median_cv_rmse']}"
                )
            for meter, v in mv["extended"].items():
                L.append(
                    f"- M&V extended {meter}: {v['accepted']}/{v['fitted']} accepted "
                    f"({_fmt_rate(v['acceptance_rate'], v['acceptance_ci'])}), "
                    f"{v['declined']} declined"
                )
            for meter, v in mv["benchmark_compare"].items():
                L.append(
                    f"- vs benchmark {meter}: catalog "
                    f"{_fmt_rate(v['catalog_rate'], v['catalog_ci'])}"
                    f" (n={v['catalog_n_fitted']}) vs baseline "
                    f"{_fmt_rate(v['baseline_rate'], v['baseline_ci'])} (n={v['baseline_n']})"
                )
            for why, n in mv["decline_reasons"].items():
                L.append(f"  - declined x{n}: {why}")
        if rec.get("reports_note"):
            L.append(f"- {rec['reports_note']}")
        for k, v in (s.get("reports") or {}).items():
            c = v.get("checks") or {}
            L.append(
                f"- report {k}: ok={v.get('ok')} rc={v.get('rc')} {v.get('seconds')} s "
                f"{_mb(v.get('bytes'))}; licence {c.get('licence_shown')}, doi "
                f"{c.get('doi_shown')}, G36 mentions {c.get('g36_mentions')} "
                f"(unqualified {len(c.get('g36_unqualified') or [])})"
            )
        for x in rec.get("failures") or []:
            L += [
                f"- **FAILURE in {x['step']}**: {x['error'][:400]}",
                "",
                "```",
                (x.get("traceback") or "").strip()[-3000:],
                "```",
            ]
    bm = summary.get("benchmarks")
    if bm:
        L += ["", "## Example benchmarks (run live)", ""]
        for k, v in bm.items():
            L.append(f"- {k}: exit {v['rc']}, {v['seconds']} s, {len(v['metrics'])} metrics")
    pf = summary.get("portfolio")
    if pf:
        L += ["", "## Portfolio workspace", "", f"- {json.dumps(pf, sort_keys=True)}"]
    return "\n".join(L) + "\n"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return "?"


def build_summary(ctx: Ctx, ids: list, *, benchmarks=None, prev=None, seconds=0.0) -> dict:
    results = {}
    for did in ids:
        rec = _read_json(os.path.join(ctx.ddir(did), "result.json"))
        if rec is not None:
            _recheck_reports(ctx, ds.get(did), rec)
            for t in (rec.get("steps") or {}).get("fault_free_trips") or []:
                cls, note = KNOWN_TRIPS.get((did, t["rule"]), ("unclear (unreviewed)", ""))
                t["classification"], t["note"] = cls, note
            results[did] = rec
    disk = _dir_bytes(ctx.out, unique_only=True)
    summary = {
        "schema": SCHEMA,
        "camber_version": __version__,
        "git_commit": _git_commit(),
        "subset": ctx.subset,
        "generated_at": _now(),
        "datasets": results,
        "totals": {
            "seconds": round(sum(r.get("seconds", 0) for r in results.values()), 1),
            "this_invocation_seconds": round(seconds, 1),
            "disk_gb": round(disk / 1e9, 3),
            "store_bytes": _dir_bytes(ctx.store),
            "cache_unique_bytes": _dir_bytes(ctx.cache, unique_only=True),
            "free_gb": round(_free_gb(ctx.out), 1),
        },
        "benchmarks": benchmarks
        or _read_json(os.path.join(ctx.out, "benchmarks", "benchmarks.json")),
        "portfolio": _portfolio_state(ctx) if os.path.isdir(ctx.workspace) else None,
    }
    if prev is not None:
        summary["changes"] = compare(prev, summary)
    return summary


# --------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="sweep output dir (cache, workspace, summaries)")
    ap.add_argument(
        "--local-root",
        action="append",
        help="directory holding local copies of the catalog files (repeatable; matched by size, "
        "then by file name)",
    )
    ap.add_argument("--data-dir", help="CAMBER_DATA_DIR to seed (default: <out>/cache)")
    ap.add_argument("--subset", choices=("full", "default"), default="full")
    ap.add_argument("--only", help="comma-separated dataset ids (default: every catalog entry)")
    ap.add_argument("--skip-done", action="store_true", help="skip datasets already completed")
    ap.add_argument(
        "--download-missing",
        action="store_true",
        help="let fetch download files that have no local copy (default: refuse the network)",
    )
    ap.add_argument("--keep-extracted", action="store_true", help="keep extracted CSVs")
    ap.add_argument("--force-ingest", action="store_true", help="re-ingest even if unchanged")
    ap.add_argument(
        "--accept-noncommercial",
        action="store_true",
        help="acknowledge research-only (NC/ND) licences so those entries are swept too "
        "(recorded in the sweep cache's acknowledgements.json); without it they fail the fetch "
        "step with the licence-gate error",
    )
    ap.add_argument("--min-free-gb", type=float, default=40.0, help="stop below this free disk")
    ap.add_argument("--skip-reports", action="store_true")
    ap.add_argument(
        "--skip-rcx",
        metavar="IDS",
        default="",
        help="comma-separated dataset ids whose reports skip the RCx layout (audit only): RCx's "
        "select_week is slow on long or large runs (#35)",
    )
    ap.add_argument(
        "--run-benchmarks",
        action="store_true",
        help="also run examples/lbnl_fdd + examples/bdg2 benchmark.py --json for comparison",
    )
    ap.add_argument("--compare", metavar="SUMMARY", help="previous summary.json to diff against")
    ap.add_argument(
        "--summarize-only", action="store_true", help="rebuild summary.json/.md from results"
    )
    args = ap.parse_args(argv)

    ctx = Ctx(args)
    os.makedirs(ctx.out, exist_ok=True)
    entries = [ds.get(e.id) for e in ds.catalog()]
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        entries = [ds.get(i) for i in want]
    ids = [e.id for e in ds.catalog()]
    t0 = time.monotonic()
    benchmarks = None
    if not args.summarize_only:
        from camber.portfolio import Portfolio

        Portfolio.init(ctx.workspace)
        jobs = start_benchmarks(ctx) if args.run_benchmarks else []
        for e in entries:
            prev = _read_json(os.path.join(ctx.ddir(e.id), "result.json"))
            if (
                args.skip_done
                and prev
                and prev.get("status") == "done"
                and prev.get("subset") == (ctx.subset if ctx.subset in e.subsets else "default")
            ):
                print(f"{e.id}: done earlier -- skipped", flush=True)
                continue
            print(f"{e.id}: sweeping ({_free_gb(ctx.out):.0f} GB free)", flush=True)
            rec = sweep_dataset(ctx, e)
            print(
                f"{e.id}: {rec['status']} in {rec['seconds']} s; {len(rec['failures'])} failure(s)",
                flush=True,
            )
            if rec["status"] == "stopped":
                print(rec["failures"][-1]["error"], flush=True)
                break
        if jobs:
            benchmarks = finish_benchmarks(ctx, jobs)
    prev = _read_json(args.compare) if args.compare else None
    summary = build_summary(
        ctx, ids, benchmarks=benchmarks, prev=prev, seconds=time.monotonic() - t0
    )
    _write_json(os.path.join(ctx.out, "summary.json"), summary)
    with open(os.path.join(ctx.out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(summary))
    print(f"wrote {os.path.join(ctx.out, 'summary.json')} and summary.md")
    for c in summary.get("changes") or []:
        print(f"  change: {c}")
    bad = [d for d, r in summary["datasets"].items() if r.get("status") != "done"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
