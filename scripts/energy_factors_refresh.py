"""Validate, re-pin and document the energy conversion factor sets (maintainer tool).

    python scripts/energy_factors_refresh.py --validate                     # every bundled set
    python scripts/energy_factors_refresh.py --validate new_set.json        # a draft file
    python scripts/energy_factors_refresh.py --check energy_star_thermal_2015
    python scripts/energy_factors_refresh.py --check energy_star_thermal_2015 --local ref.pdf
    python scripts/energy_factors_refresh.py --pin energy_star_thermal_2015 --local ref.pdf \\
        --edition 2015-08 --write
    python scripts/energy_factors_refresh.py --docs --write                  # regenerate the table

``--check`` hashes the source document (a ``--local`` copy, or a verified HTTPS download of the
set's ``source.url``) and compares it with the pinned sha256. ``--pin`` records the new sha256,
today's ``retrieved`` date and, with ``--edition``, the edition. ``--docs`` regenerates the
reference tables in docs/ENERGY-FACTORS.md. Nothing is written without ``--write``.

Checklist: adding or updating a factor set
------------------------------------------
1. Get the publisher's document and record its URL, edition and retrieval date. Check the terms
   (a U.S. Government work is public domain; anything else needs a licence that allows
   redistribution of the numbers).
2. **New edition of an existing set:** a new file with a new name (``<source>_<year>.json``), so
   results that cite the old set stay reproducible. Never edit old numbers in place; only a
   transcription error in the old file is corrected, with a CHANGELOG line.
3. Transcribe every factor from the document itself: the unit label exactly as printed
   (``input_unit``), its normalised ``unit_key`` (see ``camber.energy_factors.UNIT_KEYS``), the
   multiplier as a number and as printed (``multiplier_text``), the heat content (``value``,
   ``value_text``, the printed ``unit`` and a ``unit_key`` such as ``Btu/ft3`` or
   ``MMBtu/gal_US``), and the footnote ids. Record the set's "M" convention in
   ``conventions.M`` (``thousand`` or ``million``).
4. ``--validate`` the file. A multiplier that disagrees with its own heat content beyond the
   printed precision is a transcription error to fix, or a source discrepancy to mark
   (``"consistency": {"status": "source_discrepancy", "note": ...}``) and keep as printed.
5. ``--pin <name> --local <document> --write`` to record the sha256 and retrieval date.
6. Add the hand-checked rows to ``tests/test_energy_factors.py``, run ``--docs --write``, and
   note the set in docs/UNITS.md and the CHANGELOG.

A new *physical* unit (one not in ``UNIT_KEYS``) is the only change that needs code.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from camber import energy_factors as ef  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "camber", "energy_factors")
DOCS = os.path.join(ROOT, "docs", "ENERGY-FACTORS.md")
BEGIN, END = "<!-- BEGIN GENERATED FACTOR TABLES -->", "<!-- END GENERATED FACTOR TABLES -->"


def set_path(name: str) -> str:
    """The JSON file of a bundled set."""
    return os.path.join(PKG, f"{name}.json")


def source_sha256(url: str, local: str | None) -> str:
    """The sha256 of the source document, from ``local`` or a verified HTTPS download."""
    from camber.datasets._fetch import download, sha256_file

    if local:
        return sha256_file(local)
    with tempfile.TemporaryDirectory(prefix="camber-factors-") as tmp:
        return download(url, os.path.join(tmp, "source")).sha256


def pin_text(text: str, *, sha256: str, retrieved: str, edition: str | None) -> str:
    """``text`` (a set's JSON) with its source sha256 / retrieved / edition replaced in place, so
    the file's one-entry-per-line layout is kept."""
    for key, val in (("sha256", sha256), ("retrieved", retrieved), ("edition", edition)):
        if val is None:
            continue
        text, n = re.subn(rf'("{key}": )"[^"]*"', rf'\g<1>"{val}"', text, count=1)
        if n != 1:
            raise ValueError(f"no source.{key} field to replace")
    return text


def _source(name: str) -> dict:
    """The pinned source block of a set: a conversion set's, or an ``eui_reference`` set's (a
    ``price_band`` set cites several sources and has no single document to pin)."""
    if name in ef.factor_sets():
        return ef.get_factor_set(name).source
    ref = ef.get_reference_set(name)
    if "source" not in ref.doc:
        sys.exit(f"{name} is a {ref.kind} set with no single pinned source document")
    return ref.doc["source"]


def docs_text(current: str) -> str:
    """docs/ENERGY-FACTORS.md with its generated block rebuilt from every bundled set."""
    body = "\n".join(ef.reference_markdown(n) for n in ef.factor_sets())
    head, rest = current.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{body}\n{END}{tail}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--validate", nargs="*", metavar="FILE", help="validate files (or all sets)")
    ap.add_argument("--check", metavar="NAME", help="compare the source's sha256 with the pin")
    ap.add_argument("--pin", metavar="NAME", help="record the source's sha256 and today's date")
    ap.add_argument("--local", metavar="PATH", help="a local copy of the source document")
    ap.add_argument("--edition", help="with --pin: the new edition (YYYY, YYYY-MM or YYYY-MM-DD)")
    ap.add_argument("--docs", action="store_true", help="regenerate docs/ENERGY-FACTORS.md")
    ap.add_argument("--write", action="store_true", help="write the changes")
    a = ap.parse_args(argv)
    status = 0
    if a.validate is not None:
        files = a.validate or [set_path(n) for n in ef.factor_sets(None)]
        for f in files:
            with open(f, encoding="utf-8") as fh:
                problems = ef.validate_factor_set(json.load(fh))
            print(f"{os.path.basename(f)}: {'ok' if not problems else 'INVALID'}")
            for p in problems:
                print(f"  {p}")
            status |= bool(problems)
    for name in filter(None, (a.check, a.pin)):
        src = _source(name)
        got = source_sha256(src["url"], a.local)
        same = got == src["sha256"]
        verdict = "(unchanged)" if same else "(CHANGED: re-transcribe and bump the edition)"
        print(f"{name}: pinned {src['sha256']}\n{' ' * len(name)}  source {got}  {verdict}")
        if a.pin:
            path = set_path(name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            new = pin_text(
                text, sha256=got, retrieved=_dt.date.today().isoformat(), edition=a.edition
            )
            if ef.validate_factor_set(json.loads(new)):
                sys.exit("the re-pinned set is invalid")
            if a.write:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
                print(f"wrote {os.path.relpath(path, ROOT)}")
        elif not same:
            status = 1
    if a.docs:
        with open(DOCS, encoding="utf-8") as fh:
            cur = fh.read()
        new = docs_text(cur)
        print("docs/ENERGY-FACTORS.md: " + ("unchanged" if new == cur else "out of date"))
        if a.write and new != cur:
            with open(DOCS, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote docs/ENERGY-FACTORS.md")
    return status


if __name__ == "__main__":
    sys.exit(main())
