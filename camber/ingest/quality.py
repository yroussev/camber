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

**Intermittent signals and the two-regime read.** The robust test assumes the bulk of the series is
one normal population. A mostly-off meter with real bursts (an HHW BTU meter near zero except during
heating events, a lead pump, a duty-cycled status point) breaks that assumption: every legitimate
burst reads as an outlier and the score collapses -- not a little, but chaotically, because
:func:`_mad_z` switches between its MAD branch and its meanAD fallback as the median crosses into
the "on" band.

So ``assess`` also looks for a **two-regime** structure and, when it finds one, reports the outlier
count read *within each regime* (``regime_outlier_frac``). A split is claimed only when all three of
these hold, because each rules out a different way of being wrong:

* **mass** -- both regimes carry real weight, which is what separates a regime (many samples in a
  band) from a spike (one sample far away), and what keeps genuine spike detection intact;
* **separation** -- the two centres are far apart relative to the scatter *within* them, so an
  ordinary continuous signal is not cut in half;
* **temporal coherence** -- each regime persists in runs. Real duty cycles persist; comms dropouts
  and error-sentinel scatter do not. Without this a flow meter railed to zero at random would look
  like a healthy duty cycle and its faults would be masked.

**The pooled ``n_outliers`` / ``outlier_frac`` never change meaning and are never masked** -- they
stay the whole-series answer, so a two-regime read is always visible next to the plain one.

This is a *distributional* test, not an on/off oracle: ``regime_threshold`` is not a setpoint and
not a schedule (a mapped status point stays the right oracle for "was it running"), it finds at most
two modes, and it cannot tell "off because the plant is off" from "off because the sensor railed to
a plausible constant for a long block". Because splitting is the direction that could *mask* a
fault, the composite ``score`` uses the regime read only when the caller opts in with
``regime_aware=True``; :mod:`camber.sensorhealth` turns it on for the roles where a duty cycle is
physically expected.

**Skewed and tightly-controlled signals: the shape-aware read.** The same one-population assumption
breaks a second way on a healthy plant. A loop differential pressure held at setpoint, or a supply
temperature controlled to a fraction of a degree, has a MAD far below any physically meaningful
deviation, so float noise and ordinary 1 degF swings score as "outliers". A pump that idles at
minimum speed for half the year and then ramps smoothly with load has a tight mode at the idle
speed and a broad, continuous tail -- one population, but a skewed one -- and the whole of its load
operation reads as outliers against the idle mode's MAD. Neither is bimodal enough for the regime
split, so the regime read does not help. On a fault-free simulated boiler plant this scored the
loop flow, DP and pump speed 0.2-0.3, low enough to gate every hydronic diagnostic off.

``assess`` therefore also reports a **shape-aware** outlier read (``shape_outlier_frac``). It
differs from the pooled test in exactly two ways, each with its own guard:

* **a scale floor** (``scale_floor``, in series units, supplied by the caller who knows the role):
  the robust scale never drops below the sensor's plausible measurement precision, so a deviation
  inside that precision is never an outlier. Always applied when given -- a deviation smaller than
  what the instrument can resolve is not evidence of anything.
* **a two-sided scale** (the "double MAD"; Rosenmai 2013, Leys et al. 2013): each side of the median
  gets its own MAD, so a long one-sided operating tail is judged against its own spread. Its
  breakdown point is a quarter of the series rather than half, so a 30 % scattered rail to zero
  *would* be absorbed on its own -- hence the guard: the points the two-sided scale excuses must be
  **temporally coherent** (median run length >= ``_REGIME_MIN_RUN``), the same gate that keeps a
  comms dropout from passing as a duty cycle. Load operation persists for hours; scatter does not.
  When the excused points are scattered, the read falls back to the (floored) pooled test.

As with the regime read, ``outlier_frac`` keeps its pooled, unmasked meaning, and the composite
``score`` uses the shape-aware read only when the caller opts in with ``shape_aware=True``.
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

# ---------------------------------------------------------------------------------------------
# Two-regime split gates. A split is claimed only when ALL THREE pass, because each rules out a
# different way of being wrong, and claiming one wrongly is the direction that MASKS a fault.
# These are judgement calls tuned against failure modes (a spike, a sine, a scattered rail), not
# against fixtures; they are module-level so a caller can retune without editing the algorithm.
# ---------------------------------------------------------------------------------------------
_REGIME_MIN_N = 24  # a day of hourly data; below this no split is attempted at all
_REGIME_MIN_FRAC = 0.05  # each regime holds >= 5% of the samples ...
_REGIME_MIN_COUNT = 8  # ... and >= 8 of them (the mass gate: a lone spike can never qualify)
_REGIME_MIN_SEPARATION = 6.0  # centre gap in pooled within-regime MADs (a sine scores ~2.9)
_REGIME_MIN_RUN = 2.0  # median run length of each regime, in samples (the coherence gate)
_REGIME_BINS = 256
_REGIME_WINSOR = (1.0, 99.0)  # percentiles the histogram is clipped to before thresholding
_REGIME_SEP_FRAC = 0.25  # share of the on/off gap a within-regime deviation must exceed


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


@dataclass(frozen=True)
class _RegimeSplit:
    """A qualifying two-regime split of one series: where it cuts, and how well separated."""

    threshold: float
    separation: float
    n_low: int
    n_high: int
    high: np.ndarray  # bool mask over the non-null values, True = the "on" regime


def _otsu_threshold(values: np.ndarray):
    """Otsu's two-class threshold over a winsorised histogram, or ``None`` if degenerate.

    Winsorising first is load-bearing: one 20x spike otherwise collapses the histogram into a
    single bin and the threshold lands between the bulk and the spike, which would destroy exactly
    the spike detection this must preserve. Values are *classified* un-winsorised, so a spike still
    lands in the high regime and is still tested there.
    """
    v = values[np.isfinite(values)]
    if len(v) < 3:
        return None
    lo_p, hi_p = np.percentile(v, _REGIME_WINSOR)
    w = np.clip(v, lo_p, hi_p)
    lo, hi = float(w.min()), float(w.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    counts, edges = np.histogram(w, bins=_REGIME_BINS, range=(lo, hi))
    total = counts.sum()
    if total == 0:
        return None
    centres = (edges[:-1] + edges[1:]) / 2.0
    weight_lo = np.cumsum(counts)
    weight_hi = total - weight_lo
    valid = (weight_lo > 0) & (weight_hi > 0)
    if not valid.any():
        return None
    csum = np.cumsum(counts * centres)
    mean_lo = np.divide(csum, weight_lo, out=np.zeros_like(csum), where=weight_lo > 0)
    mean_hi = np.divide(csum[-1] - csum, weight_hi, out=np.zeros_like(csum), where=weight_hi > 0)
    between = weight_lo * weight_hi * (mean_lo - mean_hi) ** 2
    between[~valid] = -1.0
    return float(edges[int(np.argmax(between)) + 1])


def _median_run_length(mask: np.ndarray) -> float:
    """Median length of the consecutive runs of ``True`` in ``mask`` (0.0 when there are none)."""
    if not mask.any():
        return 0.0
    padded = np.concatenate(([0], mask.astype("int8"), [0]))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return float(np.median(ends - starts)) if len(starts) else 0.0


def _regime_split(values: np.ndarray):
    """A qualifying two-regime split of ``values``, or ``None`` when the series is one population.

    Applies the mass, separation and temporal-coherence gates in that order (see the module
    docstring). Returning ``None`` is the safe answer: the caller then uses the ordinary pooled
    test, which is what today's behaviour already is.
    """
    n = len(values)
    if n < _REGIME_MIN_N:
        return None
    threshold = _otsu_threshold(values)
    if threshold is None:
        return None
    high = values > threshold
    n_high = int(high.sum())
    n_low = n - n_high

    floor = max(_REGIME_MIN_COUNT, int(np.ceil(_REGIME_MIN_FRAC * n)))
    if min(n_low, n_high) < floor:  # mass: a lone spike is not a regime
        return None

    lo_v, hi_v = values[~high], values[high]
    med_lo, med_hi = float(np.median(lo_v)), float(np.median(hi_v))
    mad_lo = float(np.median(np.abs(lo_v - med_lo)))
    mad_hi = float(np.median(np.abs(hi_v - med_hi)))
    spread = mad_lo + mad_hi
    separation = float("inf") if spread == 0 else (med_hi - med_lo) / spread
    if separation < _REGIME_MIN_SEPARATION:  # separation: don't bisect a continuous signal
        return None

    if min(_median_run_length(high), _median_run_length(~high)) < _REGIME_MIN_RUN:
        return None  # coherence: scatter is a fault, a duty cycle persists

    return _RegimeSplit(
        threshold=threshold, separation=separation, n_low=n_low, n_high=n_high, high=high
    )


def _mask_from(series: pd.Series, flag) -> pd.Series:
    """Shared shell: drop nulls, let ``flag(values)`` decide, write back positionally.

    Positional (not index-aligned) so a non-unique duplicate-timestamp index stays safe.
    """
    s = series.dropna()
    notna = series.notna().to_numpy()
    vals = np.zeros(len(series), dtype=bool)
    if len(s) < 3:
        return pd.Series(vals, index=series.index)
    vals[notna] = flag(s.to_numpy(dtype="float64"))
    return pd.Series(vals, index=series.index)


def outlier_mask(series: pd.Series, cutoff: float = _MAD_Z_CUTOFF) -> pd.Series:
    """Boolean mask of robust (MAD-based) outliers among the non-null values."""
    return _mask_from(series, lambda v: np.abs(_mad_z(v)) > cutoff)


def _regime_outlier_flags(values: np.ndarray, split, cutoff: float) -> np.ndarray:
    """Robust outliers judged *within* each regime, so a duty cycle is not itself the outlier.

    A point must be a robust outlier in its own regime **and** displaced from that regime's centre
    by a meaningful share of the gap between the two -- otherwise a regime that sits at (or is
    clipped to) zero has near-zero MAD, the meanAD fallback fires inside it, and it flags its own
    noise. A genuine spike inside the "on" band still clears both tests.
    """
    out = np.zeros(len(values), dtype=bool)
    med_lo = float(np.median(values[~split.high]))
    med_hi = float(np.median(values[split.high]))
    gap = med_hi - med_lo
    for sel in (split.high, ~split.high):
        if sel.sum() < 3:
            continue
        sub = values[sel]
        centre = float(np.median(sub))
        far_enough = np.abs(sub - centre) > _REGIME_SEP_FRAC * gap
        out[sel] = (np.abs(_mad_z(sub)) > cutoff) & far_enough
    return out


def _floored_z(values: np.ndarray, scale_floor: float | None) -> np.ndarray:
    """Pooled modified z-score whose scale never drops below ``scale_floor`` (std-equivalent)."""
    z = _mad_z(values)
    if not scale_floor or scale_floor <= 0:
        return z
    dev = values - np.median(values)
    # the floored score is the smaller of the two: a floor can only *shrink* a z-score
    return np.sign(z) * np.minimum(np.abs(z), np.abs(dev) / scale_floor)


def _two_sided_z(values: np.ndarray, scale_floor: float | None) -> np.ndarray:
    """Double-MAD modified z-score: each side of the median judged against its own spread.

    Each side's MAD is taken over the deviations of the samples on that side (median-valued samples
    count on both, as in the standard double MAD). Falls back per side exactly as :func:`_mad_z`
    does (meanAD when a side's MAD is 0) and applies the same ``scale_floor``. For a symmetric
    series both side-MADs equal the pooled MAD, so the score matches the pooled one.
    """
    med = np.median(values)
    dev = values - med
    z = np.zeros_like(values, dtype="float64")
    for side, pool in ((dev > 0, dev >= 0), (dev < 0, dev <= 0)):
        if not side.any():
            continue
        absdev = np.abs(dev[pool])
        mad = float(np.median(absdev))
        scale = mad / _MAD_SCALE if mad > 0 else _MEANAD_SCALE * float(np.mean(absdev))
        if scale_floor and scale_floor > 0:
            scale = max(scale, scale_floor)
        if scale > 0:
            z[side] = dev[side] / scale
    return z


def _shape_outlier_flags(
    values: np.ndarray, scale_floor: float | None, cutoff: float
) -> np.ndarray:
    """The shape-aware outlier read (see the module docstring): floored, two-sided, coherent."""
    pooled = np.abs(_floored_z(values, scale_floor)) > cutoff
    two_sided = np.abs(_two_sided_z(values, scale_floor)) > cutoff
    excused = pooled & ~two_sided
    if excused.any() and _median_run_length(excused) < _REGIME_MIN_RUN:
        return pooled  # the excused points are scatter, not operation: don't absorb them
    return pooled & two_sided


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

    n: int  # samples present (non-null)
    n_missing: int  # NaN samples in the series
    coverage: float  # non-null fraction, 0..1
    n_gaps: int  # time-grid gaps > 1.5x expected interval
    longest_flatline: int  # longest stuck run (identical consecutive values)
    flatline_frac: float  # longest_flatline / n, 0..1
    n_outliers: int  # robust (MAD) outliers
    outlier_frac: float  # n_outliers / n, 0..1
    expected_freq: object  # inferred/declared interval (Timedelta or None)
    score: float  # composite quality 0..1 (1 = clean)
    n_duplicate_ts: int = 0  # duplicate timestamps (DST fall-back / concatenated exports)
    # Two-regime read (see the module docstring). Tri-state per the honesty convention:
    # None means the split could not be TESTED (too few samples), never "no split found".
    n_regimes: int | None = None  # 1 or 2; None when untestable
    regime_threshold: float | None = None  # the split value; None unless n_regimes == 2
    n_regime_outliers: int | None = None  # outliers judged within their own regime
    regime_outlier_frac: float | None = None  # n_regime_outliers / n
    # Shape-aware read (see the module docstring): None when untestable (< 3 samples).
    n_shape_outliers: int | None = None  # floored, two-sided, coherence-gated outliers
    shape_outlier_frac: float | None = None  # n_shape_outliers / n

    def as_dict(self):
        """Return as a plain dict (expected_freq stringified)."""
        d = self.__dict__.copy()
        d["expected_freq"] = str(self.expected_freq)
        return d


def assess(
    series: pd.Series,
    expected_freq=None,
    *,
    regime_aware: bool = False,
    shape_aware: bool = False,
    scale_floor: float | None = None,
) -> QualityReport:
    """Compute a :class:`QualityReport` without modifying the series.

    ``expected_freq`` (a pandas-parseable interval) overrides the inferred
    sampling interval used for gap detection.

    The two-regime read is **always computed and reported**; ``regime_aware`` only decides whether
    the composite ``score`` uses it instead of the pooled outlier fraction. It is off by default
    because scoring a duty cycle as normal is the direction that could mask a fault -- so it is
    opted into per role by :func:`camber.sensorhealth.sensor_trust`, where the role is known. The
    pooled ``n_outliers`` / ``outlier_frac`` are never masked either way.

    The shape-aware read (``shape_outlier_frac``) is likewise always computed; ``scale_floor`` (the
    sensor's plausible measurement precision, std-equivalent, in series units) feeds it, and
    ``shape_aware`` decides whether the score uses it. When both reads are opted into and a
    two-regime split was found, the regime read wins (it is the more specific model).
    """
    total = len(series)
    n_missing = int(series.isna().sum())
    n = total - n_missing
    coverage = (n / total) if total else 0.0
    exp = pd.Timedelta(expected_freq) if expected_freq is not None else infer_freq(series.index)
    n_gaps = gap_count(series.index, exp)
    flat = longest_flatline(series)
    flat_frac = (flat / n) if n else 0.0
    n_out = int(outlier_mask(series).sum())
    out_frac = (n_out / n) if n else 0.0
    n_dup = int(pd.DatetimeIndex(series.index).duplicated().sum())

    # The two-regime read. Always computed and reported; only the composite score is gated on
    # regime_aware, because using it is the direction that could mask a fault.
    values = series.dropna().to_numpy(dtype="float64")
    n_regimes: int | None = None
    threshold: float | None = None
    n_reg_out: int | None = None
    reg_frac: float | None = None
    if len(values) >= 3:
        split = _regime_split(values)
        n_regimes = 2 if split is not None else 1
        if split is None:
            n_reg_out, reg_frac = n_out, out_frac
        else:
            threshold = round(float(split.threshold), 4)
            flags = _regime_outlier_flags(values, split, _MAD_Z_CUTOFF)
            n_reg_out = int(flags.sum())
            reg_frac = (n_reg_out / n) if n else 0.0

    n_shape: int | None = None
    shape_frac: float | None = None
    if len(values) >= 3:
        n_shape = int(_shape_outlier_flags(values, scale_floor, _MAD_Z_CUTOFF).sum())
        shape_frac = (n_shape / n) if n else 0.0

    if regime_aware and n_regimes == 2 and reg_frac is not None:
        out_used = reg_frac
    elif shape_aware and shape_frac is not None:
        out_used = shape_frac
    elif regime_aware and reg_frac is not None:
        out_used = reg_frac
    else:
        out_used = out_frac

    # Composite: coverage dominates; outliers penalize moderately; an extreme
    # flatline (whole series stuck) contributes lightly since some points are
    # legitimately constant.
    score = coverage * (1.0 - min(out_used * 2.0, 1.0)) * (1.0 - 0.2 * flat_frac)
    score = float(max(0.0, min(1.0, score)))
    return QualityReport(
        n=n,
        n_missing=n_missing,
        coverage=round(coverage, 4),
        n_gaps=n_gaps,
        longest_flatline=flat,
        flatline_frac=round(flat_frac, 4),
        n_outliers=n_out,
        outlier_frac=round(out_frac, 4),
        expected_freq=exp,
        score=round(score, 4),
        n_duplicate_ts=n_dup,
        n_regimes=n_regimes,
        regime_threshold=threshold,
        n_regime_outliers=n_reg_out,
        regime_outlier_frac=None if reg_frac is None else round(reg_frac, 4),
        n_shape_outliers=n_shape,
        shape_outlier_frac=None if shape_frac is None else round(shape_frac, 4),
    )


@dataclass
class CleaningLog:
    """An auditable record of what :func:`clean` changed."""

    steps: list = field(default_factory=list)  # list[dict]: op, n_affected, detail

    def add(self, op: str, n_affected: int, detail: str = ""):
        """Record one cleaning step (operation, count affected, detail)."""
        self.steps.append({"op": op, "n_affected": int(n_affected), "detail": detail})

    @property
    def total_changed(self) -> int:
        """Total samples changed across all logged steps."""
        return sum(s["n_affected"] for s in self.steps)


def clean(
    series: pd.Series,
    *,
    drop_outliers: bool = False,
    fill_limit: int = 0,
    outlier_cutoff: float = _MAD_Z_CUTOFF,
):
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
            log.add("drop_outliers", mask.sum(), f"MAD z>{outlier_cutoff} set to NaN")
    if fill_limit and fill_limit > 0:
        before = int(out.isna().sum())
        out = out.ffill(limit=fill_limit)
        filled = before - int(out.isna().sum())
        if filled:
            log.add("ffill", filled, f"limit={fill_limit} consecutive")
    return out, log
