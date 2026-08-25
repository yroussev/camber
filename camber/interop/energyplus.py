"""Optional EnergyPlus bridge: cross-validate an Option-D counterfactual ([energyplus] extra).

CAMBER's Option-D savings (`camber.mandv.rc_model`) come from a dependency-light grey-box calibrated
to metered energy. For users who want a second, independent opinion, this bridges to **EnergyPlus**
(the U.S. DOE reference whole-building engine) via `eppy`: run a user-supplied IDF under the
as-found and as-corrected control, difference the two annual totals, and compare that avoided energy
to the grey-box number — the same "own it, then cross-check" pattern as `interop.better` /
`interop.pvlib_bridge`. Two independent engines agreeing is far stronger than one.

Optional path — install the extra (the core needs none of it):

    pip install "camber-toolkit[energyplus]"     # eppy (MIT); also needs an installed E+ engine

`eppy` is a pip package but *running* an IDF needs the EnergyPlus engine installed on the machine,
so the default runner is only exercised where E+ is present. The runner is **injectable**
(``compare_option_d(..., runner=...)``) so the comparison logic is fully testable without the
engine — mirroring how the ingest adapters inject a client. Nothing here runs EnergyPlus at import.
"""

from __future__ import annotations

import numpy as np

from ..mandv.rc_model import option_d_savings

__all__ = ["compare_option_d"]


def _require():  # pragma: no cover - exercised only where the optional extra is installed
    try:
        import eppy  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            "the EnergyPlus bridge needs the optional extra: "
            'pip install "camber-toolkit[energyplus]"'
        ) from e
    return eppy


def _default_runner(idf_path, epw_path, schedule):  # pragma: no cover - needs the E+ engine
    """Run ``idf_path`` against ``epw_path`` under ``schedule``; return hourly energy (kWh) ndarray.

    Requires an installed EnergyPlus engine. Provide your own ``runner`` (same signature) to drive a
    different toolchain, or to test without E+.
    """
    _require()
    raise NotImplementedError(
        "wire an EnergyPlus runner: apply the schedule to the IDF, run it against the EPW, and "
        "return the hourly HVAC energy series. Pass runner= to compare_option_d to inject one."
    )


def compare_option_d(
    idf_path,
    epw_path,
    oat,
    as_found_schedule,
    as_corrected_schedule,
    grey_box_calibration,
    *,
    runner=None,
    tol_pct: float = 15.0,
) -> dict:
    """Cross-validate the grey-box Option-D saving against an EnergyPlus run of the same measure.

    Runs the IDF under ``as_found_schedule`` and ``as_corrected_schedule`` (via ``runner``, default
    the installed-E+ runner), differences the two annual totals for the EnergyPlus avoided energy,
    and compares it to :func:`camber.mandv.rc_model.option_d_savings` on the same grey-box
    calibration. Returns each engine's avoided energy plus an ``agreement`` block (pct difference,
    within ``tol_pct``, and whether both agree the measure saves). ``runner(idf_path, epw_path,
    schedule) -> hourly-energy ndarray`` is injectable so this is testable without the E+ engine.
    Requires the ``[energyplus]`` extra for the default runner.
    """
    run = runner or _default_runner
    e_found = float(np.sum(run(idf_path, epw_path, as_found_schedule)))
    e_corr = float(np.sum(run(idf_path, epw_path, as_corrected_schedule)))
    eplus_avoided = e_found - e_corr

    gb = option_d_savings(grey_box_calibration, oat, as_found_schedule, as_corrected_schedule)
    gb_avoided = gb.avoided_energy  # None when the calibration failed G14 acceptance

    if gb_avoided is None:
        pct = float("nan")
        within = False
        signs_agree = False
    else:
        mx = max(abs(eplus_avoided), abs(gb_avoided))
        pct = 100.0 * abs(eplus_avoided - gb_avoided) / mx if mx > 0 else 0.0
        within = pct <= tol_pct
        signs_agree = (eplus_avoided > 0) == (gb_avoided > 0)

    return {
        "energyplus": {
            "avoided_energy": round(eplus_avoided, 3),
            "energy_as_found": round(e_found, 3),
            "energy_as_corrected": round(e_corr, 3),
        },
        "camber": gb.as_dict(),
        "agreement": {
            "avoided_pct_diff": round(pct, 1) if pct == pct else float("nan"),
            "within_tol": bool(within),
            "both_save": bool(signs_agree and (gb_avoided or 0) > 0),
            "grey_box_valid": bool(gb.valid),
        },
    }
