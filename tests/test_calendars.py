"""Holiday calendars for the occupied-day driver (0.93, #68): bundled data, CSV files, providers,
and the ``mv[].holiday_calendar`` / ``break_calendar`` config path."""

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import calendars  # noqa: E402
from camber.calendars import (  # noqa: E402
    HolidayCalendar,
    calendar_info,
    load_calendar_csv,
    public_holidays,
    register_calendar,
)
from camber.datasets._catalog import package_text  # noqa: E402
from camber.mandv import _mvform  # noqa: E402

D = dt.date
# the 2018 Norwegian public holidays on weekdays, as the cofactor-drammen template listed them
# by hand before 0.93
NO_2018 = [
    "2018-01-01", "2018-03-29", "2018-03-30", "2018-04-02", "2018-05-01",
    "2018-05-10", "2018-05-17", "2018-05-21", "2018-12-25", "2018-12-26",
]  # fmt: skip


# --------------------------------------------------------------------------- bundled data


@pytest.mark.parametrize("code", calendars.bundled())
def test_bundled_files_cite_their_sources_and_stay_in_coverage(code):
    info = calendar_info(code)
    assert info["schema"] == calendars.SCHEMA
    assert info["sources"] and all(s.get("url", "").startswith("https://") for s in info["sources"])
    assert info["terms"] and info["retrieved"]
    lo, hi = info["coverage"]
    doc = calendars._load(code)
    for dates in doc["dates"].values():
        assert dates and all(lo <= int(d[:4]) <= hi for d in dates)


def test_us_federal_observed_dates():
    h = public_holidays("US", [2021, 2022])
    assert h[D(2021, 12, 31)] == "New Year's Day"  # 2022-01-01 is a Saturday
    assert h[D(2021, 6, 18)].startswith("Juneteenth")  # first year, observed Friday
    assert h[D(2021, 7, 5)] == "Independence Day"
    assert D(2022, 1, 1) not in h
    assert not [d for d in public_holidays("US", 2020) if d.month == 6]  # no Juneteenth yet
    assert len(public_holidays("US", 2018)) == 10
    with pytest.raises(ValueError, match="covers 2011-2030"):
        public_holidays("US", 2031)


def test_norway_matches_the_hand_listed_2018_holidays():
    listed = NO_2018
    got = public_holidays("NO", 2018)
    weekdays = sorted(d.isoformat() for d in got if d.weekday() < 5)
    assert weekdays == sorted(listed)
    assert got[D(2018, 5, 17)].startswith("Grunnlovsdag")
    tmpl = json.loads(package_text("configs", "cofactor-drammen.json"))["mv"][0]
    assert tmpl["holiday_calendar"] == "NO"
    assert D(2024, 3, 28) in public_holidays("NO", 2024)  # Maundy Thursday, Easter 31 March


def test_spain_needs_the_community():
    with pytest.raises(ValueError, match="differ by region"):
        public_holidays("ES", 2018)
    with pytest.raises(ValueError, match="no subdivision 'XX'"):
        public_holidays("ES-XX", 2018)
    cl = public_holidays("ES-CL", 2018)
    assert D(2018, 4, 23) in cl and D(2018, 3, 29) in cl  # Castilla y León day, Maundy Thursday
    assert len(cl) == 12
    assert public_holidays("ES", 2018, subdivision="CL") == cl
    assert D(2018, 12, 8) in public_holidays("ES-CN", 2018)  # BOE-A-2017-12209 correction
    ct = public_holidays("ES-CT", 2019)
    assert D(2019, 9, 11) in ct and D(2019, 4, 18) not in ct  # Diada; no Maundy Thursday
    assert D(2016, 5, 2) in public_holidays("ES-CL", 2016)  # from the PDF-only annex


def test_unknown_country_names_the_way_out():
    with pytest.raises(ValueError, match="register a provider"):
        public_holidays("DE", 2020)


def test_registered_provider_serves_its_code():
    register_calendar("DE", lambda y, sub: {f"{y}-10-03": "Tag der Deutschen Einheit"})
    try:
        assert public_holidays("DE", 2020) == {D(2020, 10, 3): "Tag der Deutschen Einheit"}
        assert "DE" in calendars.registered()
        assert HolidayCalendar.from_spec("DE").coverage() is None
    finally:
        register_calendar("DE", None)
    assert "DE" not in calendars.registered()
    with pytest.raises(TypeError):
        register_calendar("DE", "not callable")


# --------------------------------------------------------------------------- CSV files


def test_csv_dates_and_ranges(tmp_path):
    p = tmp_path / "school.csv"
    p.write_text(
        "# a comment\nstart,end,name\n2019-09-30,2019-10-04,autumn break\n2019-12-21,2020-01-05,\n",
        encoding="utf-8",
    )
    got = load_calendar_csv(str(p))
    assert len(got) == 5 + 16
    assert got[D(2019, 10, 2)] == "autumn break" and got[D(2020, 1, 1)] == "school"
    q = tmp_path / "days.csv"
    q.write_text("date\n2016-05-13\n2016-09-08\n", encoding="utf-8")
    assert set(load_calendar_csv(str(q))) == {D(2016, 5, 13), D(2016, 9, 8)}


@pytest.mark.parametrize(
    "text,msg",
    [
        ("day\n2019-01-01\n", "needs a 'date' column"),
        ("date\nnot-a-date\n", "unreadable date"),
        ("start,end\n2019-02-01,2019-01-01\n", "before start"),
    ],
)
def test_csv_errors(tmp_path, text, msg):
    p = tmp_path / "bad.csv"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        load_calendar_csv(str(p))


def test_calendar_spec(tmp_path):
    p = tmp_path / "local.csv"
    p.write_text("date\n2018-05-13\n", encoding="utf-8")
    cal = HolidayCalendar.from_spec(
        {"country": "ES", "subdivision": "CL", "files": ["local.csv"], "dates": ["2018-09-08"]},
        base_dir=str(tmp_path),
    )
    days = cal.days("2018-01-01", "2018-12-31")
    assert {D(2018, 5, 13), D(2018, 9, 8), D(2018, 4, 23)} <= set(days)
    assert cal.label() == "ES-CL public holidays + 1 calendar file + 1 listed date"
    assert cal.coverage() == (2016, 2026)
    for spec, msg in (
        ({"country": "NO", "file": "x"}, "unknown key"),
        ({"files": ["missing.csv"]}, "no such file"),
        ({"subdivision": "CL"}, "needs a country"),
        ({}, "needs a country, files or dates"),
        ("ES", "differ by region"),
        (3, "must be a country code"),
        ({"dates": ["soon"]}, "is not a date"),
    ):
        with pytest.raises(ValueError, match=msg):
            HolidayCalendar.from_spec(spec, base_dir=str(tmp_path))


# --------------------------------------------------------------------------- the drivers


def _daily(start="2018-01-01", end="2018-12-31"):
    idx = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"energy": 1.0, "oat": 50.0}, index=idx)


def test_occupied_day_from_the_calendar_equals_the_listed_dates():
    listed = NO_2018
    base = {"model": "cp_driver", "drivers": ["occupied_day"]}
    a = _mvform.add_drivers(_daily(), {**base, "holidays": listed})
    b = _mvform.add_drivers(_daily(), {**base, "holiday_calendar": "NO"})
    pd.testing.assert_frame_equal(a, b)


def test_days_outside_the_coverage_are_dropped_not_guessed():
    entry = {"model": "cp_driver", "drivers": ["occupied_day"], "holiday_calendar": "US"}
    out = _mvform.add_drivers(_daily("2030-12-01", "2031-01-31"), entry)
    assert out.index.max() == pd.Timestamp("2030-12-31")
    assert out.loc["2030-12-25", "drv:occupied_day"] == 0.0


def test_break_day_is_its_own_driver(tmp_path):
    p = tmp_path / "breaks.csv"
    p.write_text("start,end\n2018-10-01,2018-10-05\n2018-12-20,2019-01-04\n", encoding="utf-8")
    entry = _mvform.with_base_dir(
        {
            "model": "cp_driver",
            "drivers": ["occupied_day", "break_day"],
            "holiday_calendar": "NO",
            "break_calendar": {"files": ["breaks.csv"]},
        },
        str(tmp_path),
    )
    assert os.path.isabs(entry["break_calendar"]["files"][0])
    out = _mvform.add_drivers(_daily(), entry)
    row = out.loc["2018-10-03"]
    assert row["drv:occupied_day"] == 1.0 and row["drv:break_day"] == 1.0
    xmas = out.loc["2018-12-25"]  # a public holiday inside the break: off, not a break day
    assert xmas["drv:occupied_day"] == 0.0 and xmas["drv:break_day"] == 0.0
    assert out.loc["2018-10-06", "drv:break_day"] == 0.0  # Saturday
    assert out["drv:break_day"].sum() == 5 + 6  # 1-5 Oct; 20, 21, 24, 27, 28, 31 Dec
    assert np.isfinite(out["drv:break_day"]).all()


@pytest.mark.parametrize(
    "entry,msg",
    [
        ({"drivers": ["weekday"], "holiday_calendar": "NO"}, 'needs the "occupied_day"'),
        ({"drivers": ["occupied_day"], "break_calendar": {"dates": ["2018-01-02"]}}, "go together"),
        ({"drivers": ["occupied_day", "break_day"]}, "go together"),
        ({"drivers": ["occupied_day"], "holiday_calendar": "XX"}, "holiday_calendar: no bundled"),
    ],
)
def test_config_validation(entry, msg):
    with pytest.raises(ValueError, match=msg):
        _mvform.spec_of({"model": "cp_driver", **entry})


def test_config_path_reads_a_calendar_file_beside_the_config(tmp_path):
    from camber.config import run_config

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_mv_cp_driver import HOLIDAY, _write

    (tmp_path / "closures.csv").write_text(f"date\n{HOLIDAY}\n", encoding="utf-8")
    entry = {
        "model": "cp_driver",
        "drivers": ["occupied_day"],
        "holiday_calendar": {"files": ["closures.csv"]},
    }
    cfg, _path, _ws = _write(tmp_path, entry)
    run = run_config(cfg, base_dir=str(tmp_path))
    by_file = [f for f in run.findings if f.rule == "mv_baseline"]
    listed = {k: v for k, v in entry.items() if k != "holiday_calendar"}
    cfg2, _p, _w = _write(tmp_path / "b", {**listed, "holidays": [HOLIDAY]})
    by_list = [f for f in run_config(cfg2).findings if f.rule == "mv_baseline"]
    assert by_file[0].metrics["cv_rmse"] == by_list[0].metrics["cv_rmse"]
    assert by_file[0].metrics["drivers"] == ["occupied_day"]
