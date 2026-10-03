"""A step-change test for bills: trigger T1 on a billing meter (provisional; 0.95, issue #74).

The daily trigger T1 (:func:`camber.mandv.rebaseline.assess_triggers`) segments the reporting
days' deviation from the frozen baseline by PELT with a ``3 ln n`` penalty and segments of at least
``min_segment_days`` rows. On bills that test rarely runs: 12 to 36 bills are too few rows for two
28-row segments. Run on bills with a segment of a few bills instead, it raises far too many false
alarms: in the #74 simulation, with a minimum of 6 bills a segment, 13 to 22% of meters with no
change raised one within 36 bills, and 49 to 62% with 3.

This module is the replacement for bills, **opt-in** (``mv[].rebaseline.bill_steps``):

* **The series.** ``x_i = E_i / P_i - 1``: each bill's energy per day relative to the frozen
  baseline's projection for it, over every bill since the baseline ended -- the relative savings
  series the daily T1 uses, one row per bill. Each bill is weighted by its **projected energy**
  (days x projection), so a segment's weighted mean is its fractional deviation in energy,
  ``(sum E - sum P) / sum P``, and a summer bill whose heating projection is near zero cannot
  swamp the test with a huge relative deviation.
* **The test: a scan of the two-sample t statistic.** For every split with at least ``min_run``
  bills on each side, the difference in the weighted means over its standard error, with the
  variance pooled within the two segments and inflated by ``(1 + rho) / (1 - rho)``, where rho is
  the lag-1 autocorrelation of the within-segment residuals, corrected for its small-sample bias
  (``rho + (1 + 3 rho) / n``; Marriott & Pope 1954). The largest ``|t|`` is compared with
  ``threshold``; the split it occurs at dates the step (the first bill after it).
* **Known changes are not steps.** The series is cut at each ECM installation window (ECM date +-
  ``settle_days``) and each declared event (T2, +- ``settle_days``), and each stretch between them
  is scanned on its own, so an ECM's own saving or a declared change is never re-detected.
* **Calibration.** ``threshold`` 3.75 with ``min_run`` 6 bills is the 95th percentile of the
  largest statistic over up to 36 reporting bills, looked at after every bill, on synthetic meters
  with no change: a **5% false-alarm rate per meter over 36 bills** (the SEP 2019 Ed. 2 §4.2
  achievement period, after which T5 calls for a rebaseline anyway). The meters were an office
  (heating and cooling) and a heating-only gas meter, with bills of 28 to 35 days, baselines of 12
  or 24 bills and a monthly CV(RMSE) of 3 to 13%; the rate per kind of meter ranged from 1 to 13%.
  See docs/MANDV.md "Step changes on bills" for the study, its detection rates and delays, and
  where it falls short.

**Where it falls short.** A step becomes testable only once ``min_run`` bills follow it, so the
median delay was 5 to 10 bills. Steps of 20% were found in 61 to 100% of meters, 10% in 21 to
99%, and 5% in 3 to 62%, depending on the meter and the noise. When the meter's residuals drift
slowly (strongly persistent operations noise), 12 to 36 bills cannot tell a drift from a step, and
the false-alarm rate rose to 22% (4 to 41% by kind of meter). So **declared events remain the
recommended path** for a change on a bill-only meter; the scan is a screen that catches large
unannounced changes.

Every name here is provisional (docs/API-STABILITY.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["DEFAULT_MIN_RUN", "DEFAULT_THRESHOLD", "BillStepRule", "scan_statistic", "bill_steps"]

#: The calibrated defaults (#74): a 5% false-alarm rate per meter over 36 bills.
DEFAULT_MIN_RUN = 6
DEFAULT_THRESHOLD = 3.75


@dataclass(frozen=True)
class BillStepRule:
    """The bill step test's settings (``mv[].rebaseline.bill_steps``; provisional).

    ``min_run`` is the fewest bills on each side of a step; ``threshold`` the scan statistic a
    step must exceed. The defaults are the calibrated pair; changing either changes the
    false-alarm rate, which the finding then says it no longer knows.
    """

    min_run: int = DEFAULT_MIN_RUN
    threshold: float = DEFAULT_THRESHOLD

    def __post_init__(self):
        if isinstance(self.min_run, bool) or int(self.min_run) != self.min_run:
            raise ValueError("bill_steps.min_run must be a whole number of bills")
        if int(self.min_run) < 3:
            raise ValueError("bill_steps.min_run must be >= 3")
        if not float(self.threshold) > 0:
            raise ValueError("bill_steps.threshold must be positive")
        object.__setattr__(self, "min_run", int(self.min_run))
        object.__setattr__(self, "threshold", float(self.threshold))

    @property
    def calibrated(self) -> bool:
        """Whether these are the calibrated defaults (a known 5% false-alarm rate)."""
        return self.min_run == DEFAULT_MIN_RUN and self.threshold == DEFAULT_THRESHOLD

    @classmethod
    def from_spec(cls, spec) -> BillStepRule | None:
        """``None`` / ``False`` / ``"off"``: no test; ``True`` / ``"scan"``: the defaults; or
        ``{"min_run": n, "threshold": t}``."""
        if spec is None or spec is False or spec == "off":
            return None
        if spec is True or spec == "scan":
            return cls()
        if isinstance(spec, dict):
            extra = set(spec) - {"min_run", "threshold"}
            if extra:
                raise ValueError(f"mv.rebaseline.bill_steps: unknown key(s) {sorted(extra)}")
            return cls(**spec)
        raise ValueError(
            'mv.rebaseline.bill_steps must be "scan", "off", true/false or '
            '{"min_run": n, "threshold": t}'
        )

    def as_dict(self) -> dict:
        return {"min_run": self.min_run, "threshold": self.threshold, "calibrated": self.calibrated}

    def describe(self) -> str:
        fa = (
            "calibrated to a 5% false-alarm rate per meter over 36 bills"
            if self.calibrated
            else "not the calibrated settings (6 bills, 3.75): the false-alarm rate is unknown"
        )
        return (
            f"scan of the two-sample t of the bills' deviation from the frozen baseline "
            f"projection (at least {self.min_run} bills each side, threshold "
            f"{self.threshold:g}; {fa})"
        )


def scan_statistic(x, w, min_run: int) -> tuple:
    """``(t, k, detail)``: the largest ``|t|`` over splits ``k`` (the first index after the step)
    with at least ``min_run`` rows each side, or ``(0.0, None, {})`` when there are too few rows.

    ``x`` are the relative deviations, ``w`` their weights (the bills' projected energy).
    ``detail`` holds the two means, the pooled variance, rho and the inflation at the best split.
    """
    x = np.asarray(x, dtype=float)
    w = np.asarray(w, dtype=float)
    n = len(x)
    if n < 2 * min_run:
        return 0.0, None, {}
    W = np.cumsum(w)
    S = np.cumsum(w * x)
    best, at, info = 0.0, None, {}
    for k in range(min_run, n - min_run + 1):
        w1, w2 = W[k - 1], W[-1] - W[k - 1]
        m1, m2 = S[k - 1] / w1, (S[-1] - S[k - 1]) / w2
        r = np.concatenate([x[:k] - m1, x[k:] - m2])
        s2 = float(np.sum(w * r * r) / (n - 2))
        rho = float(np.nan_to_num(np.corrcoef(r[:-1], r[1:])[0, 1])) if n > 4 else 0.0
        rho = float(np.clip(rho + (1 + 3 * rho) / n, -0.9, 0.9))
        kappa = max(1.0, (1 + rho) / (1 - rho))
        s2k = s2 * kappa
        if not s2k > 0:
            continue
        t = abs(m2 - m1) / np.sqrt(s2k * (1 / w1 + 1 / w2))
        if t > best:
            best, at = float(t), k
            info = {
                "mean_before": float(m1),
                "mean_after": float(m2),
                "se_rel": float(np.sqrt(s2k * (1 / w1 + 1 / w2))),
                "rho": round(rho, 4),
                "kappa": round(kappa, 4),
            }
    return best, at, info


def _stretches(starts, ends, cuts) -> list:
    """Index ranges of bills between the cut windows (a bill touching a window is left out)."""
    keep = np.ones(len(starts), dtype=bool)
    for c0, c1 in cuts:
        keep &= ~((starts <= c1) & (ends > c0))
    out, cur = [], []
    for i, k in enumerate(keep):
        if k:
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def bill_steps(since: pd.DataFrame, model, rule: BillStepRule, *, schedule=None, events=()) -> list:
    """Steps in the bills since the baseline ended (``since``: a bills frame at the model's bases,
    one row per bill with ``start``, ``end`` (exclusive), ``days`` and ``energy`` per day).

    Returns step dicts in date order -- ``date`` (the first bill after the step), ``delta_rel``,
    ``delta`` (per day, at the post-step projection), ``se``, ``z`` (the scan statistic),
    ``n_before`` / ``n_after`` bills and the scan ``detail`` -- one per stretch between the ECM
    installation windows and declared events whose statistic exceeds ``rule.threshold``.
    """
    from ._mvform import design_rows

    if since is None or not len(since):
        return []
    pred = np.asarray(model.predict(design_rows(since, model)), dtype=float)
    y = since["energy"].to_numpy(float)
    ok = np.isfinite(pred) & (pred > 0) & np.isfinite(y)
    sub = since[ok]
    x = y[ok] / pred[ok] - 1.0
    proj = pred[ok]
    d = sub["days"].to_numpy(float)
    w = d * proj  # each bill's projected energy: a segment's mean is (sum E - sum P) / sum P
    starts = pd.DatetimeIndex(sub["start"]).normalize()
    ends = pd.DatetimeIndex(sub["end"]).normalize()
    cuts = []
    settle = pd.Timedelta(days=int(getattr(schedule, "settle_days", 0) or 0))
    for e in getattr(schedule, "ecm_dates", ()) or ():
        t = pd.Timestamp(e).normalize()
        cuts.append((t - settle, t + settle))
    for ev in events or ():
        t = pd.Timestamp(ev.date).normalize()
        cuts.append((t - settle, t + settle))
    out = []
    for idx in _stretches(starts.values, ends.values, cuts):
        ii = np.asarray(idx)
        t, k, info = scan_statistic(x[ii], w[ii], rule.min_run)
        if k is None or not t > rule.threshold:
            continue
        drel = info["mean_after"] - info["mean_before"]
        level = float(np.average(proj[ii][k:], weights=d[ii][k:]))
        out.append(
            {
                "date": starts[ii[k]],
                "delta_rel": round(drel, 4),
                "delta": round(drel * level, 4),
                "se": round(info["se_rel"] * level, 4),
                "z": round(t, 2),
                "n_before": int(k),
                "n_after": int(len(ii) - k),
                "detail": {k2: v for k2, v in info.items() if k2 in ("rho", "kappa")},
            }
        )
    return sorted(out, key=lambda s: s["date"])
