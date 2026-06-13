"""Optional bridge to NREL PySAM's UtilityRate5 for full-fidelity URDB billing.

The native engine (:mod:`camber.tariff`) covers the common tariff structures with no
dependency. For exotic URDB rates -- coincident demand, seasonal ratchets, deeply nested
look-back tiers -- this bridges to NREL PySAM's battle-tested ``Utilityrate5`` model,
which implements the full URDB semantics. It is an **optional** path: install the extra

    pip install "camber-toolkit[tariff]"      # pulls NREL-PySAM (BSD-3-Clause, ~47 MB binary)

PySAM is imported lazily, so the core stays dependency-free. Hand it a URDB rate JSON
(from :func:`camber.interop.openei.fetch_urdb_rate`) and an 8760-hour load (kW); it
returns the year-one utility bill. Cross-check the native engine against this for any
tariff you rely on -- the same role CAMBER plays with eemeter for M&V.
"""

from __future__ import annotations

import numpy as np


def _require_pysam():
    try:
        import PySAM.Utilityrate5 as ur5            # noqa: F401
        from PySAM.ResourceTools import URDBv8_to_ElectricityRates  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            "the NREL PySAM bridge needs the optional extra: "
            'pip install "camber-toolkit[tariff]"'
        ) from e
    return ur5, URDBv8_to_ElectricityRates


def bill_with_pysam(urdb_rate: dict, load_kw_8760, *, analysis_period: int = 1) -> dict:
    """Year-one utility bill for an 8760-hour kW load under ``urdb_rate`` via PySAM.

    ``urdb_rate`` is a URDB rate JSON dict (as returned by ``fetch_urdb_rate``);
    ``load_kw_8760`` is an iterable of 8760 average-kW values. Returns
    ``{"annual_bill", "monthly"}``. Requires the ``[tariff]`` extra.
    """
    ur5, urdb_to_rates = _require_pysam()
    load = [float(x) for x in load_kw_8760]
    if len(load) != 8760:
        raise ValueError(f"load_kw_8760 must have 8760 values, got {len(load)}")

    rates = urdb_to_rates({"items": [urdb_rate]} if "items" not in urdb_rate else urdb_rate)
    ur = ur5.new()
    ur.assign({"Electricity Rates": rates})
    ur.Lifetime.analysis_period = analysis_period
    ur.Lifetime.system_use_lifetime_output = 0
    ur.Lifetime.inflation_rate = 0
    ur.SystemOutput.gen = [0.0] * 8760           # load-only (no on-site generation)
    ur.SystemOutput.degradation = [0.0]
    ur.Load.load = load
    ur.execute(0)
    out = ur.Outputs.export()
    annual = float(out.get("utility_bill_w_sys_year1")
                   or out.get("elec_cost_with_system_year1") or 0.0)
    monthly = list(out.get("utility_bill_w_sys_ym", []) or [])
    return {"annual_bill": round(annual, 2), "monthly": monthly}
