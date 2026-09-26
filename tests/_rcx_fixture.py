"""A small, deterministic store-backed site for the RCx report tests (neutral names only)."""

import numpy as np
import pandas as pd

from camber.model.roles import Role
from camber.store import ParquetStore

FID = "demo-fac"
START = "2026-03-02"  # a Monday
NC = {
    "dataset_id": "demo-nc",
    "title": "Demo research-only data",
    "publisher": "Demo Lab",
    "licence": "CC-BY-NC-4.0",
    "access": "research_only",
    "citation": "Demo Lab (2026). Demo data.",
}


def ahu_frame(*, fault_week=None, reheat_week=None, weeks=3, seed=0, fan=True):
    """Hourly AHU trends. ``fault_week`` (0-based) holds the OA damper open in hot weather;
    ``reheat_week`` opens the heating valve while the cooling coil runs."""
    idx = pd.date_range(START, periods=24 * 7 * weeks, freq="h")
    rng = np.random.default_rng(seed)
    h = idx.hour.to_numpy()
    day = np.arange(len(idx)) // 24
    oat = 62 + 18 * np.sin((h - 9) / 24 * 2 * np.pi) + 4 * np.sin(day / 5.0)
    oat = oat + rng.normal(0, 1, len(idx))
    running = (idx.dayofweek < 5) & (h >= 6) & (h < 19)
    rat = 73 + rng.normal(0, 0.4, len(idx))
    oaf = np.where(oat < 68, 0.55, 0.2)
    if fault_week is not None:
        wk = (np.arange(len(idx)) // (24 * 7)) == fault_week
        oaf = np.where(wk & (oat >= 68), 0.8, oaf)
    mat = rat - oaf * (rat - oat)
    cool = np.where(running & (oat > 60), 45.0, 0.0)
    # cooling coil active -> 55 °F supply; economizer-only -> mixed air plus ~1 °F of fan heat
    sat = np.where(cool > 0, 55.0, mat + 1.0) + rng.normal(0, 0.3, len(idx))
    sat = np.where(running, sat, 70.0)
    heat = np.zeros(len(idx))
    if reheat_week is not None:
        wk = (np.arange(len(idx)) // (24 * 7)) == reheat_week
        heat = np.where(wk & (cool > 0), 30.0, 0.0)
    cols = {
        Role.OAT: oat,
        Role.RETURN_AIR_TEMP: np.where(running, rat, 70.0),
        Role.MIXED_AIR_TEMP: np.where(running, mat, 70.0),
        Role.SUPPLY_AIR_TEMP: sat,
        Role.OA_DAMPER: np.where(running, 100 * oaf, 0.0),
        Role.COOL_VALVE: cool,
        Role.HEAT_VALVE: heat,
        Role.DUCT_STATIC: np.where(running, 1.5 + rng.normal(0, 0.05, len(idx)), 0.0),
    }
    if fan:
        cols[Role.SUPPLY_FAN_STATUS] = running.astype(float)
    return pd.DataFrame(cols, index=idx)


def make_store(tmp_path, *, meta=NC, fault_week=1):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(
        ahu_frame(fault_week=fault_week), facility_id=FID, equip="DemoAHU", equip_class="AHU"
    )
    st.write_role_frame(
        ahu_frame(seed=1, reheat_week=2), facility_id=FID, equip="DemoAHU2", equip_class="AHU"
    )
    st.register_facility(FID, name="Demo facility", **({"dataset": meta} if meta else {}))
    return st


def config(**report):
    return {
        "site": "Demo facility",
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": [
            {"name": "economizer_high_limit", "params": {"high_limit_f": 68.0, "min_oa_pct": 30.0}},
            "outdoor_air_fraction",
            "supply_air_reset",
            "supply_air_reset_compliance",
            "simultaneous_heat_cool",
            "leaking_valve",
        ],
        "report": {"layout": "rcx", "rcx": dict(report)},
    }
