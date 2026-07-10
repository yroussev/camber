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

from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.mapping_assist import (  # noqa: E402
    FeatureSuggester, MLSuggester, LLMSuggester, RoleSuggestion, suggest_roles, review_unmapped,
)
from camber.agent import stub_client  # noqa: E402

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
        Role(s.role)                       # raises if out of vocab
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
    fit = suggest_roles("OAT", series=_series(30, 95))   # squarely inside OAT bounds
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
    return MappingProvider.from_dict({
        "aliases": {"OAT": "oat"},
        "patterns": [[r".*_sat$", "supply_air_temp"]],
    })


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
    assert mp.role_of("Foo_Damper") is None       # still unmapped afterward


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


# --------------------------------------------------------------------------- LLMSuggester (agent seam)

def test_llm_suggester_drops_out_of_vocab_and_labels_basis():
    stub = stub_client("supply_air_temp, oat, flux_capacitor")   # last is not a Role
    out = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub))
    assert [s.role for s in out][:2] == [Role.SUPPLY_AIR_TEMP.value, Role.OAT.value]
    assert all(s.role in {r.value for r in Role} for s in out)   # no out-of-vocab leaked
    assert out[0].basis == "llm"


def test_llm_suggestion_rescored_against_physical_range():
    # the SAME proposed role scores lower when the data contradicts it
    bad = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub_client("supply_air_temp")),
                        series=pd.Series(500.0, index=_IDX))
    good = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub_client("supply_air_temp")),
                         series=_series(52, 58))
    assert bad[0].confidence < good[0].confidence


def test_llm_suggester_empty_when_no_valid_role_proposed():
    out = suggest_roles("AH1_SAT", suggester=LLMSuggester(stub_client("banana, wormhole")))
    assert out == []


# --------------------------------------------------------------------------- MLSuggester ([ml] extra)

def _ml_labels():
    labels = []
    for prefix in ("AH1", "AH2", "RTU3", "AHU7", "MAU4"):
        labels += [(f"{prefix}_SAT", "supply_air_temp"), (f"{prefix}_OAT", "oat"),
                   (f"{prefix}_MAT", "mixed_air_temp"), (f"{prefix}_DmprPos", "oa_damper"),
                   (f"{prefix}_HtgVlv", "heat_valve"), (f"{prefix}_ClgVlv", "cool_valve")]
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
