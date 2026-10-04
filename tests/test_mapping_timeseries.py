"""Time-series evidence for point-role suggestion (0.96, #45): profiles, templates, the fitted
model, the blend with names, and the opt-in ``FeatureSuggester(use_timeseries=True)`` path.

Synthetic series only (no dataset is vendored)."""

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mapping_assist import FeatureSuggester, suggest_roles  # noqa: E402
from camber.mapping_timeseries import (  # noqa: E402
    INFORMATIVE_NAME,
    ROLE_TEMPLATES,
    ProfileModel,
    blend,
    profile_series,
    template_scores,
)
from camber.model.roles import Role  # noqa: E402

IDX = pd.date_range("2024-01-01", periods=96 * 42, freq="15min")  # six weeks
HOUR = IDX.hour.to_numpy() + IDX.minute.to_numpy() / 60.0
DAY = np.arange(len(IDX)) // 96
RNG = np.random.default_rng(7)
# weather: a daily swing on a slow synoptic wander (degC)
OAT = pd.Series(
    16
    + 6 * np.sin((HOUR - 9) / 24 * 2 * np.pi)
    + 4 * np.sin(DAY / 5.0)
    + RNG.normal(0, 0.3, len(IDX)),
    index=IDX,
)
OAT2 = OAT + RNG.normal(0, 0.4, len(IDX))  # a second outdoor sensor of the site


def _s(values):
    return pd.Series(np.asarray(values, dtype=float), index=IDX)


def zone():
    return _s(22 + 0.4 * np.sin((HOUR - 15) / 24 * 2 * np.pi) + 0.2 * np.sin(DAY / 3.0)
              + RNG.normal(0, 0.08, len(IDX)))  # fmt: skip


def setpoint():
    return _s(np.where((HOUR >= 7) & (HOUR < 18) & (IDX.dayofweek.to_numpy() < 5), 22.0, 18.0))


def status():
    return _s(((HOUR >= 6) & (HOUR < 19) & (IDX.dayofweek.to_numpy() < 5)).astype(float))


def heating_valve():
    # opens as the day is colder (daily-mean response), clipped to 0-100 %
    daily = OAT.groupby(IDX.normalize()).transform("mean")
    return _s(np.clip((18 - daily.to_numpy()) * 12 + RNG.normal(0, 3, len(IDX)), 0, 100))


def test_profile_captures_level_timing_and_behaviour():
    p = profile_series(zone(), oat=OAT)
    assert 21.4 < p.median < 22.6 and p.n == len(IDX) and p.cadence_s == 900.0
    assert not p.binary and not p.two_level and p.change_frac > 0.9
    assert p.diurnal > 0.5  # a clean daily swing
    sp = profile_series(setpoint())
    assert sp.two_level and not sp.binary and sp.change_frac < 0.05 and sp.step_share == 1.0
    assert sp.weekly > 0.3  # weekday/weekend schedule
    st = profile_series(status())
    assert st.binary and st.zero_frac > 0.5
    o = profile_series(OAT2, oat=OAT)
    assert o.oat_corr_hourly > 0.95 and o.oat_corr > 0.9
    hv = profile_series(heating_valve(), oat=OAT)
    assert hv.oat_corr < -0.8
    # no DatetimeIndex: value features only, timing features NaN
    arr = profile_series(np.array([1.0, 2.0, 2.0, 3.0]))
    assert arr.n == 4 and math.isnan(arr.diurnal) and math.isnan(arr.cadence_s)
    empty = profile_series(pd.Series(dtype=float))
    assert empty.n == 0 and template_scores(empty) == {}


def _top(p, unit=None, k=3):
    sc = template_scores(p, unit)
    return [r.value for r, _ in sorted(sc.items(), key=lambda kv: -kv[1][0])[:k]]


def test_templates_recognise_the_physical_signatures():
    assert _top(profile_series(OAT2, oat=OAT), k=2)[0] in ("oat", "wetbulb_temp")
    assert "space_temp" in _top(profile_series(zone(), oat=OAT))
    assert any(r.endswith("_sp") for r in _top(profile_series(setpoint(), oat=OAT)))
    assert _top(profile_series(status()))[0] in {"supply_fan_status", "pump_status", "occupancy"}
    top = _top(profile_series(heating_valve(), oat=OAT), k=5)
    assert "heat_valve" in top and (
        "cool_valve" not in top or top.index("heat_valve") < top.index("cool_valve")
    )


def test_a_declared_unit_restricts_the_interpretation():
    p = profile_series(zone(), oat=OAT)
    assert all(ROLE_TEMPLATES[r].family == "temp" for r in template_scores(p, "degC"))
    assert template_scores(p, "ppm") == {} or all(
        ROLE_TEMPLATES[r].family == "co2" for r in template_scores(p, "ppm")
    )
    # 22 read as degF is an implausible zone temperature
    assert template_scores(p, "degF").get(Role.SPACE_TEMP, (0,))[0] < 0.2


def test_profile_model_learns_classes_and_uses_equal_priors():
    train = [(profile_series(zone() + RNG.normal(0, 0.3)), "space_temp") for _ in range(6)]
    train += [(profile_series(status()), "pump_status") for _ in range(20)]
    m = ProfileModel().fit(train)
    assert set(m.roles) == {Role.SPACE_TEMP, Role.PUMP_STATUS}
    sc = m.scores(profile_series(zone()))
    assert sc[Role.SPACE_TEMP] > 0.9 and math.isclose(sum(sc.values()), 1.0)
    assert ProfileModel().scores(profile_series(zone())) == {}  # not fitted
    with pytest.raises(ValueError):
        ProfileModel(min_examples=5).fit(train[:3])


def test_blend_lets_an_informative_name_dominate():
    # a clear name: the data only breaks ties
    assert blend(0.9, 0.0, 0.9) > blend(0.5, 1.0, 0.9)
    # no usable name: the data carries the choice
    assert blend(0.0, 1.0, 0.0) == pytest.approx(0.8)
    assert blend(0.3, 0.5, INFORMATIVE_NAME) == pytest.approx(0.35)


def test_default_suggester_is_unchanged_and_ignores_the_new_keywords():
    s = zone()
    base = [x.as_dict() for x in FeatureSuggester().suggest("ZoneTemp", series=s, unit="degC")]
    same = [
        x.as_dict()
        for x in FeatureSuggester(use_timeseries=False, oat=OAT).suggest(
            "ZoneTemp", series=s, unit="degC", oat=OAT
        )
    ]
    assert base == same
    assert all(x["basis"] != "timeseries" for x in base)


# (token, series, unit) -> the default suggester's output recorded on 0.95 (8f2d7b8), before
# the time-series path existed: the default must stay byte-identical.
LINEAR = "linear 13-16"
GOLDEN = [
    ("AH1_SAT", None, "degF", [("supply_air_temp", 0.95), ("hw_supply_temp", 0.65),
                               ("chw_supply_temp", 0.65)]),
    ("OaTemp", LINEAR, "degC", [("oat", 0.98), ("supply_air_temp", 0.4175),
                                ("mixed_air_temp", 0.4175)]),
    ("OaTemp", LINEAR, None, [("oat", 0.93), ("oa_damper", 0.3675), ("oa_airflow", 0.3675)]),
    ("3dfa2bab_f8f2_485b", None, None, []),
    ("Zone_Air_Temperature_Sensor", None, None, [("space_temp", 0.9), ("oat", 0.375),
                                                 ("supply_air_temp", 0.375)]),
    ("VAV12_DmprPos", None, "%", [("damper", 0.95), ("oa_damper", 0.5), ("heat_valve", 0.3)]),
]  # fmt: skip


@pytest.mark.parametrize("token,series,unit,want", GOLDEN)
def test_default_output_matches_the_previous_release(token, series, unit, want):
    s = pd.Series(np.linspace(13, 16, len(IDX)), index=IDX) if series == LINEAR else None
    got = [(x.role, x.confidence) for x in suggest_roles(token, series=s, unit=unit)]
    assert got == want


def test_timeseries_path_uses_the_data_when_the_name_says_nothing():
    fs = FeatureSuggester(use_timeseries=True, oat=OAT)
    anon = fs.suggest("3dfa2bab_f8f2_485b", series=OAT2)
    assert anon and anon[0].role in ("oat", "wetbulb_temp") and anon[0].basis == "timeseries"
    assert "read as degC" in anon[0].rationale
    # a clear name keeps its role on top even when the data is ambiguous
    named = fs.suggest("Zone_Air_Temperature_Sensor", series=setpoint())
    assert named[0].role == "space_temp" and named[0].basis == "combined"
    # a precomputed profile is accepted, and a fitted model replaces the templates
    prof = profile_series(status())
    m = ProfileModel().fit([(prof, "pump_status"), (prof, "pump_status"),
                            (profile_series(zone()), "space_temp"),
                            (profile_series(zone()), "space_temp")])  # fmt: skip
    got = FeatureSuggester(use_timeseries=True, model=m).suggest("x", profile=prof)
    assert got[0].role == "pump_status" and "fitted profile model" in got[0].rationale


def test_0100_weather_station_guard_keeps_the_data_on_weather_roles():
    # an outdoor humidity whose data the templates read as an outdoor airflow (no hour-by-hour
    # match with the site's outdoor reference): a weather-station name keeps it a weather role
    rh = _s(np.clip(50 + 10 * np.sin((HOUR - 3) / 24 * 2 * np.pi) + RNG.normal(0, 2, len(IDX)),
                    20, 100))  # fmt: skip
    fs = FeatureSuggester(use_timeseries=True, oat=OAT)
    got = fs.suggest("Weather_7", series=rh)
    assert got[0].role == "outdoor_rh"
    allowed = {"oat", "outdoor_rh", "outdoor_co2"}
    assert all(x.role in allowed or x.basis != "combined" for x in got)
    # a named outdoor point never takes a wet-bulb or supply-air role from the data alone
    for name, series in [("Weather_Current_Temperature", OAT2), ("Weather_Current_Humidity", rh)]:
        roles = [x.role for x in fs.suggest(name, series=series)]
        assert "wetbulb_temp" not in roles and "supply_air_humidity" not in roles
    # without an outdoor word there is no guard
    from camber.mapping_assist import _outdoor_guard, _tag_tokens

    assert _outdoor_guard(_tag_tokens("3dfa2bab_f8f2_485b")) is None
    assert Role.WETBULB_TEMP in _outdoor_guard(_tag_tokens("OA_WB"))
