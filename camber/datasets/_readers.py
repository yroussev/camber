"""Tabular readers for catalog ingest: CSV in the core, Excel through the ``xlsx`` extra.

The ingest adapters read every source table through :func:`read_table`, which picks the reader by
file extension:

* ``.csv`` / ``.txt`` / ``.tsv`` -- :func:`pandas.read_csv` (core);
* ``.xlsx`` / ``.xlsm`` -- :func:`pandas.read_excel` with ``openpyxl``, the ``xlsx`` extra
  (``pip install "camber-toolkit[xlsx]"``);
* ``.xls`` (legacy BIFF) -- :func:`pandas.read_excel` with ``xlrd``, which is **not** part of the
  extra: no catalog entry needs a legacy workbook (the one published beside a catalog dataset, a
  steady-state summary appendix, is not time-series data and is not ingested). Install ``xlrd``
  yourself to read one.

Both Excel engines are imported lazily, only when such a file is read, and a missing engine
raises :class:`MissingExtra` naming the exact install command. ``usecols`` (a column predicate)
prunes the read the same way for every format; ``sheet`` names a worksheet (default: the first).
"""

from __future__ import annotations

import importlib
import os

import pandas as pd

#: catalog ``requires_extras`` name -> (module it needs, install hint)
EXTRAS = {
    "xlsx": ("openpyxl", 'pip install "camber-toolkit[xlsx]"'),
    "brick": ("rdflib", 'pip install "camber-toolkit[brick]"'),
}
EXCEL_EXTS = (".xlsx", ".xlsm")
LEGACY_EXCEL_EXTS = (".xls",)
TEXT_EXTS = (".csv", ".txt", ".tsv")


class MissingExtra(ImportError):
    """An optional dependency a dataset needs is not installed (the message says how to add it)."""


def _import(module: str, hint: str, why: str):
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise MissingExtra(f"{why} needs the optional package {module!r}: {hint}") from e


def require_extras(extras, *, what: str) -> None:
    """Raise :class:`MissingExtra` unless each named extra's module imports (``what``: who asks)."""
    for name in extras or ():
        if name not in EXTRAS:
            raise ValueError(f"{what}: unknown extra {name!r} (known: {sorted(EXTRAS)})")
        module, hint = EXTRAS[name]
        _import(module, hint, f"{what} (extra {name!r})")


def needs_extra(filename: str) -> str | None:
    """The extra a file's format needs to be read (``"xlsx"`` for a workbook), else ``None``."""
    ext = os.path.splitext(str(filename))[1].lower()
    return "xlsx" if ext in EXCEL_EXTS + LEGACY_EXCEL_EXTS else None


def read_table(path: str, *, usecols=None, sheet=None, **kw) -> pd.DataFrame:
    """Read one source table by extension (see the module docstring).

    ``usecols`` is a predicate on column names (or a list); ``sheet`` a worksheet name or index for
    a workbook (ignored for text files). Extra keyword arguments go to the pandas reader.
    """
    ext = os.path.splitext(str(path))[1].lower()
    if ext in EXCEL_EXTS:
        _import("openpyxl", EXTRAS["xlsx"][1], f"reading {os.path.basename(path)}")
        return pd.read_excel(
            path, sheet_name=0 if sheet is None else sheet, usecols=usecols, engine="openpyxl", **kw
        )
    if ext in LEGACY_EXCEL_EXTS:
        _import(
            "xlrd",
            'pip install "xlrd>=2" (legacy .xls is not part of the xlsx extra)',
            f"reading {os.path.basename(path)}",
        )
        return pd.read_excel(
            path, sheet_name=0 if sheet is None else sheet, usecols=usecols, engine="xlrd", **kw
        )
    sep = "\t" if ext == ".tsv" else ","
    return pd.read_csv(path, usecols=usecols, sep=kw.pop("sep", sep), **kw)
