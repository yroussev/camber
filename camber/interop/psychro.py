"""Optional PsychroLib bridge: exact psychrometrics + a Stull cross-check ([psychro] extra).

CAMBER computes wet-bulb where it needs it (e.g. cooling-tower approach) with Stull's 2011
closed-form approximation -- dependency-free and accurate to ~±1 °F. For users who need
exact psychrometrics (dew point, humidity ratio, enthalpy) or want to validate the
approximation, this bridges to **PsychroLib** (ASHRAE-formulation psychrometrics; MIT).

Optional path -- install the extra (the core needs none of it):

    pip install "camber-toolkit[psychro]"      # PsychroLib (MIT)

PsychroLib is imported lazily and set to IP units (°F, psia, RH as 0-1; this module takes
RH in %). ``compare_wetbulb`` reports CAMBER's Stull value next to PsychroLib's exact value.
"""

from __future__ import annotations

from ..coolingtower import stull_wetbulb_f


def _require():
    try:
        import psychrolib
    except Exception as e:  # noqa: BLE001
        raise ImportError('the PsychroLib bridge needs the optional extra: '
                          'pip install "camber-toolkit[psychro]"') from e
    psychrolib.SetUnitSystem(psychrolib.IP)
    return psychrolib


def wet_bulb_f(tdb_f, rh_pct, *, pressure_psia: float = 14.696) -> float:
    """Exact wet-bulb temperature (°F) from dry-bulb (°F) and RH (%) via PsychroLib."""
    p = _require()
    return float(p.GetTWetBulbFromRelHum(float(tdb_f), float(rh_pct) / 100.0,
                                         float(pressure_psia)))


def psychrometrics(tdb_f, rh_pct, *, pressure_psia: float = 14.696) -> dict:
    """Full psychrometric state (°F dry-bulb, RH %) -> wet-bulb, dew point, w, enthalpy."""
    p = _require()
    tdb, rh, press = float(tdb_f), float(rh_pct) / 100.0, float(pressure_psia)
    w = p.GetHumRatioFromRelHum(tdb, rh, press)
    return {"wet_bulb_f": round(float(p.GetTWetBulbFromRelHum(tdb, rh, press)), 2),
            "dew_point_f": round(float(p.GetTDewPointFromRelHum(tdb, rh)), 2),
            "humidity_ratio": round(float(w), 5),
            "enthalpy_btu_per_lb": round(float(p.GetMoistAirEnthalpy(tdb, w)), 2)}


def compare_wetbulb(tdb_f, rh_pct, *, pressure_psia: float = 14.696) -> dict:
    """CAMBER's Stull wet-bulb vs PsychroLib's exact value (validate the approximation)."""
    exact = wet_bulb_f(tdb_f, rh_pct, pressure_psia=pressure_psia)
    approx = float(stull_wetbulb_f(tdb_f, rh_pct))
    return {"stull_f": round(approx, 2), "psychrolib_f": round(exact, 2),
            "abs_diff_f": round(abs(approx - exact), 2)}
