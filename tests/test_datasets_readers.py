"""Tabular readers for catalog ingest: CSV core, .xlsx via the ``xlsx`` extra, lazy + actionable."""

import importlib
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.datasets import _readers  # noqa: E402
from camber.datasets._readers import (  # noqa: E402
    MissingExtra,
    needs_extra,
    read_table,
    require_extras,
)


def _frame():
    return pd.DataFrame(
        {"Datetime": ["2020-01-01 00:00", "2020-01-01 00:15"], "A": [1.0, 2.0], "B": [3, 4]}
    )


def test_csv_and_tsv_with_a_column_predicate(tmp_path):
    p = tmp_path / "t.csv"
    _frame().to_csv(p, index=False)
    got = read_table(str(p), usecols=lambda c: c != "B")
    assert list(got.columns) == ["Datetime", "A"]
    t = tmp_path / "t.tsv"
    _frame().to_csv(t, index=False, sep="\t")
    assert list(read_table(str(t)).columns) == ["Datetime", "A", "B"]


def test_xlsx_reads_the_named_or_first_sheet(tmp_path):
    pytest.importorskip("openpyxl")
    p = tmp_path / "w.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as xw:
        pd.DataFrame({"x": [0]}).to_excel(xw, sheet_name="notes", index=False)
        _frame().to_excel(xw, sheet_name="data", index=False)
    assert list(read_table(str(p)).columns) == ["x"]
    got = read_table(str(p), sheet="data", usecols=lambda c: c in ("Datetime", "B"))
    assert list(got.columns) == ["Datetime", "B"] and got["B"].tolist() == [3, 4]


def _without(monkeypatch, module):
    real = importlib.import_module

    def fake(name, *a, **k):
        if name == module:
            raise ImportError(f"No module named {module!r}")
        return real(name, *a, **k)

    monkeypatch.setattr(_readers.importlib, "import_module", fake)


def test_missing_openpyxl_says_how_to_install_the_extra(tmp_path, monkeypatch):
    _without(monkeypatch, "openpyxl")
    with pytest.raises(MissingExtra, match=r'pip install "camber-toolkit\[xlsx\]"'):
        read_table(str(tmp_path / "w.xlsx"))
    with pytest.raises(MissingExtra, match="dataset x"):
        require_extras(["xlsx"], what="dataset x")
    assert issubclass(MissingExtra, ImportError)


def test_legacy_xls_needs_xlrd_which_is_not_in_the_extra(tmp_path, monkeypatch):
    _without(monkeypatch, "xlrd")
    with pytest.raises(MissingExtra, match="not part of the xlsx extra"):
        read_table(str(tmp_path / "old.xls"))


def test_extras_helpers():
    assert needs_extra("a/b.XLSX") == "xlsx" and needs_extra("x.xls") == "xlsx"
    assert needs_extra("x.csv") is None
    with pytest.raises(ValueError, match="unknown extra"):
        require_extras(["nope"], what="t")
    require_extras([], what="t")
