"""Optional pvlib bridge: irradiance transposition + temperature-aware PV yield ([pv] extra).

CAMBER's `camber.pv` tracks a PV array against a *measured* plane-of-array (POA) resource with
a flat assumed performance ratio -- enough for monitoring (performance ratio, specific yield,
net energy). It deliberately does not model the solar resource or cell temperature. When you
need to *estimate* expected generation from weather -- transpose horizontal irradiance (GHI/
DNI/DHI) onto the array plane, or apply a temperature-derated PVWatts yield -- this bridges to
**pvlib** (the reference open-source PV modeling library; BSD-3).

Optional path -- install the extra (the core needs none of it):

    pip install "camber-toolkit[pv]"            # pvlib (BSD-3)

pvlib is imported lazily. `compare_expected` puts CAMBER's flat-PR estimate next to pvlib's
temperature-corrected PVWatts yield, so you can see the derate the simple model omits.
"""

from __future__ import annotations

import numpy as np


def _require():
    try:
        import pvlib
    except Exception as e:  # noqa: BLE001
        raise ImportError('the pvlib bridge needs the optional extra: '
                          'pip install "camber-toolkit[pv]"') from e
    return pvlib


def poa_from_ghi(ghi, dni, dhi, *, solar_zenith, solar_azimuth,
                 surface_tilt: float, surface_azimuth: float, albedo: float = 0.2):
    """Transpose horizontal irradiance onto the array plane (POA global, W/m²) via pvlib.

    Inputs are array-likes (W/m²) plus the solar position (degrees) for each interval and the
    fixed array orientation (tilt from horizontal, azimuth clockwise from north). Returns the
    POA global irradiance series — the resource CAMBER's `pv` module then meters against.
    """
    pvlib = _require()
    tot = pvlib.irradiance.get_total_irradiance(
        surface_tilt=float(surface_tilt), surface_azimuth=float(surface_azimuth),
        solar_zenith=np.asarray(solar_zenith, dtype=float),
        solar_azimuth=np.asarray(solar_azimuth, dtype=float),
        dni=np.asarray(dni, dtype=float), ghi=np.asarray(ghi, dtype=float),
        dhi=np.asarray(dhi, dtype=float), albedo=albedo)
    return np.asarray(tot["poa_global"], dtype=float)


def pvwatts_expected_kwh(poa_kwh_m2: float, rated_kw: float, *, cell_temp_c: float = 25.0,
                         gamma_pdc: float = -0.0047, system_losses: float = 0.14) -> float:
    """Expected AC energy (kWh) for a POA insolation via pvlib's PVWatts DC model + losses.

    Uses pvlib's `pvwatts_dc` as the (linear) energy kernel: insolation in kWh/m² scaled to
    the 1 kW/m² reference, derated for cell temperature (`gamma_pdc`, %/°C as a fraction),
    times rated DC capacity, then a flat system-loss factor for the AC side. Unlike CAMBER's
    flat-PR `expected_generation`, this captures the temperature derate.
    """
    pvlib = _require()
    # first arg passed positionally: pvlib renamed the keyword (g_poa_effective ->
    # effective_irradiance) in 0.13, but the positional contract is stable across versions.
    dc = pvlib.pvsystem.pvwatts_dc(1000.0 * float(poa_kwh_m2), float(cell_temp_c),
                                   float(rated_kw), gamma_pdc)
    return round(float(dc) * (1.0 - float(system_losses)), 2)


def compare_expected(poa_kwh_m2: float, rated_kw: float, *, cell_temp_c: float = 45.0,
                     performance_ratio: float = 0.80, gamma_pdc: float = -0.0047,
                     system_losses: float = 0.14) -> dict:
    """CAMBER's flat-PR expected generation vs pvlib's temperature-corrected PVWatts yield."""
    from ..pv import expected_generation
    camber = expected_generation(poa_kwh_m2, rated_kw, performance_ratio=performance_ratio)
    pv = pvwatts_expected_kwh(poa_kwh_m2, rated_kw, cell_temp_c=cell_temp_c,
                              gamma_pdc=gamma_pdc, system_losses=system_losses)
    return {"camber_kwh": camber, "pvlib_kwh": pv,
            "ratio": round(pv / camber, 3) if camber else float("nan"),
            "cell_temp_c": cell_temp_c}
