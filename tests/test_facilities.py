"""Facility identity for the store: id derivation, path-safe validation, registry, migration.

The point of a stable, path-safe ``facility_id`` is that a portfolio scales without name
collisions, rename-orphaning, or the filesystem-encoding hazard a raw name causes as a partition
directory -- so these tests also lock the data-loss regression the id prevents.
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.store import (  # noqa: E402
    FacilityRegistry,
    ParquetStore,
    make_facility_id,
    migrate_site_to_facility,
    require_facility_id,
    valid_facility_id,
)


def _frame(n=48):
    idx = pd.date_range("2024-07-01", periods=n, freq="h")
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: np.linspace(55, 60, n)}, index=idx)


# --------------------------------------------------------------------------- id + validation


def test_make_facility_id_deterministic_and_path_safe():
    a, b = make_facility_id("Fox Lodge"), make_facility_id("Fox Lodge")
    assert a == b and valid_facility_id(a) and a.startswith("fox-lodge-")
    # distinct seeds -> distinct ids; a seed with no alnum degrades to the bare hash
    assert make_facility_id("Fox Lodge") != make_facility_id("Bear Lodge")
    assert valid_facility_id(make_facility_id("日本語ビル"))


@pytest.mark.parametrize("bad", ["My Building", "a/b", "a=b", "", " x", "héllo", "x" * 300])
def test_require_facility_id_rejects_unsafe(bad):
    with pytest.raises(ValueError, match="facility_id"):
        require_facility_id(bad)


@pytest.mark.parametrize(
    "ok", ["S", "DemoSite", "site_000", "bldg.a17", "Fox_lodging_Stephen", "x"]
)
def test_valid_facility_id_accepts_safe(ok):
    assert valid_facility_id(ok) and require_facility_id(ok) == ok


# --------------------------------------------------------------------------- registry


def test_registry_round_trip_and_collision(tmp_path):
    reg = FacilityRegistry(str(tmp_path))
    reg.register("fox-lodge-9f3a1c", name="Fox Lodge", climate_zone="CA CZ15")
    assert reg.name("fox-lodge-9f3a1c") == "Fox Lodge"
    assert reg.get("fox-lodge-9f3a1c")["climate_zone"] == "CA CZ15"
    assert reg.name("never-registered") == "never-registered"  # id fallback
    with pytest.raises(ValueError, match="already registered"):
        reg.register("fox-lodge-9f3a1c", name="A Different Building")
    assert reg.remove("fox-lodge-9f3a1c") and reg.all() == {}


# --------------------------------------------------------------------------- the data-loss fix


def test_unsafe_facility_id_rejected_at_write(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    # a space in the name would URL-encode the partition dir and silently overwrite parts;
    # it is now rejected up front -- derive a safe id instead.
    with pytest.raises(ValueError, match="facility_id"):
        st.write_role_frame(_frame(), facility_id="My Building", equip="AHU_1")
    fid = make_facility_id("My Building")
    assert st.write_role_frame(_frame(), facility_id=fid, equip="AHU_1", name="My Building") > 0
    assert st.facilities() == [fid] and st.facility_name(fid) == "My Building"


# --------------------------------------------------------------------------- migration


def test_migrate_site_store_to_facility(tmp_path):
    root = str(tmp_path / "db")
    st = ParquetStore(root)
    st.write_role_frame(_frame(), facility_id="DemoSite", equip="AHU_1", equip_class="AHU")
    before = st.read_role_frame(facility_id="DemoSite", equip="AHU_1")
    # simulate a pre-feature store: rename the partition dir back to site=, drop the sidecars
    os.rename(os.path.join(root, "facility_id=DemoSite"), os.path.join(root, "site=DemoSite"))
    for side in ("_facilities.json", "_catalog.json"):
        p = os.path.join(root, side)
        if os.path.isfile(p):
            os.remove(p)

    assert migrate_site_to_facility(root) == 1
    assert migrate_site_to_facility(root) == 0  # idempotent
    st2 = ParquetStore(root)
    assert st2.facilities() == ["DemoSite"]
    assert st2.facility_name("DemoSite") == "DemoSite"  # old value recorded as the name
    after = st2.read_role_frame(facility_id="DemoSite", equip="AHU_1")
    pd.testing.assert_frame_equal(before, after)


# --------------------------------------------------------------------------- deprecation alias


def test_sites_alias_deprecated_but_works(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    st.write_role_frame(_frame(), facility_id="S", equip="AHU_1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert st.sites() == ["S"]  # same result as facilities()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
