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
# the time-series path existed. From 0.101 (#107) it is the opt-out (use_timeseries=False), and
# the default without a series: both must stay byte-identical.
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
def test_opt_out_output_matches_the_previous_release(token, series, unit, want):
    # 0.101 (#107): use_timeseries=False is the 0.95 default, byte for byte; without a series
    # the new default is too
    s = pd.Series(np.linspace(13, 16, len(IDX)), index=IDX) if series == LINEAR else None
    got = [(x.role, x.confidence)
           for x in suggest_roles(token, series=s, unit=unit, use_timeseries=False)]  # fmt: skip
    assert got == want
    if s is None:
        assert [(x.role, x.confidence) for x in suggest_roles(token, unit=unit)] == want


# 0.101 (#107): with a series, the default reads the data too. The golden series cases recorded
# on the time-series path (the top role holds; the confidences rise with the data's evidence).
GOLDEN_0101 = [
    ("OaTemp", "degC", [("oat", 1.0), ("supply_air_temp", 0.4175), ("mixed_air_temp", 0.4175)]),
    ("OaTemp", None, [("oat", 0.975), ("oa_damper", 0.3798), ("oa_airflow", 0.3742)]),
]


@pytest.mark.parametrize("token,unit,want", GOLDEN_0101)
def test_0101_default_with_a_series_reads_the_data(token, unit, want):
    s = pd.Series(np.linspace(13, 16, len(IDX)), index=IDX)
    got = suggest_roles(token, series=s, unit=unit)
    assert [(x.role, x.confidence) for x in got] == want
    explicit = suggest_roles(token, series=s, unit=unit, use_timeseries=True)
    assert [x.as_dict() for x in got] == [x.as_dict() for x in explicit]


def test_0101_review_unmapped_defaults_and_opt_out():
    from camber.mapping_assist import review_unmapped
    from camber.model.mapping import MappingProvider

    mp = MappingProvider.from_dict({"aliases": {}})
    s = zone()
    on = review_unmapped(["temp_setpoint", "Rm_T"], mp, series_by_token={"temp_setpoint": s})
    off = review_unmapped(["temp_setpoint", "Rm_T"], mp, series_by_token={"temp_setpoint": s},
                          use_timeseries=False)  # fmt: skip
    ts = FeatureSuggester(mp, use_timeseries=True).suggest("temp_setpoint", series=s)
    lex = FeatureSuggester(mp).suggest("temp_setpoint", series=s)
    assert on["suggestions"]["temp_setpoint"] == ts
    assert off["suggestions"]["temp_setpoint"] == lex
    # a token without a series is suggested by name alone either way
    assert (
        on["suggestions"]["Rm_T"]
        == off["suggestions"]["Rm_T"]
        == FeatureSuggester(mp).suggest("Rm_T")
    )
    # an explicit suggester is used as given
    got = review_unmapped(["temp_setpoint"], mp, series_by_token={"temp_setpoint": s},
                          suggester=FeatureSuggester(mp))  # fmt: skip
    assert got["suggestions"]["temp_setpoint"] == lex


def test_timeseries_path_uses_the_data_when_the_name_says_nothing():
    fs = FeatureSuggester(use_timeseries=True, oat=OAT)
    anon = fs.suggest("3dfa2bab_f8f2_485b", series=OAT2)
    # the data places it (0.101, #106: the physical-range check now joins it without a unit)
    assert anon and anon[0].role in ("oat", "wetbulb_temp") and anon[0].basis == "combined"
    assert "read as degC" in anon[0].rationale and "physical bounds" in anon[0].rationale
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
    # the data (its template note) ranks only weather roles; 0.101 adds the range check's basis
    assert all(x.role in allowed or "data at the level" not in x.rationale for x in got)
    # a named outdoor point never takes a wet-bulb or supply-air role from the data alone
    for name, series in [("Weather_Current_Temperature", OAT2), ("Weather_Current_Humidity", rh)]:
        roles = [x.role for x in fs.suggest(name, series=series)]
        assert "wetbulb_temp" not in roles and "supply_air_humidity" not in roles
    # without an outdoor word there is no guard
    from camber.mapping_assist import _outdoor_guard, _tag_tokens

    assert _outdoor_guard(_tag_tokens("3dfa2bab_f8f2_485b")) is None
    assert Role.WETBULB_TEMP in _outdoor_guard(_tag_tokens("OA_WB"))


def test_0101_setpoint_guard_keeps_a_named_setpoint_a_setpoint():
    # an occupant-adjusted zone setpoint moves like the room it controls: before 0.101 the data
    # carried ``temp_setpoint`` to space_temp; the name says setpoint, so the data now only ranks
    # the setpoint roles (#106)
    from camber.mapping_assist import _SETPOINT_ROLES, _setpoint_guard, _tag_tokens

    fs = FeatureSuggester(use_timeseries=True, oat=OAT)
    got = fs.suggest("temp_setpoint", series=zone())
    assert got[0].role == "cool_sp"
    assert all(Role(x.role) in _SETPOINT_ROLES or "data at the level" not in x.rationale
               for x in got)  # fmt: skip
    assert "space_temp" not in [x.role for x in got[:1]]
    # the guard follows the name: a setpoint word (also run together) guards, a sensor does not
    assert _setpoint_guard(_tag_tokens("RMCLGSPT")) == _SETPOINT_ROLES
    assert _setpoint_guard(_tag_tokens("Zone_Temp")) is None
    assert _setpoint_guard(_tag_tokens("ZN-SPT")) is None  # SPT is a space temperature
    # a sensor name is unaffected
    assert fs.suggest("Zone_Temp", series=zone())[0].role == "space_temp"


def test_0101_range_check_without_a_unit_reads_every_plausible_unit():
    from camber.mapping_assist import _ts_range_violation
    from camber.sensorhealth import range_violation_frac

    room_c = zone()  # degC
    # read as is (degF) a 22 C room is impossible; read as degC it fits
    assert range_violation_frac(room_c, Role.SPACE_TEMP) == 1.0
    assert _ts_range_violation(room_c, Role.SPACE_TEMP, "") == 0.0
    # a declared unit is trusted, as on the default path
    assert _ts_range_violation(room_c, Role.SPACE_TEMP, "degf") == 1.0
    assert _ts_range_violation(room_c, Role.SPACE_TEMP, "degc") == 0.0
    # a duct static in Pa fits once read as Pa; a sentinel fits no unit
    assert _ts_range_violation(_s(np.full(len(IDX), 250.0)), Role.DUCT_STATIC, "") == 0.0
    assert _ts_range_violation(_s(np.full(len(IDX), -999.0)), Role.SPACE_TEMP, "") == 1.0
    # a temperature difference converts without the offset (2 C of subcooling is 3.6 F)
    assert _ts_range_violation(_s(np.full(len(IDX), -15.0)), Role.SUBCOOLING_TEMP, "") == 0.0
    assert _ts_range_violation(_s(np.full(len(IDX), -15.0)), Role.SUBCOOLING_TEMP, "degc") == 1.0


def test_0101_time_series_path_range_checks_a_point_with_no_unit():
    # a dead channel (-999) named as a supply-air temperature: before 0.101 the time-series path
    # skipped the range check without a unit and kept supply_air_temp first; the default path
    # (name and series) already demoted it, and now both do (#106)
    dead = _s(np.full(len(IDX), -999.0) + RNG.normal(0, 0.01, len(IDX)))
    ts = [x.role for x in FeatureSuggester(use_timeseries=True).suggest("SupplyAirTemp",
                                                                        series=dead)]  # fmt: skip
    assert "supply_air_temp" not in ts
    assert "supply_air_temp" not in [x.role for x in suggest_roles("SupplyAirTemp", series=dead)]
    # a degC zone temperature with no unit keeps its role and gains the range-fit basis
    got = FeatureSuggester(use_timeseries=True).suggest("Zone_Temp", series=zone())
    assert got[0].role == "space_temp" and "physical bounds" in got[0].rationale


def _occupied():
    return (HOUR >= 7) & (HOUR < 18) & (IDX.dayofweek.to_numpy() < 5)


def test_0102_exact_zero_dropouts_are_not_range_evidence():
    # a zero a role cannot read in any plausible unit (a 0 degC room is 32 degF) is a dropout:
    # it is left out of the unitless range check, and an all-zero series gives no verdict (#110)
    from camber.mapping_assist import _ts_range_violation

    room = zone().to_numpy().copy()
    room[RNG.random(len(IDX)) < 0.4] = 0.0  # 40 % dropouts
    assert _ts_range_violation(_s(room), Role.SPACE_TEMP, "") == 0.0
    assert math.isnan(_ts_range_violation(_s(np.zeros(len(IDX))), Role.OUTDOOR_CO2, ""))
    # where 0 is a real reading (a closed damper, a stopped fan's airflow) zeros still count
    assert _ts_range_violation(_s(np.zeros(len(IDX))), Role.AIRFLOW, "") == 0.0
    assert _ts_range_violation(_s(np.full(len(IDX), -500.0)), Role.AIRFLOW, "") == 1.0
    # a declared unit is trusted as before: zeros are read as zeros
    assert _ts_range_violation(_s(room), Role.SPACE_TEMP, "degc") > 0.3
    # a strongly named point on a dead (all-zero) channel keeps the name's role
    assert suggest_roles("Outside_Air_CO2_Sensor", series=_s(np.zeros(len(IDX))))[0].role == (
        "outdoor_co2"
    )


def test_0102_dirty_series_does_not_overturn_a_strong_name():
    # a terminal airflow logged with a negative sign most of the time (a sign convention, or a
    # bad scaling): before 0.102 the unitless range check demoted ``airflow`` to almost nothing
    # and the setpoint (which has no physical bounds) took top-1 (#110)
    flow = np.where(_occupied(), -400.0, -150.0) + RNG.normal(0, 20, len(IDX))
    flow[RNG.random(len(IDX)) < 0.1] = 120.0
    got = suggest_roles("Discharge_Air_Flow_Sensor", series=_s(flow))
    assert got[0].role == "airflow"
    # a zone temperature with a scale mix-up on most samples keeps its role too
    room = zone().to_numpy().copy()
    room[RNG.random(len(IDX)) < 0.85] /= 6.0
    assert suggest_roles("Zone_Air_Temperature_Sensor", series=_s(room))[0].role == "space_temp"
    # a series that never fits (a sentinel, a dead channel) is still demoted in full
    dead = _s(np.full(len(IDX), -999.0) + RNG.normal(0, 0.01, len(IDX)))
    assert "supply_air_temp" not in [x.role for x in suggest_roles("SupplyAirTemp", series=dead)]
    # the 0.100 opt-out is unchanged
    old = suggest_roles("Discharge_Air_Flow_Sensor", series=_s(flow), use_timeseries=False)
    assert old[0].role != "airflow"
