"""Data-quality assessment and cleaning with an audit trail (capability-map §1).

Garbage at ingest poisons every layer above it, so before a series feeds a
diagnostic or the M&V engine it is worth knowing: how much is missing, are there
gaps in the time grid, is the sensor stuck (flatlined), and are there physically
implausible spikes. This module answers that with :func:`assess` (a read-only
:class:`QualityReport`) and optionally repairs it with :func:`clean`, which
returns the cleaned series **plus a log of exactly what it changed** -- cleaning
that cannot be audited is its own kind of garbage.

Methods are deliberately simple and robust (median/MAD, not mean/std, so the
outlier test is not itself skewed by the outliers): pandas + numpy only.

A note on flatlines: many control points are legitimately constant (a setpoint, a
status held at 0/1). ``assess`` reports flatline extent as a neutral signal and
the composite score weights it lightly; treat a high flatline fraction as "look
here," not "bad data," and use the role to judge.

A matching caveat for outliers on *intermittent* signals: the robust test assumes
the bulk of the series is the normal regime, so a mostly-zero meter with real
bursts (e.g. an HHW BTU meter that is near-zero except during heating events)
flags every legitimate burst as an outlier and scores low. That is a property of
the signal's shape, not bad data -- for intermittent/event-driven points read the
outlier count as "this isn't a smooth sensor," not "this is broken." A
regime-aware check is future work; today, interpret the score with the role and
the expected duty cycle in mind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Iglewicz-Hoaglin robust-outlier cutoff on the modified (MAD-based) z-score.
_MAD_Z_CUTOFF = 3.5
# 0.6745 = inverse normal CDF at 0.75; scales MAD to a std-equivalent.
_MAD_SCALE = 0.6745
# meanAD fallback scale (Iglewicz-Hoaglin), used when MAD collapses to 0 because
# more than half the values are identical -- common for near-constant BAS points
# with an occasional spike, where plain MAD would miss the spike entirely.
_MEANAD_SCALE = 1.253314


def infer_freq(index: pd.DatetimeIndex):
    """Best-guess sampling interval as the modal gap between samples, or None."""
    if index is None or len(index) < 3:
        return None
    diffs = pd.Series(index).diff().dropna()
    if diffs.empty:
        return None
    mode = diffs.mode()
    return mode.iloc[0] if len(mode) else diffs.median()


def _mad_z(values: np.ndarray) -> np.ndarray:
    """Modified z-score per point; all-zero only when the series is truly constant.

    Uses the MAD-based modified z-score, falling back to the meanAD-based form
    when MAD is 0 (>50% identical values) so a spike against a near-constant
    baseline is still caught. Returns zeros only when there is no spread at all.
    """
    med = np.median(values)
    dev = np.abs(values - med)
    mad = np.median(dev)
    if mad > 0:
        return _MAD_SCALE * (values - med) / mad
    mean_ad = np.mean(dev)
    if mean_ad > 0:
        return (values - med) / (_MEANAD_SCALE * mean_ad)
    return np.zeros_like(values, dtype="float64")


def outlier_mask(series: pd.Series, cutoff: float = _MAD_Z_CUTOFF) -> pd.Series:
    """Boolean mask of robust (MAD-based) outliers among the non-null values."""
    s = series.dropna()
    mask = pd.Series(False, index=series.index)
    if len(s) < 3:
        return mask
    z = np.abs(_mad_z(s.to_numpy(dtype="float64")))
    mask.loc[s.index] = z > cutoff
    return mask


def longest_flatline(series: pd.Series) -> int:
    """Length of the longest run of identical consecutive (non-null) values."""
    s = series.dropna()
    if s.empty:
        return 0
    changed = s.ne(s.shift())
    run_id = changed.cumsum()
    return int(run_id.value_counts().max())


def gap_count(index: pd.DatetimeIndex, expected) -> int:
    """Number of inter-sample gaps longer than 1.5x the expected interval."""
    if expected is None or len(index) < 2:
        return 0
    diffs = pd.Series(index).diff().dropna()
    return int((diffs > 1.5 * expected).sum())


@dataclass(frozen=True)
class QualityReport:
    """Read-only quality summary for one point series."""

    n: int                      # samples present (non-null)
    n_missing: int              # NaN samples in the series
    coverage: float             # non-null fraction, 0..1
    n_gaps: int                 # time-grid gaps > 1.5x expected interval
    longest_flatline: int       # longest stuck run (identical consecutive values)
    flatline_frac: float        # longest_flatline / n, 0..1
    n_outliers: int             # robust (MAD) outliers
    outlier_frac: float         # n_outliers / n, 0..1
    expected_freq: object       # inferred/declared interval (Timedelta or None)
    score: float                # composite quality 0..1 (1 = clean)

    def as_dict(self):
        """Return as a plain dict (expected_freq stringified)."""
        d = self.__dict__.copy()
        d["expected_freq"] = str(self.expected_freq)
        return d


def assess(series: pd.Series, expected_freq=None) -> QualityReport:
    """Compute a :class:`QualityReport` without modifying the series.

    ``expected_freq`` (a pandas-parseable interval) overrides the inferred
    sampling interval used for gap detection.
    """
    total = len(series)
    n_missing = int(series.isna().sum())
    n = total - n_missing
    coverage = (n / total) if total else 0.0
    exp = pd.Timedelta(expected_freq) if expected_freq is not None \
        else infer_freq(series.index)
    n_gaps = gap_count(series.index, exp)
    flat = longest_flatline(series)
    flat_frac = (flat / n) if n else 0.0
    n_out = int(outlier_mask(series).sum())
    out_frac = (n_out / n) if n else 0.0

    # Composite: coverage dominates; outliers penalize moderately; an extreme
    # flatline (whole series stuck) contributes lightly since some points are
    # legitimately constant.
    score = coverage * (1.0 - min(out_frac * 2.0, 1.0)) * (1.0 - 0.2 * flat_frac)
    score = float(max(0.0, min(1.0, score)))
    return QualityReport(
        n=n, n_missing=n_missing, coverage=round(coverage, 4),
        n_gaps=n_gaps, longest_flatline=flat, flatline_frac=round(flat_frac, 4),
        n_outliers=n_out, outlier_frac=round(out_frac, 4),
        expected_freq=exp, score=round(score, 4),
    )


@dataclass
class CleaningLog:
    """An auditable record of what :func:`clean` changed."""

    steps: list = field(default_factory=list)   # list[dict]: op, n_affected, detail

    def add(self, op: str, n_affected: int, detail: str = ""):
        """Record one cleaning step (operation, count affected, detail)."""
        self.steps.append({"op": op, "n_affected": int(n_affected), "detail": detail})

    @property
    def total_changed(self) -> int:
        """Total samples changed across all logged steps."""
        return sum(s["n_affected"] for s in self.steps)


def clean(series: pd.Series, *, drop_outliers: bool = False,
          fill_limit: int = 0, outlier_cutoff: float = _MAD_Z_CUTOFF):
    """Return ``(cleaned_series, CleaningLog)`` applying opt-in repairs.

    - ``drop_outliers``: replace robust outliers with NaN (so they don't bias a
      regression) before any fill.
    - ``fill_limit``: forward-fill at most this many consecutive NaNs (0 = no
      fill). Gaps longer than the limit stay NaN -- honest holes beat invented
      data.

    Every action is recorded in the returned :class:`CleaningLog`; nothing is
    changed silently.
    """
    out = series.copy()
    log = CleaningLog()
    if drop_outliers:
        mask = outlier_mask(out, cutoff=outlier_cutoff)
        if mask.any():
            out[mask] = np.nan
            log.add("drop_outliers", mask.sum(),
                    f"MAD z>{outlier_cutoff} set to NaN")
    if fill_limit and fill_limit > 0:
        before = int(out.isna().sum())
        out = out.ffill(limit=fill_limit)
        filled = before - int(out.isna().sum())
        if filled:
            log.add("ffill", filled, f"limit={fill_limit} consecutive")
    return out, log
