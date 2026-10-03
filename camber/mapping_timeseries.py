"""Time-series evidence for point-role suggestion (0.96, #45): what a point's data says it is.

:mod:`camber.mapping_assist` suggests a role from a point's *name*. When the names are anonymised
(an export of UUIDs, ``AI_0417``, a vendor's numeric object ids) the name says nothing, but the
data still does: a zone temperature sits near 21-23 C with a gentle daily swing, an outdoor
temperature follows the weather, a setpoint holds still and then steps, a status point takes two
values, a heating valve opens as the weather cools. This module turns a series into those facts
and scores every role against them.

* :func:`profile_series` -- a :class:`SeriesProfile`: value range and quantiles, cadence and the
  change-of-value pattern, binary / enumerated values, the daily and weekly periodicity, the
  correlation with outdoor-air temperature (when one is given) and how step-like, setpoint-like
  the series is. numpy + pandas only.
* :data:`ROLE_TEMPLATES` / :func:`template_scores` -- a hand-written, physically motivated
  template per role (quantity, typical level in each plausible unit, sensor / setpoint / status /
  command behaviour, expected response to the weather), scored against a profile. No training.
* :class:`ProfileModel` -- optional: a Gaussian class model over profile features, fitted on
  labelled points (e.g. other buildings of a portfolio) with numpy only; its scores replace or
  refine the templates' when given.
* :func:`blend` -- how a time-series score joins the lexical one so that an informative **name
  still dominates**: the data's weight shrinks as the best lexical score grows.

It is advisory, like the rest of mapping assist, and opt-in: ``FeatureSuggester`` behaves exactly
as before unless it is built with ``use_timeseries=True``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .model.roles import Role

__all__ = [
    "SeriesProfile",
    "profile_series",
    "RoleTemplate",
    "ROLE_TEMPLATES",
    "UNIT_SCALES",
    "template_scores",
    "ProfileModel",
    "PROFILE_FEATURES",
    "blend",
    "INFORMATIVE_NAME",
    "TS_WEIGHT_UNINFORMATIVE",
    "TS_WEIGHT_INFORMATIVE",
]

# --------------------------------------------------------------------------- the profile


@dataclass(frozen=True)
class SeriesProfile:
    """What a series' values and timing say about the point (NaN where it cannot be measured).

    ``binary`` means every value is 0 or 1, ``two_level`` at most two distinct values (a two-state
    setpoint is two-level, not binary). ``cadence_s`` is the median gap between samples;
    ``change_frac`` the share of consecutive samples that differ (a sensor changes almost every
    sample, a setpoint or status rarely); ``run_median`` the median length (in samples) of a run of
    equal values; ``plateau_frac`` the share of samples sitting exactly at the series' minimum or
    maximum (a valve closed or wide open, a damper at its minimum position); ``step_share`` the
    share of the total movement made in jumps of at least a quarter of the 5-95 % range (1 for a
    signal that only steps, small for a drifting sensor). ``diurnal`` and ``weekly`` are the
    shares of variance explained by the hour of day (within-day movement) and by the day of the
    week (day-to-day movement).
    ``oat_corr`` correlates daily means with outdoor air (the load / reset response),
    ``oat_corr_hourly`` hourly means (the outdoor-air sensor itself).
    """

    n: int
    median: float
    p01: float
    p05: float
    p25: float
    p75: float
    p95: float
    p99: float
    std: float
    n_unique: int
    binary: bool
    two_level: bool
    integer_frac: float
    zero_frac: float
    plateau_frac: float
    cadence_s: float
    change_frac: float
    run_median: float
    step_share: float
    diurnal: float
    weekly: float
    oat_corr: float
    oat_corr_hourly: float

    @property
    def spread(self) -> float:
        """The 5-95 % range."""
        return self.p95 - self.p05

    def as_dict(self) -> dict:
        return asdict(self)


_NAN = float("nan")
_UNIQUE_CAP = 1000


def _empty_profile() -> SeriesProfile:
    """The profile of a series with no finite value (counts 0, flags false, the rest NaN)."""
    import dataclasses

    vals: dict = {}
    for f in dataclasses.fields(SeriesProfile):
        vals[f.name] = 0 if f.type in ("int", int) else False if f.type in ("bool", bool) else _NAN
    return SeriesProfile(**vals)


def _values(series) -> tuple:
    """``(finite float values in order, DatetimeIndex or None)``."""
    if isinstance(series, pd.Series):
        s = pd.to_numeric(series, errors="coerce")
        s = s[np.isfinite(s.to_numpy(dtype=float, na_value=np.nan))]
        idx = s.index if isinstance(s.index, pd.DatetimeIndex) else None
        if idx is not None and not idx.is_monotonic_increasing:
            s = s.sort_index(kind="stable")
            idx = s.index
        return s.to_numpy(dtype=float), idx
    v = np.asarray(series, dtype=float).ravel()
    return v[np.isfinite(v)], None


def _share_explained(values: np.ndarray, groups: np.ndarray) -> float:
    """R^2 of group means (share of variance the grouping explains), small-sample corrected."""
    if values.size < 3:
        return _NAN
    total = float(np.var(values))
    if total <= 0.0:
        return 0.0
    keys, inv = np.unique(groups, return_inverse=True)
    sums = np.bincount(inv, weights=values)
    counts = np.bincount(inv)
    means = sums / np.maximum(counts, 1)
    between = float(np.sum(counts * (means - values.mean()) ** 2) / values.size)
    r2 = between / total
    # expected R^2 of pure noise with k groups is about (k - 1) / n: remove it
    k, n = len(keys), values.size
    adj = (r2 - (k - 1) / n) / (1 - (k - 1) / n) if n > k else 0.0
    return float(min(1.0, max(0.0, adj)))


def _corr(a: pd.Series, b: pd.Series, min_n: int) -> float:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < min_n:
        return _NAN
    x, y = j.iloc[:, 0].to_numpy(), j.iloc[:, 1].to_numpy()
    if x.std() <= 0 or y.std() <= 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def profile_series(series, *, oat=None) -> SeriesProfile:
    """The :class:`SeriesProfile` of ``series`` (a pandas Series, ideally with a DatetimeIndex, or
    an array; timing features are NaN without one). ``oat`` is an outdoor-air temperature series
    of the same site (any unit), used only for the two correlation features."""
    v, idx = _values(series)
    n = int(v.size)
    if n == 0:
        return _empty_profile()
    q = np.quantile(v, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    rounded = np.round(v, 4)
    uniq = np.unique(rounded[:200_000]) if n > 200_000 else np.unique(rounded)
    n_unique = int(min(len(uniq), _UNIQUE_CAP))
    d = np.diff(v)
    moves = d[d != 0]
    change_frac = float(moves.size / d.size) if d.size else _NAN
    if moves.size:
        a = np.abs(moves)
        big = a >= 0.25 * max(float(q[5] - q[1]), 1e-12)
        step_share = float(a[big].sum() / a.sum())
    else:
        step_share = _NAN
    # run lengths of equal consecutive values
    if d.size:
        edges = np.flatnonzero(d != 0)
        bounds = np.concatenate(([-1], edges, [n - 1]))
        run_median = float(np.median(np.diff(bounds)))
    else:
        run_median = float(n)
    cadence = diurnal = weekly = oc = och = _NAN
    if idx is not None and n >= 3:
        gaps = np.diff(idx.asi8) / 1e9
        gaps = gaps[gaps > 0]
        cadence = float(np.median(gaps)) if gaps.size else _NAN
        s = pd.Series(v, index=idx)
        hourly = s.resample("1h").mean().dropna()
        if len(hourly) >= 48:
            day = hourly.index.normalize()
            within = hourly - hourly.groupby(day).transform("mean")
            diurnal = _share_explained(within.to_numpy(), hourly.index.hour.to_numpy())
            daily = hourly.groupby(day).mean()
            if len(daily) >= 14:
                weekly = _share_explained(
                    (daily - daily.rolling(7, center=True, min_periods=1).mean()).to_numpy(),
                    daily.index.dayofweek.to_numpy(),
                )
        if oat is not None:
            o = pd.to_numeric(pd.Series(oat), errors="coerce")
            if isinstance(o.index, pd.DatetimeIndex) and len(hourly) >= 48:
                oh = o.resample("1h").mean()
                och = _corr(hourly, oh, 48)
                oc = _corr(hourly.resample("1D").mean(), oh.resample("1D").mean(), 14)
    return SeriesProfile(
        n=n,
        median=float(q[3]),
        p01=float(q[0]),
        p05=float(q[1]),
        p25=float(q[2]),
        p75=float(q[4]),
        p95=float(q[5]),
        p99=float(q[6]),
        std=float(v.std()),
        n_unique=n_unique,
        binary=bool(n_unique <= 2 and np.isin(uniq, (0.0, 1.0)).all()),
        two_level=n_unique <= 2,
        integer_frac=float(np.mean(np.abs(v - np.round(v)) < 1e-9)),
        zero_frac=float(np.mean(v == 0.0)),
        plateau_frac=float(np.mean((v == v.min()) | (v == v.max()))),
        cadence_s=cadence,
        change_frac=change_frac,
        run_median=run_median,
        step_share=step_share,
        diurnal=diurnal,
        weekly=weekly,
        oat_corr=oc,
        oat_corr_hourly=och,
    )


# --------------------------------------------------------------------------- role templates

#: How a raw value maps onto CAMBER's canonical (IP) unit for each quantity family, per plausible
#: source unit: ``{family: {unit: (scale, offset)}}``; canonical = raw * scale + offset.
UNIT_SCALES: dict = {
    "temp": {"degF": (1.0, 0.0), "degC": (1.8, 32.0)},
    "percent": {"percent": (1.0, 0.0), "fraction": (100.0, 0.0)},
    "humidity": {"percent": (1.0, 0.0), "fraction": (100.0, 0.0)},
    "air_press": {"inH2O": (1.0, 0.0), "Pa": (1.0 / 249.08891, 0.0), "kPa": (4.0146, 0.0)},
    "water_press": {"psi": (1.0, 0.0), "kPa": (0.145038, 0.0), "ftH2O": (0.433527, 0.0)},
    "air_flow": {"cfm": (1.0, 0.0), "L/s": (2.11888, 0.0), "m3/h": (0.588578, 0.0)},
    "water_flow": {"gpm": (1.0, 0.0), "L/s": (15.8503, 0.0), "m3/h": (4.40287, 0.0)},
    "co2": {"ppm": (1.0, 0.0)},
    "binary": {"": (1.0, 0.0)},
    "stage": {"": (1.0, 0.0)},
    "power": {"kW": (1.0, 0.0), "W": (0.001, 0.0)},
}
# declared unit spellings -> (family-independent) unit key used in UNIT_SCALES
_UNIT_KEYS = {
    "degf": "degF", "f": "degF", "°f": "degF",
    "degc": "degC", "c": "degC", "°c": "degC", "celsius": "degC",
    "percent": "percent", "pct": "percent", "%": "percent", "fraction": "fraction",
    "inwc": "inH2O", "inh2o": "inH2O", "pa": "Pa", "kpa": "kPa", "psi": "psi", "psig": "psi",
    "ft": "ftH2O", "cfm": "cfm", "l/s": "L/s", "lps": "L/s", "m3/h": "m3/h", "gpm": "gpm",
    "ppm": "ppm", "kw": "kW", "w": "W",
}  # fmt: skip


@dataclass(frozen=True)
class RoleTemplate:
    """What a role's data typically looks like, in CAMBER's canonical units.

    ``level`` bounds the typical median and ``spread`` the typical 5-95 % range; ``kind`` is
    ``sensor`` (moves almost every sample), ``setpoint`` (holds still, then steps), ``status``
    (two values) or ``command`` (a 0-100 % output that may sit at 0); ``oat`` is the expected sign
    of the day-to-day correlation with outdoor air (``+1`` a cooling load, ``-1`` a heating load or
    a reset, ``0`` none expected); ``outdoor`` marks a sensor *in* the outdoor air, which follows
    the weather hour by hour (``oat_hourly`` its expected sign). ``prior`` says how common the
    role's points are (a plant has a few, a building hundreds of zones). ``gated`` marks a
    quantity that reads about zero while its fan or pump is off (duct static, filter pressure
    drop, airflow): its level is judged on the 95th percentile, since a series logged around the
    clock can have a median near zero.
    """

    role: Role
    family: str
    kind: str
    level: tuple
    spread: tuple
    oat: int = 0
    outdoor: bool = False
    oat_hourly: int = 1
    gated: bool = False
    prior: float = 1.0


#: How common a role's points are in a building, relative to zone- and terminal-level points
#: (hundreds per building) -- an air handler has a handful, a plant a few.
_PLANT = 0.5
_AHU = 0.8


def _t(role, family, kind, level, spread, oat=0, outdoor=False, oat_hourly=1, gated=False,
       prior=1.0):  # fmt: skip
    return RoleTemplate(
        Role(role), family, kind, level, spread, oat, outdoor, oat_hourly, gated, prior
    )


#: The hand-written templates (roles without one get no time-series score).
ROLE_TEMPLATES: dict = {
    t.role: t
    for t in (
        _t("oat", "temp", "sensor", (25, 90), (15, 60), outdoor=True, prior=_AHU),
        _t("wetbulb_temp", "temp", "sensor", (25, 75), (10, 45), outdoor=True, prior=_AHU),
        _t("supply_air_temp", "temp", "sensor", (52, 72), (3, 30)),
        _t("mixed_air_temp", "temp", "sensor", (52, 74), (5, 25), prior=_AHU),
        _t("return_air_temp", "temp", "sensor", (67, 79), (2, 10), prior=_AHU),
        _t("space_temp", "temp", "sensor", (66, 77), (1, 14)),
        _t("cool_sp", "temp", "setpoint", (70, 80), (0, 6)),
        _t("heat_sp", "temp", "setpoint", (62, 71), (0, 6)),
        _t("supply_air_temp_sp", "temp", "setpoint", (52, 70), (0, 12)),
        _t("chw_supply_temp", "temp", "sensor", (38, 50), (2, 16), oat=-1, prior=_PLANT),
        _t("chw_return_temp", "temp", "sensor", (48, 62), (3, 16), oat=1, prior=_PLANT),
        _t("chw_supply_temp_sp", "temp", "setpoint", (40, 50), (0, 8), prior=_PLANT),
        _t("hw_supply_temp", "temp", "sensor", (110, 180), (10, 60), oat=-1, prior=_PLANT),
        _t("hw_return_temp", "temp", "sensor", (95, 160), (10, 60), oat=-1, prior=_PLANT),
        _t("cw_supply_temp", "temp", "sensor", (65, 88), (5, 25), oat=1, prior=_PLANT),
        _t("cw_return_temp", "temp", "sensor", (75, 98), (5, 25), oat=1, prior=_PLANT),
        _t("heat_valve", "percent", "command", (0, 60), (5, 100), oat=-1),
        _t("cool_valve", "percent", "command", (0, 70), (5, 100), oat=1),
        _t("oa_damper", "percent", "command", (10, 100), (0, 100), prior=_AHU),
        _t("damper", "percent", "command", (20, 100), (5, 100)),
        _t("supply_fan_speed", "percent", "command", (30, 100), (5, 100), prior=_AHU),
        _t("chw_pump_speed", "percent", "command", (30, 100), (5, 100), oat=1, prior=_PLANT),
        _t("hw_pump_speed", "percent", "command", (30, 100), (5, 100), oat=-1, prior=_PLANT),
        _t(
            "outdoor_rh",
            "humidity",
            "sensor",
            (35, 90),
            (25, 70),
            outdoor=True,
            oat_hourly=-1,
            prior=_AHU,
        ),
        _t("supply_air_humidity", "humidity", "sensor", (40, 95), (10, 50), prior=_AHU),
        _t("return_air_humidity", "humidity", "sensor", (30, 65), (8, 40), prior=_AHU),
        _t("duct_static", "air_press", "sensor", (0.3, 2.5), (0.1, 2.5), gated=True, prior=_AHU),
        _t("duct_static_sp", "air_press", "setpoint", (0.4, 2.2), (0, 1.0), prior=_AHU),
        _t(
            "filter_diff_press",
            "air_press",
            "sensor",
            (0.1, 1.5),
            (0.05, 1.5),
            gated=True,
            prior=_AHU,
        ),
        _t("chw_diff_press", "water_press", "sensor", (3, 25), (1, 15), prior=_PLANT),
        _t("chw_diff_press_sp", "water_press", "setpoint", (3, 25), (0, 8), prior=_PLANT),
        _t("hw_diff_press", "water_press", "sensor", (3, 25), (1, 15), prior=_PLANT),
        _t("airflow", "air_flow", "sensor", (40, 20000), (20, 15000), gated=True),
        _t("airflow_sp", "air_flow", "setpoint", (40, 20000), (0, 15000)),
        _t("oa_airflow", "air_flow", "sensor", (200, 20000), (100, 15000), gated=True, prior=_AHU),
        _t("chw_flow", "water_flow", "sensor", (20, 3000), (10, 3000), oat=1, prior=_PLANT),
        _t("hw_flow", "water_flow", "sensor", (10, 1500), (5, 1500), oat=-1, prior=_PLANT),
        _t("co2", "co2", "sensor", (420, 1000), (50, 800)),
        _t("outdoor_co2", "co2", "sensor", (380, 480), (5, 80), prior=_AHU),
        _t("supply_fan_status", "binary", "status", (0, 1), (0, 1), prior=_AHU),
        _t("pump_status", "binary", "status", (0, 1), (0, 1), prior=_PLANT),
        _t("occupancy", "binary", "status", (0, 1), (0, 1)),
        _t("compressor_stage", "stage", "stage", (0, 3), (1, 6), oat=1, prior=_AHU),
        _t("heat_stage", "stage", "stage", (0, 3), (1, 6), oat=-1, prior=_AHU),
        _t("power", "power", "sensor", (1, 3000), (1, 3000), prior=_AHU),
    )
}


def _unit_key(unit) -> str:
    return _UNIT_KEYS.get(str(unit).strip().lower(), "") if unit else ""


def _band(x: float, lo: float, hi: float, tol: float, peak: float = 0.0) -> float:
    """1 inside ``[lo, hi]``, a Gaussian fall-off outside with width ``tol``. With ``peak`` > 0
    the band prefers its middle: the edges score ``1 - peak`` (a typical level beats a merely
    possible one)."""
    if not math.isfinite(x):
        return 0.5
    d = lo - x if x < lo else x - hi if x > hi else 0.0
    if d > 0:
        return (1.0 - peak) * math.exp(-0.5 * (d / tol) ** 2)
    if peak <= 0.0 or hi <= lo:
        return 1.0
    z = (x - 0.5 * (lo + hi)) / (0.3 * (hi - lo))
    return 1.0 - peak + peak * math.exp(-0.5 * z * z)


def _kind_fit(p: SeriesProfile, kind: str) -> float:
    cf = p.change_frac if math.isfinite(p.change_frac) else 0.5
    if kind == "status":
        return 1.0 if p.binary else 0.3 if p.two_level else 0.02
    if p.binary:
        return 0.3 if kind == "command" else 0.05  # a two-position valve can still be a command
    if p.two_level and kind == "sensor":
        return 0.1
    if kind == "stage":
        return 1.0 if p.integer_frac > 0.99 and p.n_unique <= 8 else 0.05
    if kind == "setpoint":
        # holds still, then steps: few changes, long runs, movement concentrated in steps
        still = math.exp(-max(0.0, cf - 0.1) / 0.15)
        steps = p.step_share if math.isfinite(p.step_share) else 0.5
        return max(0.05, still * (0.6 + 0.4 * steps))
    plateau = p.plateau_frac if math.isfinite(p.plateau_frac) else 0.0
    if kind == "sensor":
        # a measurement moves; it rarely sits exactly at its own extreme for long
        return (0.5 + 0.5 * min(1.0, cf / 0.2)) * (
            1.0 - 0.6 * min(1.0, max(0.0, plateau - 0.1) / 0.3)
        )
    # command: a loop output rests at its limits (closed, wide open, a minimum position) and
    # moves between them; one that never moves is more likely a setpoint
    return (0.3 + 0.7 * min(1.0, plateau / 0.15)) * (0.4 + 0.6 * min(1.0, cf / 0.05))


def _weather_fit(p: SeriesProfile, t: RoleTemplate) -> float:
    ch, cd = p.oat_corr_hourly, p.oat_corr
    if t.outdoor:
        if math.isfinite(ch):
            return max(0.05, 1.0 / (1.0 + math.exp(-8.0 * (t.oat_hourly * ch - 0.5))))
        d = p.diurnal if math.isfinite(p.diurnal) else 0.3
        return 0.4 + 0.6 * min(1.0, d / 0.3)
    fit = 1.0
    if math.isfinite(ch) and ch > 0.85:
        fit *= 0.3  # tracks the outdoor air hour by hour: an outdoor sensor, not this role
    if t.oat and math.isfinite(cd):
        s = t.oat * cd
        fit *= 1.0 if s >= 0.2 else 0.85 if s > -0.2 else 0.5
    return fit


def _scales(family: str, unit) -> dict:
    """The unit interpretations to try: the declared one, or every plausible one."""
    table = UNIT_SCALES[family]
    key = _unit_key(unit)
    if key and key in table:
        return {key: table[key]}
    if unit and key and key not in table:
        return {}  # a declared unit of another quantity: this family does not apply
    return table


def template_scores(p: SeriesProfile, unit=None, *, vocab=None) -> dict:
    """``{Role: (score 0..1, note)}`` for every templated role in ``vocab`` (default: all).

    The score multiplies how well the median and the 5-95 % range sit in the role's typical
    bands (in the best-fitting plausible unit, or the declared ``unit``), whether the series
    behaves like the role's kind (sensor / setpoint / status / command / stage), and whether it
    responds to the weather as the role should (when the profile has an outdoor-air correlation).
    """
    out: dict = {}
    if p.n < 3:
        return out
    roles = (
        ROLE_TEMPLATES
        if vocab is None
        else {r: ROLE_TEMPLATES[r] for r in vocab if r in ROLE_TEMPLATES}
    )
    for role, t in roles.items():
        best, best_u = 0.0, ""
        for u, (a, b) in _scales(t.family, unit).items():
            med = (p.p95 if t.gated else p.median) * a + b
            spread = p.spread * abs(a)
            lo, hi = t.level
            tol = 0.15 * (hi - lo)
            if lo > 0:  # a positive band (a flow, a concentration): never tolerate half its floor
                tol = min(tol, 0.5 * lo)
            tol = max(tol, 0.05)
            s = _band(med, lo, hi, tol, peak=0.4 if t.family == "temp" else 0.0)
            slo, shi = t.spread
            s *= _band(spread, slo, shi, max(0.3 * (shi - slo), 0.05)) ** 0.5
            if t.family in ("percent", "humidity") and (p.p01 * a < -5 or p.p99 * a > 105):
                s *= 0.1  # a percentage leaves 0-100
            if s > best:
                best, best_u = s, u
        if best <= 0.0:
            continue
        # a template that accepts any level over decades says little when it fits: weigh it by
        # how narrow its typical band is (log width), as a likelihood would
        lo, hi = t.level
        breadth = math.log10((hi + 1.0) / (max(lo, 0.0) + 1.0)) if hi > lo else 0.0
        best *= 1.0 / (1.0 + breadth)
        kind = _kind_fit(p, t.kind)
        weather = _weather_fit(p, t)
        score = best * kind * weather * t.prior
        if score < 1e-3:
            continue
        note = f"{t.kind} data at the level of {role.value}" + (
            f" (read as {best_u})" if best_u else ""
        )
        out[role] = (round(score, 4), note)
    return out


# --------------------------------------------------------------------------- a fitted model

#: The profile features a :class:`ProfileModel` uses (transformed to comparable scales).
PROFILE_FEATURES = (
    "level",
    "low",
    "high",
    "spread",
    "binary",
    "zero_frac",
    "plateau_frac",
    "integer_frac",
    "change_frac",
    "run_median",
    "step_share",
    "n_unique",
    "diurnal",
    "weekly",
    "oat_corr",
    "oat_corr_hourly",
)


def _vector(p: SeriesProfile) -> np.ndarray:
    return np.array(
        [
            math.asinh(p.median),
            math.asinh(p.p05),
            math.asinh(p.p95),
            math.log1p(max(p.spread, 0.0)) if math.isfinite(p.spread) else _NAN,
            float(p.binary),
            p.zero_frac,
            p.plateau_frac,
            p.integer_frac,
            p.change_frac,
            math.log1p(p.run_median) if math.isfinite(p.run_median) else _NAN,
            p.step_share,
            math.log1p(p.n_unique),
            p.diurnal,
            p.weekly,
            p.oat_corr,
            p.oat_corr_hourly,
        ],
        dtype=float,
    )


class ProfileModel:
    """A Gaussian class model over :class:`SeriesProfile` features (numpy only).

    ``fit([(profile, role), ...])`` learns, per role, the mean and variance of each feature
    (a missing feature is left out of that point's likelihood) and how common the role is;
    :meth:`scores` returns the posterior probability of each learned role. ``prior="empirical"``
    (the default) weighs roles by their share of the training points, ``"equal"`` does not.
    ``var_floor`` is added to every variance: other buildings differ (units, sequences, sensor
    quality), and a model that trusts one building's tight spreads transfers badly. It learns
    levels in the units of its training data: fit it on data in the units you will score.
    """

    def __init__(self, *, var_floor: float = 1.0, min_examples: int = 2, prior: str = "empirical"):
        if prior not in ("empirical", "equal"):
            raise ValueError("prior must be 'empirical' or 'equal'")
        self.var_floor = var_floor
        self.min_examples = min_examples
        self.prior = prior
        self.roles: list = []
        self._mu: np.ndarray | None = None
        self._var: np.ndarray | None = None
        self._logprior: np.ndarray | None = None

    def fit(self, labelled) -> ProfileModel:
        """Learn from ``[(SeriesProfile, role), ...]`` (roles with fewer than ``min_examples``
        are skipped)."""
        by: dict = {}
        for prof, role in labelled:
            if prof.n >= 3:
                by.setdefault(Role(role), []).append(_vector(prof))
        roles = sorted(
            (r for r, v in by.items() if len(v) >= self.min_examples), key=lambda r: r.value
        )
        if not roles:
            raise ValueError("ProfileModel.fit needs at least one role with enough examples")
        mu, var = [], []
        for r in roles:
            x = np.vstack(by[r])
            ok = np.isfinite(x)
            cnt = ok.sum(axis=0)
            xs = np.where(ok, x, 0.0)
            m = xs.sum(axis=0) / np.maximum(cnt, 1)
            v = (np.where(ok, (x - m) ** 2, 0.0)).sum(axis=0) / np.maximum(cnt, 1)
            mu.append(np.where(cnt > 0, m, 0.0))
            var.append(np.where(cnt > 1, v, 1.0) + self.var_floor)
        self.roles, self._mu, self._var = roles, np.vstack(mu), np.vstack(var)
        counts = np.array([len(by[r]) for r in roles], dtype=float)
        if self.prior == "equal":
            counts = np.ones_like(counts)
        self._logprior = np.log(counts / counts.sum())
        return self

    def scores(self, p: SeriesProfile) -> dict:
        """``{Role: posterior probability}`` over the fitted roles (empty before :meth:`fit`)."""
        if self._mu is None or self._var is None or self._logprior is None or p.n < 3:
            return {}
        x = _vector(p)
        ok = np.isfinite(x)
        d = (x[ok] - self._mu[:, ok]) ** 2 / self._var[:, ok]
        ll = -0.5 * (d + np.log(2 * np.pi * self._var[:, ok])).sum(axis=1) + self._logprior
        ll -= ll.max()
        post = np.exp(ll)
        post /= post.sum()
        return {r: float(pr) for r, pr in zip(self.roles, post)}


# --------------------------------------------------------------------------- blending with names

#: The lexical score at which a name counts as fully informative (the data then only breaks ties).
INFORMATIVE_NAME = 0.6
#: The weight of the data when the name says nothing, and when it is fully informative.
TS_WEIGHT_UNINFORMATIVE = 0.8
TS_WEIGHT_INFORMATIVE = 0.1


def blend(lexical: float, timeseries: float, best_lexical: float) -> float:
    """A combined score in which an informative name dominates.

    ``best_lexical`` is the token's best lexical score over all roles. Its informativeness ``g``
    rises linearly to 1 at :data:`INFORMATIVE_NAME`; the data's weight falls from
    :data:`TS_WEIGHT_UNINFORMATIVE` (no usable name) to :data:`TS_WEIGHT_INFORMATIVE` (a clear
    name: the data breaks ties between the roles the name leaves open).
    """
    g = min(1.0, max(0.0, best_lexical / INFORMATIVE_NAME))
    w = (1.0 - g) * TS_WEIGHT_UNINFORMATIVE + g * TS_WEIGHT_INFORMATIVE
    return lexical + w * timeseries
