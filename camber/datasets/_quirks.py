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
``{"columns": [...], "from": unit}`` (to IP, see :mod:`._units`) · ``annotate`` (no-op).

A quirk may carry ``"runs": [glob, ...]`` to apply only to matching run names. Anything outside this
closed set is rejected when the catalog is validated, so a catalog can never smuggle in code.
"""

from __future__ import annotations

import fnmatch

import pandas as pd

from ._units import canonical_unit, convert_series

OPS = frozenset({"swap", "rename", "mask", "drop", "scale", "convert", "annotate"})
ACTIONS = frozenset({"fix", "annotate"})

__all__ = ["OPS", "ACTIONS", "validate_quirk", "quirk_columns", "applies_to", "apply_quirks"]


def _cols(q: dict) -> list:
    if q.get("op") == "rename":
        return [q.get("from"), q.get("to")]
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
    if op in ("mask", "drop", "scale", "convert") and not _cols(q):
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


def _mask(df: pd.DataFrame, q: dict) -> pd.DataFrame:
    cond = pd.Series(True, index=df.index)
    if "before" in q:
        cond &= df.index < pd.Timestamp(q["before"])
    if "after" in q:
        cond &= df.index > pd.Timestamp(q["after"])
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
        elif op == "scale":
            for c in cols:
                if c in out.columns:
                    out[c] = out[c] * float(q.get("factor", 1.0)) + float(q.get("offset", 0.0))
        elif op == "convert":
            for c in cols:
                if c in out.columns:
                    out[c] = convert_series(out[c], q["from"], delta=bool(q.get("delta")))
    return out, notes
