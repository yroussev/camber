"""The M&V names registry (0.105, #163): every model kind, method and degree-day kind has a row,
every row's callable and citations resolve, labels and the generated docs table are current."""

import importlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from camber import config as C  # noqa: E402
from camber.mandv import basetemp, methods, models, sep  # noqa: E402
from camber.mandv import names as N  # noqa: E402
from camber.references import MV_REFERENCES, REFERENCES  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_kind_method_and_dd_kind_has_a_row():
    want = set(models._FITTERS) | set(basetemp.DD_KINDS) | set(methods.METHODS)
    want |= set(C._MV_METHODS) | set(sep.SEP_METHODS)
    want |= {"towt", "degreeday", "multivariable", "categorical", "option_a"}
    want |= {"retrofit_isolation", "option_d", "normalized", "cusum", "seasonal_forecast"}
    want |= {"caltrack_savings", "caltrack_savings_hourly", "best_model"}
    missing = want - set(N.MV_NAMES)
    assert not missing, missing


def test_rows_resolve_and_are_well_formed():
    for code, row in N.MV_NAMES.items():
        assert row.code == code and row.label and row.group in N.GROUPS
        mod, _, attr = row.callable.rpartition(".")
        assert callable(getattr(importlib.import_module(mod), attr)), row.callable
        assert all(i in REFERENCES for i in row.citation_id), code
        assert row.standard_name or row.differences, code  # no standard: say what it is
        assert row.as_dict()["citation_id"] == list(row.citation_id)
    cited = {i for r in N.MV_NAMES.values() for i in r.citation_id}
    assert set(MV_REFERENCES) <= set(REFERENCES)
    assert cited <= set(MV_REFERENCES)


def test_caltrack_rows_say_not_caltrack():
    for code in ("caltrack_savings", "caltrack_savings_hourly"):
        row = N.mv_name(code)
        assert "not CalTRACK" in row.label and row.standard_name == ""
        assert "#156" in row.differences


def test_labels():
    assert N.label("3PC") == "3P cooling" and N.label("3PH") == "3P heating"
    assert N.label("3PHZ") == "3P heating, zero base (CAMBER)"
    assert N.label("5P", ()) == "2P (5P fallback)"
    assert N.label("5P", (50.0, 60.0)) == "5P"
    assert N.label("weird") == "weird" and N.label(None) == ""
    assert N.mv_name("towt").label == "TOWT (CAMBER variant)"
    assert N.citations("towt")[0].id == "mathieu-2011-towt"
    # a fitted model, its payload and a type without a kind
    T = np.linspace(20, 90, 200)
    m = models.fit_model(T, 100 + 3 * np.maximum(0, T - 60), "3PC")
    assert N.model_label(m) == N.model_label(m.as_dict()) == "3P cooling"
    assert N.model_label({"type": "TOWTModel"}) == "TOWT (CAMBER variant)"
    assert N.model_label({"type": "DegreeDayModel", "kind": "both"}).startswith("Degree-day on")
    assert N.model_label({"type": "Other"}) == "Other" and N.model_label(None) == ""
    flat = models.ChangePointModel("5P", {"base": 1.0, "slope": 0.0}, (), 0.0, 3)
    assert N.model_label(flat) == "2P (5P fallback)"


def test_degree_day_renames_warn_and_still_work():
    from camber.mandv import degreeday as D

    t = np.linspace(20, 80, 24)
    e = 500 + 10 * np.maximum(0, 60 - t)
    new = D.fit_mean_temp_degree_day(t, e, kind="heating")
    with pytest.warns(DeprecationWarning, match="fit_mean_temp_degree_day"):
        old = D.fit_degree_day(t, e, kind="heating")
    assert old.as_dict() == new.as_dict() and new.as_dict()["type"] == "DegreeDayModel"
    with pytest.warns(DeprecationWarning, match="MeanTempDegreeDayModel"):
        rt = D.DegreeDayModel.from_dict(new.as_dict())
    assert isinstance(rt, D.MeanTempDegreeDayModel)
    assert D.fit_degree_day.__deprecated__["since"] == "0.105"


def test_option_d_basis_label():
    from camber.mandv import rc_model as R

    assert R.OPTION_D_BASIS == N.mv_name("option_d").label


def test_mandv_doc_table_is_current():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "scripts", "mv_names_doc.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_dossier_titles_name_the_model():
    from camber.dossier import _REFERENCE as EVIDENCE

    lab = N.mv_name("best_model").label
    assert lab in EVIDENCE["bdg2_mv"]["title"] and lab in EVIDENCE["bdg2_mv_savings"]["title"]
