"""Tests for assisted point mapping (camber.mapping_assist).

Covers the dependency-light FeatureSuggester: string/initials matching, unit compatibility, and
physical-range-fit demotion; plus review_unmapped's advisory contract (only unmapped tokens, mapping
never mutated).
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.agent import stub_client  # noqa: E402
from camber.mapping_assist import (  # noqa: E402
    LLMSuggester,
    MLSuggester,
    RoleSuggestion,
    review_unmapped,
    suggest_roles,
)
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402

_IDX = pd.date_range("2024-07-01", periods=120, freq="1h")


def _series(lo, hi, seed=0):
    return pd.Series(np.random.default_rng(seed).uniform(lo, hi, len(_IDX)), index=_IDX)


def _roles(suggestions):
    return [s.role for s in suggestions]


def test_initials_match_ranks_role_top():
    top = suggest_roles("AH1_SAT", unit="degF")
    assert top[0].role == Role.SUPPLY_AIR_TEMP.value
    assert top[0].confidence > 0.5
    assert "initials" in top[0].basis or top[0].basis == "combined"


def test_every_suggestion_is_a_valid_role():
    for s in suggest_roles("VAV12_DmprPos", unit="%", k=5):
        assert isinstance(s, RoleSuggestion)
        Role(s.role)  # raises if out of vocab
        assert 0.0 < s.confidence <= 1.0


def test_unit_disambiguates_flow_from_temp():
    # "CHW" alone is ambiguous between temp and flow; gpm points at flow
    flow = suggest_roles("CHW_Loop", unit="gpm")
    assert flow[0].role == Role.CHW_FLOW.value


def test_incompatible_unit_demotes_role():
    # a temperature-looking tag but a % unit -> temp roles pushed down
    with_pct = suggest_roles("Room_Temp", unit="%", k=8)
    temp_conf = next((s.confidence for s in with_pct if s.role == Role.SPACE_TEMP.value), 0.0)
    with_degf = suggest_roles("Room_Temp", unit="degF", k=8)
    temp_conf_ok = next(s.confidence for s in with_degf if s.role == Role.SPACE_TEMP.value)
    assert temp_conf < temp_conf_ok


def test_range_fit_demotes_physically_impossible_role():
    # a series pinned at 500 violates every temperature bound -> temp roles fall out of the top
    ok = suggest_roles("AH1_SAT", series=_series(52, 58), unit="degF", k=3)
    bad = suggest_roles("AH1_SAT", series=pd.Series(500.0, index=_IDX), unit="degF", k=3)
    assert Role.SUPPLY_AIR_TEMP.value == ok[0].role
    assert Role.SUPPLY_AIR_TEMP.value not in _roles(bad)


def test_range_fit_bonus_promotes_fitting_data():
    plain = suggest_roles("OAT")
    fit = suggest_roles("OAT", series=_series(30, 95))  # squarely inside OAT bounds
    oat_plain = next(s.confidence for s in plain if s.role == Role.OAT.value)
    oat_fit = next(s.confidence for s in fit if s.role == Role.OAT.value)
    assert oat_fit >= oat_plain


def test_k_limits_result_count():
    assert len(suggest_roles("AH1_SAT", unit="degF", k=2)) == 2
    assert len(suggest_roles("AH1_SAT", unit="degF", k=1)) == 1


def test_rationale_is_deterministic_and_nonempty():
    a = suggest_roles("AH1_SAT", unit="degF")[0]
    b = suggest_roles("AH1_SAT", unit="degF")[0]
    assert a.rationale == b.rationale and a.rationale
    assert a.as_dict()["rationale"] == a.rationale


def _mapping():
    return MappingProvider.from_dict(
        {
            "aliases": {"OAT": "oat"},
            "patterns": [[r".*_sat$", "supply_air_temp"]],
        }
    )


def test_review_unmapped_returns_only_unmapped():
    mp = _mapping()
    tokens = ["OAT", "AH1_SAT", "VAV12_DmprPos", "MysteryPoint"]
    rev = review_unmapped(tokens, mp, units={"VAV12_DmprPos": "%"})
    # OAT (alias) and AH1_SAT (pattern) resolve; the other two don't
    assert set(rev["suggestions"]) == {"VAV12_DmprPos", "MysteryPoint"}
    assert rev["n_unmapped"] == 2
    assert all(t in {"VAV12_DmprPos", "MysteryPoint"} for t in rev["suggestions"])


def test_review_unmapped_never_mutates_mapping():
    mp = _mapping()
    before_aliases = dict(mp.aliases)
    review_unmapped(["Foo_Damper", "Bar_Qux"], mp, units={"Foo_Damper": "%"})
    assert mp.aliases == before_aliases
    assert mp.role_of("Foo_Damper") is None  # still unmapped afterward


def test_review_unmapped_attaches_serializable_suggestions():
    mp = _mapping()
    rev = review_unmapped(["VAV12_DmprPos"], mp, units={"VAV12_DmprPos": "%"})
    entry = rev["review_list"][0]
    assert entry["token"] == "VAV12_DmprPos"
    assert isinstance(entry["suggestions"], list) and entry["suggestions"]
    assert set(entry["suggestions"][0]) >= {"token", "role", "confidence", "basis", "rationale"}


def test_custom_suggester_is_honored():
    class _Fixed:
        def suggest(self, token, *, series=None, unit=None, k=3):
            return [RoleSuggestion(token, Role.CO2.value, 1.0, "ml", "stub")]

    out = suggest_roles("whatever", suggester=_Fixed())
    assert out[0].role == Role.CO2.value and out[0].basis == "ml"


# --------------------------------------------------------------------- LLMSuggester (agent seam)


def test_llm_suggester_drops_out_of_vocab_and_labels_basis():
    stub = stub_client("supply_air_temp, oat, flux_capacitor")  # last is not a Role
    out = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub))
    assert [s.role for s in out][:2] == [Role.SUPPLY_AIR_TEMP.value, Role.OAT.value]
    assert all(s.role in {r.value for r in Role} for s in out)  # no out-of-vocab leaked
    assert out[0].basis == "llm"


def test_llm_suggestion_rescored_against_physical_range():
    # the SAME proposed role scores lower when the data contradicts it
    bad = suggest_roles(
        "AH1_SAT",
        suggester=LLMSuggester(stub_client("supply_air_temp")),
        series=pd.Series(500.0, index=_IDX),
    )
    good = suggest_roles(
        "AH1_SAT", suggester=LLMSuggester(stub_client("supply_air_temp")), series=_series(52, 58)
    )
    assert bad[0].confidence < good[0].confidence


def test_llm_suggester_empty_when_no_valid_role_proposed():
    out = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub_client("banana, wormhole")))
    assert out == []


# ---------------------------------------------------------------------- MLSuggester ([ml] extra)


def _ml_labels():
    labels = []
    for prefix in ("AH1", "AH2", "RTU3", "AHU7", "MAU4"):
        labels += [
            (f"{prefix}_SAT", "supply_air_temp"),
            (f"{prefix}_OAT", "oat"),
            (f"{prefix}_MAT", "mixed_air_temp"),
            (f"{prefix}_DmprPos", "oa_damper"),
            (f"{prefix}_HtgVlv", "heat_valve"),
            (f"{prefix}_ClgVlv", "cool_valve"),
        ]
    return labels


def test_ml_suggester_learns_from_synthetic_labels():
    pytest.importorskip("sklearn")
    ml = MLSuggester().fit(_ml_labels())
    assert ml.suggest("AH9_SAT")[0].role == Role.SUPPLY_AIR_TEMP.value
    assert ml.suggest("RTU1_OAT")[0].role == Role.OAT.value
    assert ml.suggest("VAV2_DmprPos")[0].basis == "ml"


def test_ml_suggester_range_gate_demotes_impossible_prediction():
    pytest.importorskip("sklearn")
    ml = MLSuggester().fit(_ml_labels())
    clean = ml.suggest("AH9_SAT", series=_series(52, 58))
    wild = ml.suggest("AH9_SAT", series=pd.Series(500.0, index=_IDX))
    c = next((s.confidence for s in clean if s.role == Role.SUPPLY_AIR_TEMP.value), 1.0)
    w = next((s.confidence for s in wild if s.role == Role.SUPPLY_AIR_TEMP.value), 0.0)
    assert w < c


def test_ml_suggester_from_mapping_bootstrap():
    pytest.importorskip("sklearn")
    aliases = {f"{p}_SAT": "supply_air_temp" for p in "ABC"}
    aliases.update({f"{p}_OAT": "oat" for p in "ABC"})
    mp = MappingProvider.from_dict({"aliases": aliases, "patterns": []})
    ml = MLSuggester.from_mapping(mp)
    assert ml.suggest("Z_SAT")[0].role == Role.SUPPLY_AIR_TEMP.value


def test_ml_suggester_raises_before_fit():
    pytest.importorskip("sklearn")
    with pytest.raises(RuntimeError):
        MLSuggester().suggest("AH1_SAT")


# ------------------------------------------------ whole-token matching on real-world point names

# The point list of a published single-duct AHU dataset (names only, no data) with the role an
# engineer would assign. Before whole-token matching, role initials matched as *substrings* of the
# unsplit name (OaTemp -> oa_airflow, ReHeatVlvPos -> evap_approach_temp) and 3 of 16 mapped.
_REAL_AHU_POINTS = {
    "RaTemp": Role.RETURN_AIR_TEMP,
    "RaFanPower": Role.POWER,
    "OaTemp": Role.OAT,
    "OaDmprPos": Role.OA_DAMPER,
    "MaTemp": Role.MIXED_AIR_TEMP,
    "HWVlvPos": Role.HEAT_VALVE,
    "ChWVlvPos": Role.COOL_VALVE,
    "DaFanPower": Role.POWER,
    "DaTemp": Role.SUPPLY_AIR_TEMP,
    "OaTemp_WS": Role.OAT,
    "ReHeatVlvPos_1": Role.HEAT_VALVE,
    "ReHeatVlvPos_2": Role.HEAT_VALVE,
    "ZoneDaTemp_1": Role.SUPPLY_AIR_TEMP,
    "ZoneDaTemp_2": Role.SUPPLY_AIR_TEMP,
    "ZoneTemp_1": Role.SPACE_TEMP,
    "ZoneTemp_2": Role.SPACE_TEMP,
}


@pytest.mark.parametrize("name,role", sorted(_REAL_AHU_POINTS.items()))
def test_real_point_names_rank_the_right_role_first(name, role):
    assert suggest_roles(name)[0].role == role.value


@pytest.mark.parametrize("name", ["EaDmprPos", "RaDmprPos"])
def test_air_handler_dampers_do_not_confidently_become_the_vav_damper(name):
    # exhaust / return dampers have no CAMBER role; nothing may clear a mapping threshold
    assert all(s.confidence < 0.5 for s in suggest_roles(name, k=5))


@pytest.mark.parametrize(
    "name,expect",
    [
        ("OaTemp", ["oa", "temp"]),
        ("ReHeatVlvPos_1", ["re", "heat", "vlv", "pos"]),
        ("HWVlvPos", ["hw", "vlv", "pos"]),
        ("AHU1_SAT", ["ahu", "sat"]),
        ("zone-022-co2", ["zone", "co2"]),
        ("ZoneCO2", ["zone", "co2"]),
        ("supplyAirTemp", ["supply", "air", "temp"]),
    ],
)
def test_tokenizer_splits_camel_snake_kebab(name, expect):
    from camber.mapping_assist import _norm

    assert _norm(name) == expect


def test_initials_never_match_inside_a_word():
    # 'sat' is the initials of supply_air_temp, not a reason to suggest sat_reset_requests first
    assert suggest_roles("SAT")[0].role == Role.SUPPLY_AIR_TEMP.value
    assert suggest_roles("OaTemp")[0].role != Role.OA_AIRFLOW.value


def test_whole_token_initials_of_a_role():
    top = suggest_roles("AHU2_DSS")[0]
    assert top.role == Role.DUCT_STATIC_SP.value and top.basis == "initials"


def test_misspelled_word_still_matches():
    top = suggest_roles("Suply_Air_Temprature")[0]
    assert top.role == Role.SUPPLY_AIR_TEMP.value and top.basis == "edit_distance"


def test_unit_alone_gives_a_weak_suggestion():
    top = suggest_roles("Meter42", unit="kW")[0]
    assert top.role == Role.POWER.value and top.confidence < 0.5 and top.basis == "unit"


def test_celsius_declared_unit_keeps_temperature_roles():
    # 13-24 degC data: read as degF this is "below freezing" and only the widest-bounded roles
    # (wet-bulb, approach temps) survived the range gate -- every correctly named temp went to
    # wetbulb_temp. The declared unit converts before the range check.
    for name, lo, hi, role in [
        ("DaTemp", 13, 16, Role.SUPPLY_AIR_TEMP),
        ("RaTemp", 20, 24, Role.RETURN_AIR_TEMP),
        ("ZoneTemp_1", 19, 23, Role.SPACE_TEMP),
        ("OaTemp", -5, 25, Role.OAT),
    ]:
        for unit in ("degC", "°C", "C"):
            top = suggest_roles(name, series=_series(lo, hi), unit=unit)[0]
            assert top.role == role.value, (name, unit, top)


def test_celsius_unit_on_the_ml_and_llm_range_gates():
    stub = stub_client("supply_air_temp")
    ok = suggest_roles("DaTemp", suggester=LLMSuggester(stub), series=_series(13, 16), unit="degC")
    bad = suggest_roles("DaTemp", suggester=LLMSuggester(stub), series=_series(13, 16))
    assert ok[0].confidence > bad[0].confidence
