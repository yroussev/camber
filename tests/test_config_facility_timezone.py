"""0.100 (#104): config runs resolve a store facility's zone the way the read API does.

One helper (:func:`camber._provenance.facility_timezone`) serves both: the registry entry's
``timezone``, then the dataset-catalog block and entry, then the open-fdd provenance."""

import warnings

import pandas as pd
import pytest

from camber._provenance import facility_timezone
from camber.api import ReadAPI
from camber.api.read import _facility_timezone
from camber.config import _prepare, _site_timezone
from camber.model.roles import Role
from camber.store import FacilityRegistry, ParquetStore

CHI, NY, OSLO = "America/Chicago", "America/New_York", "Europe/Oslo"

#: facility id -> registry metadata
FACILITIES = {
    # an explicit zone on another facility wins over its open-fdd provenance (the #104 case)
    "EXPLICIT": {"timezone": OSLO, "openfdd": {"timezone": CHI}},
    "OPENFDD": {"openfdd": {"timezone": NY}},  # a 0.99.0 ingest: provenance only
    "BLOCK": {"dataset": {"dataset_id": "bts", "local_timezone": "Australia/Sydney"}},
    "BLOCKTZ": {"dataset": {"dataset_id": "bdg2", "timezone": "US/Mountain"}},  # per-site zone
    "CATALOG": {"dataset": {"dataset_id": "lbnl-b59"}, "openfdd": {"timezone": NY}},
    "BAD": {"timezone": "not a zone"},
    "NONE": {},
}
EXPECTED = {
    "EXPLICIT": OSLO,
    "OPENFDD": NY,
    "BLOCK": "Australia/Sydney",
    "BLOCKTZ": "US/Mountain",
    "CATALOG": "America/Los_Angeles",
    "BAD": None,
    "NONE": None,
}


@pytest.fixture
def store(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    idx = pd.date_range("2025-03-01", periods=48, freq="1h")
    frame = pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: 55.0, Role.OAT: 60.0, Role.MIXED_AIR_TEMP: 62.0}, index=idx
    )
    reg = FacilityRegistry(st.root)
    for fid, meta in FACILITIES.items():
        st.write_role_frame(frame, facility_id=fid, equip="AHU_1", equip_class="AHU", name=fid)
        if meta:
            reg.register(fid, **meta)
    return st


def _cfg(st, fid, **source):
    return {
        "source": {"kind": "store", "store": st.root, "facility_id": fid, **source},
        "equipment": [{"class": "AHU"}],
        "rules": [],
    }


def test_config_runs_and_the_read_api_agree_on_every_facility(store):
    rows = {f["facility_id"]: f for f in ReadAPI(store).facilities()["facilities"]}
    for fid, want in EXPECTED.items():
        assert rows[fid].get("timezone") == want, fid
        prep = _prepare(_cfg(store, fid), ".")
        assert prep.timezone == want, fid
        assert [r.equip for r in prep.refs] == ["AHU_1"], fid


def test_one_shared_helper():
    for fid, meta in FACILITIES.items():
        assert facility_timezone(meta) == _facility_timezone(meta) == EXPECTED[fid], fid
    assert facility_timezone(None) is None


def test_an_explicit_source_zone_still_wins_and_warns_against_the_facility_zone(store):
    with pytest.warns(UserWarning, match="differs from the zone"):
        prep = _prepare(_cfg(store, "EXPLICIT", timezone=CHI), ".")
    assert prep.timezone == CHI
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _site_timezone({"timezone": OSLO}, FACILITIES["EXPLICIT"])["timezone"] == OSLO
        # a facility with no recorded zone keeps the source's zone, or none
        assert _site_timezone({}, {})["timezone"] is None
