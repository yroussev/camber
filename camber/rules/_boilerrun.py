"""The boiler run status the hot-water plant rules read, with a gas-input fallback (0.98, #86).

``boiler_summer_lockout``, ``boiler_short_cycle`` and ``hw_plant_deltat`` need to know when the
boiler fired. The strongest evidence is its run status (``boiler_status``). A plant that trends
no run status but does trend the boiler's gas input rate (``gas_input_rate``) can still answer:
the boiler fired in the samples where its gas input exceeded a small share of its own 95th
percentile (:func:`camber.schedules.plant_run_mask`, ``"hw"`` loop). The three rules declare
``roles_any_of = ((BOILER_STATUS, GAS_INPUT_RATE),)``, so either input lets them run.

A frame that carries a usable run status is returned unchanged, so every finding on such a frame
is byte-identical to earlier releases.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role

#: The any-of group the three rules declare: a run status, or the gas input to infer one from.
BOILER_RUN_ANY_OF = ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)

#: The ``run_source`` metric of a finding whose firing hours were read from the gas input.
RUN_SOURCE_GAS = "gas"

#: The caveat on every finding judged from the gas-input fallback.
GAS_RUN_CAVEAT = (
    "no boiler run status mapped: firing read from the gas input rate above 5% of its own "
    "95th percentile, on the resampled trend -- a firing shorter than the resample interval is "
    "invisible, so starts and firing hours are approximate; map the burner's firing or flame "
    "signal (boiler_status) for a firmer gate"
)


def _has_status(frame: pd.DataFrame) -> bool:
    if Role.BOILER_STATUS not in frame.columns:
        return False
    return bool(pd.to_numeric(frame[Role.BOILER_STATUS], errors="coerce").notna().any())


def with_boiler_status(frame: pd.DataFrame) -> tuple:
    """``(frame, run_source)`` with a ``boiler_status`` column the rules can read.

    ``run_source`` is ``None`` when the frame already carries a usable run status (the frame is
    returned as is), or :data:`RUN_SOURCE_GAS` when the status was built from the gas input
    (a 0/1 firing series; samples with no gas reading stay missing rather than counting as
    "off", so a trend gap never reads as a stop and restart). When neither yields a status, the
    frame is returned unchanged with ``run_source`` ``None`` and the rule declines as before.
    """
    if _has_status(frame) or Role.GAS_INPUT_RATE not in frame.columns:
        return frame, None
    from ..schedules import plant_run_mask

    gas_only = frame[[Role.GAS_INPUT_RATE]]
    run, _src = plant_run_mask(gas_only, "hw")
    if run is None:
        return frame, None
    gas = pd.to_numeric(frame[Role.GAS_INPUT_RATE], errors="coerce")
    status = run.astype(float).where(gas.notna())
    out = frame.copy()
    out[Role.BOILER_STATUS] = status
    return out, RUN_SOURCE_GAS
