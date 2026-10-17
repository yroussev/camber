"""Which point is a VAV box's *minimum* airflow setpoint? (0.106, #186)

``airflow_sp`` has carried two meanings. The static-pressure census, ``airflow_tracking`` and the
cohort rules read it as the box's **active** airflow setpoint (ASHRAE Guideline 36's: the airflow
the controller asks for right now, anywhere between the minimum and the cooling or heating
maximum). The reheat-at-minimum rules (``reheat_minimization_g36``, ``zones_heat_cool_census``,
``reheat_penalty``, ``overcooling_min_flow``) read it as the box's **minimum**. With an active
setpoint mapped, "airflow above its setpoint" almost never happens (the damper tracks the
setpoint), so those rules went nearly blind.

The minimum now has its own role, ``airflow_min_sp``, which these rules prefer. With only
``airflow_sp`` mapped they still read it as the minimum (the behaviour before 0.106), unless it
moves like an active setpoint -- it swings widely and the airflow tracks it -- in which case the
minimum is inferred as a low percentile of that setpoint and the finding says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role

# An active setpoint swings with load: its 90th percentile is at least this multiple of its 10th
# (a minimum setpoint is flat, or steps with occupancy or a ventilation reset).
ACTIVE_SPREAD = 1.5
# ... and the airflow tracks it: the median |airflow / setpoint - 1| is at most this.
ACTIVE_TRACKING = 0.10
# The minimum inferred from an active setpoint: this percentile of its positive values.
INFERRED_MIN_PCTL = 5.0
_MIN_SAMPLES = 24

#: ``basis`` values reported in ``metrics["min_flow_basis"]``
BASIS_MIN_SP = "airflow_min_sp"
BASIS_SP_AS_MIN = "airflow_sp read as the minimum"
BASIS_INFERRED = "inferred from the active airflow_sp"


def airflow_sp_looks_active(frame: pd.DataFrame, keep: pd.Series | None = None) -> bool:
    """True when ``airflow_sp`` moves like an active airflow setpoint rather than a minimum.

    Over the samples ``keep`` selects (default: all) where the setpoint is positive and the
    airflow is trended, the setpoint must swing (90th / 10th percentile >= :data:`ACTIVE_SPREAD`)
    *and* the airflow must track it (median ``|airflow / sp - 1|`` <= :data:`ACTIVE_TRACKING`).
    A minimum setpoint stays put while the airflow rises above it in cooling. Fewer than 24
    usable samples: False (not enough to tell; the setpoint is read as the minimum, as before).
    """
    if Role.AIRFLOW_SP not in frame.columns or Role.AIRFLOW not in frame.columns:
        return False
    sp = pd.to_numeric(frame[Role.AIRFLOW_SP], errors="coerce")
    flow = pd.to_numeric(frame[Role.AIRFLOW], errors="coerce")
    ok = sp.notna() & flow.notna() & (sp > 0)
    if keep is not None:
        ok &= keep.reindex(frame.index).fillna(False).astype(bool)
    if int(ok.sum()) < _MIN_SAMPLES:
        return False
    s, f = sp[ok].to_numpy(float), flow[ok].to_numpy(float)
    lo, hi = np.percentile(s, [10.0, 90.0])
    spread = hi / lo if lo > 0 else np.inf
    tracking = float(np.median(np.abs(f / s - 1.0)))
    return bool(spread >= ACTIVE_SPREAD and tracking <= ACTIVE_TRACKING)


def min_flow_series(
    frame: pd.DataFrame, keep: pd.Series | None = None
) -> tuple[pd.Series | None, str | None]:
    """``(minimum airflow setpoint series, basis)`` for one box, or ``(None, None)``.

    ``airflow_min_sp`` when it is mapped; else ``airflow_sp`` read as the minimum; else, when
    ``airflow_sp`` looks active (:func:`airflow_sp_looks_active` over ``keep``), a constant
    minimum inferred as the 5th percentile of its positive values over ``keep``.
    """
    if Role.AIRFLOW_MIN_SP in frame.columns:
        return pd.to_numeric(frame[Role.AIRFLOW_MIN_SP], errors="coerce"), BASIS_MIN_SP
    if Role.AIRFLOW_SP not in frame.columns:
        return None, None
    sp = pd.to_numeric(frame[Role.AIRFLOW_SP], errors="coerce")
    if not airflow_sp_looks_active(frame, keep):
        return sp, BASIS_SP_AS_MIN
    pos = sp[sp > 0]
    if keep is not None:
        pos = pos[keep.reindex(pos.index).fillna(False).astype(bool)]
    vmin = float(np.percentile(pos.to_numpy(float), INFERRED_MIN_PCTL))
    return pd.Series(vmin, index=frame.index, dtype=float), BASIS_INFERRED


INFERRED_CAVEAT = (
    "airflow_sp moves like the box's active airflow setpoint (it swings with load and the "
    "airflow tracks it), not its minimum: the minimum was inferred as the 5th percentile of "
    "airflow_sp; map the box's minimum airflow setpoint as airflow_min_sp"
)
