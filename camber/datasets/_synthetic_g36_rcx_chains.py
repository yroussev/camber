"""Synthetic G36 fleet with faults that propagate from an air handler to its boxes (0.108).

The ``g36_rcx_chains`` generator of the ``synthetic-g36-rcx-chains`` catalog entry: five VAV air
handlers with six single-duct reheat boxes each, simulated exactly as the ``g36_vav_fleet``
generator's fleet (:mod:`camber.datasets._synthetic_g36_vav_fleet`, version 3: the same zones,
loads, weather and trim-and-respond resets), hourly for the same three weeks. Each air handler
also trends its supply fan's speed (``SF_SPD``, %) and its cooling valve (``CLG_VLV``, %), so the
air-handler rules that root the RCx report's AHU-to-VAV cause chains (#196) can run.

The five air handlers (``scenario``):

* ``healthy``: nothing wrong, upstream or at the boxes.
* ``one_bad_box``: a healthy air handler with one bad box: box 3's damper is stuck at 30 %, so
  that box alone is short of air and warm. Its problem is its own, not its air handler's.
* ``warm_sat``: the cooling coil cannot make supply air below 63 °F (its valve wide open). The
  SAT reset asks for colder air and gets none: the supply air runs above its setpoint and the
  zones that need cold air warm up.
* ``low_static``: a weak supply fan makes at most 0.35 in. w.c. at full speed. The static reset
  asks for more, the fan runs flat out, and the boxes that need most air are starved.
* ``cold_sat``: the SAT reset is disabled and the setpoint fixed at 55 °F, and boxes 2 and 4 run a
  single-maximum heating sequence (they reheat at 60 % of their cooling maximum airflow): cold
  supply air and reheat at the boxes, the costed chain.

Generated data, not measurements from any building. The column layout is the fleet's, plus the two
air-handler points.
"""

from __future__ import annotations

import pandas as pd

from . import _synthetic_g36_vav_fleet as _fleet_mod
from ._synthetic import Generator, register

__all__: list = []

_SCENARIOS = ("healthy", "one_bad_box", "warm_sat", "low_static", "cold_sat")


def _g36_rcx_chains(seed: int) -> dict:
    """One CSV, hourly, the five air handlers stacked with a ``scenario`` column."""
    idx = pd.date_range(_fleet_mod._START, periods=_fleet_mod._DAYS * 24, freq="1h")
    oat = _fleet_mod._weather(seed, idx)
    frames = []
    for a, scenario in enumerate(_SCENARIOS):
        cols = _fleet_mod._fleet(seed, scenario, a, idx, oat, rich=True)
        frame = pd.DataFrame({"Timestamp": idx.strftime("%Y-%m-%d %H:%M"), "scenario": scenario})
        for name, values in cols.items():
            frame[name] = values
        frames.append(frame)
    text = pd.concat(frames, ignore_index=True).to_csv(index=False, lineterminator="\n")
    return {"g36_rcx_chains.csv": text}


register(
    "g36_rcx_chains",
    Generator(
        version=1,
        files=("g36_rcx_chains.csv",),
        build=_g36_rcx_chains,
        description=(
            "the G36 VAV fleet of g36_vav_fleet (six reheat boxes per air handler under "
            "trim-and-respond resets) with the supply fan's speed and the cooling valve, hourly "
            "for three weeks of spring, as five air handlers: a healthy one, a healthy one with "
            "one stuck box damper, a cooling coil that cannot make cold supply air, a weak supply "
            "fan that cannot make duct static, and a SAT reset disabled at 55 F with two "
            "single-maximum reheat boxes"
        ),
    ),
)
