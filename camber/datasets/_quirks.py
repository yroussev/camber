"""Declared dataset quirks: a closed set of operations on raw source columns.

Open datasets carry mistakes. The catalog links each dataset exactly as its publisher provides
it and *describes* every problem it knows of in the published data as a **data issue** on the
entry (the columns, the evidence, the documentation it contradicts and how CAMBER handles it --
see :mod:`._catalog`). A quirk is the mechanical part of that handling, declared on the entry's
ingest spec with an ``action`` and the ``issue`` it belongs to:

* ``"fix"`` -- an error the ingester corrects before mapping (e.g. the LBNL chiller plant's outdoor
  wet-bulb and dry-bulb columns are exported swapped). ``camber datasets ingest --no-corrections``
  skips every fix, so a learner can compare the published data with the corrected one.
* ``"annotate"`` -- a problem left in place on purpose (a copied point, a sensor floor, a unit the
  publisher did not document): it is recorded in the facility's provenance and never changes the
  data.

Operations (``op``), all on raw source columns before the point -> role mapping:

``swap`` ``{"columns": [a, b]}`` · ``rename`` ``{"from": a, "to": b}`` · ``mask``
``{"columns": [...], "before"/"after": ts, "lt"/"gt"/"eq": x}`` (matching values -> NaN) · ``drop``
``{"columns": [...]}`` · ``scale`` ``{"columns": [...], "factor": k, "offset": c}`` · ``convert``
``{"columns": [...], "from": unit}`` (to IP, see :mod:`._units`) · ``remap`` ``{"map": {source:
target, ...}, "before"/"after": ts}`` (inside the time window each target takes its source's
value, all moves at once, and a source that is no target is emptied: a header that names the wrong
columns from some date on) · ``fill`` ``{"columns": [...], "window_days": d, "max_run": n}`` (a
missing value takes the median of the same clock time on the days within ``d`` either side, in runs
of at most ``n`` consecutive missing rows: a short meter dropout, never a long outage) ·
``annotate`` (no-op).

A quirk may carry ``"runs": [glob, ...]`` to apply only to matching run names. Anything outside this
closed set is rejected when the catalog is validated, so a catalog can never smuggle in code.
"""

from __future__ import annotations

import fnmatch

import pandas as pd

from ._units import canonical_unit, convert_series

OPS = frozenset({"swap", "rename", "mask", "drop", "scale", "convert", "remap", "fill", "annotate"})
ACTIONS = frozenset({"fix", "annotate"})

__all__ = ["OPS", "ACTIONS", "validate_quirk", "quirk_columns", "applies_to", "apply_quirks"]


def _cols(q: dict) -> list:
    if q.get("op") == "rename":
        return [q.get("from"), q.get("to")]
    if q.get("op") == "remap":
        m = q.get("map")
        pairs = m if isinstance(m, dict) else {}
        return list(dict.fromkeys([*pairs.keys(), *pairs.values()]))
    return list(q.get("columns") or [])


def validate_quirk(q) -> list:
    """Problems with one quirk spec (empty list when valid)."""
    if not isinstance(q, dict):
        return ["quirk must be an object"]
    errs = []
    op, action = q.get("op"), q.get("action")
    if op not in OPS:
        errs.append(f"unknown quirk op {op!r}")
    if action not in ACTIONS:
        errs.append(f"quirk action must be 'fix' or 'annotate', got {action!r}")
    if not str(q.get("note") or "").strip():
        errs.append(f"quirk {op!r} needs a 'note' explaining it")
    if op == "annotate" and action == "fix":
        errs.append("an 'annotate' op cannot be a fix")
    if op == "swap" and len(_cols(q)) != 2:
        errs.append("swap needs exactly two columns")
    if op == "rename" and not (q.get("from") and q.get("to")):
        errs.append("rename needs 'from' and 'to'")
    if op == "remap":
        m = q.get("map")
        if not isinstance(m, dict) or not m:
            errs.append("remap needs a non-empty 'map' {source: target}")
        elif len(set(m.values())) != len(m):
            errs.append("remap targets must be distinct")
        if not any(k in q for k in ("before", "after")):
            errs.append("remap needs a time window (before/after)")
    if op == "fill":
        for k in ("window_days", "max_run"):
            v = q.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                errs.append(f"fill needs a positive integer {k!r}")
    if op in ("mask", "drop", "scale", "convert", "fill") and not _cols(q):
        errs.append(f"{op} needs 'columns'")
    if op == "mask" and not any(k in q for k in ("before", "after", "lt", "gt", "eq")):
        errs.append("mask needs a condition (before/after/lt/gt/eq)")
    if op == "scale" and not isinstance(q.get("factor", 1.0), (int, float)):
        errs.append("scale factor must be a number")
    if op == "convert":
        try:
            canonical_unit(q.get("from", "?"))
        except ValueError as e:
            errs.append(str(e))
    return errs


def quirk_columns(quirks) -> set:
    """Raw columns the *fix* quirks touch (so a column-pruned read keeps them)."""
    out: set = set()
    for q in quirks or []:
        if q.get("action") == "fix":
            out.update(c for c in _cols(q) if c)
    return out


def applies_to(q: dict, run: str | None) -> bool:
    """Whether quirk ``q`` applies to the run named ``run`` (no ``runs`` list -> every run)."""
    pats = q.get("runs")
    if not pats or run is None:
        return True
    return any(fnmatch.fnmatchcase(run, p) for p in pats)


def _need(df: pd.DataFrame, cols, op: str) -> bool:
    """True if every column is present; False if none are; ValueError if only some are."""
    present = [c for c in cols if c in df.columns]
    if len(present) == len(cols):
        return True
    if not present:
        return False
    missing = sorted(set(cols) - set(present))
    raise ValueError(f"quirk {op}: column(s) {missing} missing while {present} are present")


def _window(df: pd.DataFrame, q: dict) -> pd.Series:
    """The rows inside a quirk's ``before`` / ``after`` window (every row when it has neither)."""
    cond = pd.Series(True, index=df.index)
    if "before" in q:
        cond &= df.index < pd.Timestamp(q["before"])
    if "after" in q:
        cond &= df.index > pd.Timestamp(q["after"])
    return cond


def _mask(df: pd.DataFrame, q: dict) -> pd.DataFrame:
    cond = _window(df, q)
    for c in _cols(q):
        if c not in df.columns:
            continue
        m = cond.copy()
        if "lt" in q:
            m &= df[c] < q["lt"]
        if "gt" in q:
            m &= df[c] > q["gt"]
        if "eq" in q:
            m &= df[c] == q["eq"]
        df.loc[m, c] = float("nan")
    return df


def _remap(df: pd.DataFrame, q: dict) -> pd.DataFrame:
    """Inside the window, move every source column's values to its target (simultaneously)."""
    moves = {s: t for s, t in q["map"].items() if s in df.columns}
    if not moves:
        return df
    win = _window(df, q).to_numpy()
    snap = {s: df[s].to_numpy(copy=True) for s in moves}
    for t in moves.values():
        if t not in df.columns:
            df[t] = float("nan")
    for s in moves:  # a source that is not also a target has moved out: empty it in the window
        if s not in moves.values():
            df.loc[win, s] = float("nan")
    for s, t in moves.items():
        col = pd.to_numeric(df[t], errors="coerce").to_numpy(dtype="float64", copy=True)
        col[win] = pd.to_numeric(pd.Series(snap[s][win]), errors="coerce").to_numpy("float64")
        df[t] = col
    return df


def _fill(df: pd.DataFrame, q: dict) -> pd.DataFrame:
    """Fill short runs of missing values with the median of the same clock time on nearby days."""
    days, max_run = int(q["window_days"]), int(q["max_run"])
    if df.empty:
        return df
    clock = df.index - df.index.normalize()
    day = df.index.normalize()
    for c in _cols(q):
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        miss = s.isna().to_numpy()
        if not miss.any():
            continue
        run_id = (~miss).cumsum()  # consecutive missing rows share the id of the row before them
        run_len = pd.Series(miss.astype(int)).groupby(run_id).transform("sum").to_numpy()
        todo = miss & (run_len <= max_run)
        if not todo.any():
            continue
        table = pd.DataFrame({"v": s.to_numpy(), "day": day, "clock": clock})
        wide = table.pivot_table(index="day", columns="clock", values="v", aggfunc="first")
        full = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
        wide = wide.reindex(full)
        med = wide.rolling(2 * days + 1, center=True, min_periods=1).median()
        pos = [(d, k) for d, k in zip(day[todo], clock[todo])]
        vals = [med.at[d, k] if k in med.columns else float("nan") for d, k in pos]
        filled = s.to_numpy(dtype="float64", copy=True)
        filled[todo] = vals
        df[c] = filled
    return df


def apply_quirks(df: pd.DataFrame, quirks, *, run: str | None = None, corrections: bool = True):
    """Apply the ``fix`` quirks to a raw frame; return ``(frame, notes)``.

    ``notes`` lists every quirk that applies to this run -- fixes *and* annotations -- as
    ``"<action>: <note>"`` for the provenance record. With ``corrections=False`` no fix is applied
    (the published data is kept as-is) and each skipped fix is noted as ``"fix skipped: <note>"``.
    The input frame is not modified.
    """
    out = df.copy()
    notes = []
    for q in quirks or []:
        if not applies_to(q, run):
            continue
        if q.get("action") == "fix" and not corrections:
            notes.append(f"fix skipped: {q.get('note', '')}")
            continue
        notes.append(f"{q.get('action')}: {q.get('note', '')}")
        if q.get("action") != "fix":
            continue
        op = q["op"]
        cols = _cols(q)
        if op == "swap":
            if _need(out, cols, op):
                a, b = cols
                out[[a, b]] = out[[b, a]].to_numpy()
        elif op == "rename":
            if q["from"] in out.columns:
                out = out.rename(columns={q["from"]: q["to"]})
        elif op == "drop":
            out = out.drop(columns=[c for c in cols if c in out.columns])
        elif op == "mask":
            out = _mask(out, q)
        elif op == "remap":
            out = _remap(out, q)
        elif op == "fill":
            out = _fill(out, q)
        elif op == "scale":
            for c in cols:
                if c in out.columns:
                    out[c] = out[c] * float(q.get("factor", 1.0)) + float(q.get("offset", 0.0))
        elif op == "convert":
            for c in cols:
                if c in out.columns:
                    out[c] = convert_series(out[c], q["from"], delta=bool(q.get("delta")))
    return out, notes
