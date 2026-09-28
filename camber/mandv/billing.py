"""Billing-period energy series: bills with their own start, end and day count (provisional).

A utility bill is not a day and not a calendar month: it covers its own service period (28--35
days for most meters, sometimes an estimated read), and its energy belongs to *that* period's
weather. Pairing a bill's total with one day's temperature -- what a daily resample of bill rows
does -- or counting bills as if they were days both give wrong models and wrong detector windows.

:class:`BillingSeries` carries, per bill: ``start`` (first day served, inclusive), ``end`` (the
day after the last day served, exclusive), ``days``, ``energy`` (the bill's total), an
``estimated`` read flag, and the energy ``units``. :meth:`BillingSeries.energy_vs_temp` pairs each
bill with the day-weighted mean temperature and the heating / cooling degree-days of its own
period, and expresses energy **per day of the period** (``energy / days``) so bills of different
lengths are comparable; ``days`` is carried alongside as the weight for anything that sums bills
back up (:meth:`BillingSeries.total`).

Conventions
-----------
* Periods are half-open day ranges ``[start, end)``. :meth:`BillingSeries.from_frame` takes the
  bill's printed end date and treats it as **inclusive** by default (``end_inclusive=True``, the
  way bills print "01/01 -- 01/31"), so ``end`` is stored as the next day.
* :meth:`BillingSeries.from_reads` takes a series indexed by **read dates**: each bill covers the
  days after the previous read up to and including its own read, ``(prev_read, read]``. The first
  bill's start comes from ``first_start`` or, failing that, the median bill length. A series
  whose every stamp is the first of a month is read as **month labels** instead (each row covers
  its calendar month), the usual shape of a month-start ``freq="MS"`` table.
* Periods may leave gaps (a missing bill) but must not overlap.

:func:`as_billing_series` is what the non-routine detectors in :mod:`camber.mandv.nonroutine`
use to recognise billing input: a :class:`BillingSeries`, or a plain energy series spaced a week
or more apart paired with a temperature series that is daily or finer.

**Fitting bills** (0.92). A bill's per-day energy is the mean of ``days`` daily values, so its
variance falls as ``1 / days``: fit it by least squares weighted by ``days`` (``weights=`` on
:func:`~camber.mandv.models.fit_model`, :func:`~camber.mandv.stats.fit_stats` and the SEP method
selector), and sum a per-day model's predictions back to energy with the same days (``days=`` on
the savings functions). The config ``mv`` path does both for a ``bills`` entry
(docs/MANDV.md, "Billing data").

**Estimated reads** (0.92). An estimated bill's energy is the utility's guess; the next actual
read corrects the running total, so the estimate and that correction belong together.
:meth:`BillingSeries.merge_estimated` merges each run of estimated bills into the next actual bill
(one period, the summed energy) and drops an estimated bill that no contiguous actual read
follows, recording both in :attr:`BillingSeries.merged`. :meth:`BillingSeries.from_csv` reads a
bills table (start, end, energy, and optional units and estimated columns).

Provisional (0.90.1): names and signatures may change in a minor release.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "BillingSeries",
    "as_billing_series",
    "is_billing_like",
    "daily_weather",
]

_DAY = pd.Timedelta(days=1)

# A series spaced at least this far apart is billing-like (weekly reads and coarser).
_BILLING_MIN_SPACING_DAYS = 7.0


@dataclass
class BillingSeries:
    """Energy per billing period (provisional).

    ``frame`` has one row per bill, in date order, indexed by ``start`` with the columns
    ``start``, ``end`` (exclusive), ``days``, ``energy`` and ``estimated``. ``units`` names the
    energy unit (free text, e.g. ``"kWh"`` or ``"therm"``). Build one with :meth:`from_frame` or
    :meth:`from_reads`.
    """

    frame: pd.DataFrame
    units: str | None = None
    # what merge_estimated() did: one dict per merged run or dropped estimate (provisional, 0.92)
    merged: list = field(default_factory=list)

    def __post_init__(self):
        f = self.frame
        missing = {"start", "end", "energy"} - set(f.columns)
        if missing:
            raise ValueError(f"billing frame is missing columns {sorted(missing)}")
        f = f.copy()
        f["start"] = pd.DatetimeIndex(f["start"]).normalize()
        f["end"] = pd.DatetimeIndex(f["end"]).normalize()
        f = f.sort_values("start", kind="stable").reset_index(drop=True)
        days = (f["end"] - f["start"]) / _DAY
        if (days <= 0).any():
            raise ValueError("every bill must end after it starts")
        if "days" in f.columns and not np.allclose(f["days"].to_numpy(float), days.to_numpy()):
            raise ValueError("'days' disagrees with the start and end dates")
        f["days"] = days.astype(int)
        if len(f) > 1 and (f["start"].to_numpy()[1:] < f["end"].to_numpy()[:-1]).any():
            raise ValueError("billing periods overlap")
        f["energy"] = pd.to_numeric(f["energy"], errors="coerce").astype(float)
        est = f["estimated"] if "estimated" in f.columns else False
        f["estimated"] = pd.Series(est, index=f.index).fillna(False).astype(bool)
        f.index = pd.DatetimeIndex(f["start"], name="start")
        self.frame = f[["start", "end", "days", "energy", "estimated"]]

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_frame(
        cls,
        df: pd.DataFrame,
        *,
        start: str = "start",
        end: str = "end",
        energy: str = "energy",
        estimated: str | None = None,
        units: str | None = None,
        end_inclusive: bool = True,
    ) -> BillingSeries:
        """Bills from a table with a start date, an end date and the energy per bill.

        ``end_inclusive`` (default) reads the end date as the last day served, as bills print it;
        pass ``False`` when it is already the exclusive end (the next bill's start).
        """
        out = pd.DataFrame(
            {
                "start": pd.to_datetime(df[start]).to_numpy(),
                "end": (pd.to_datetime(df[end]) + (_DAY if end_inclusive else pd.Timedelta(0)))
                .dt.normalize()
                .to_numpy(),
                "energy": df[energy].to_numpy(),
                "estimated": df[estimated].to_numpy() if estimated else False,
            }
        )
        return cls(out, units=units)

    @classmethod
    def from_csv(
        cls,
        path,
        *,
        start: str = "start",
        end: str = "end",
        energy: str = "energy",
        estimated: str | None = "estimated",
        units: str | None = None,
        units_column: str = "units",
        end_inclusive: bool = True,
    ) -> BillingSeries:
        """Bills from a CSV file: one row per bill (provisional, 0.92).

        The columns are named by ``start``, ``end`` and ``energy``; ``estimated`` names an optional
        estimated-read flag (``true``/``false``, ``yes``/``no``, ``1``/``0``, ``E``/``A`` -- a
        missing column means every read is actual). ``units`` names the energy unit; without it a
        ``units_column`` holding one value is used, and more than one unit in that column is an
        error. ``end_inclusive`` is as in :meth:`from_frame`. Raises ``ValueError`` on a missing
        column or an unreadable date or flag.
        """
        df = pd.read_csv(path, encoding="utf-8-sig")
        missing = [c for c in (start, end, energy) if c not in df.columns]
        if missing:
            raise ValueError(f"bills file has no column(s) {missing}; it has {list(df.columns)}")
        if units is None and units_column in df.columns:
            found = sorted({str(u).strip() for u in df[units_column].dropna()})
            if len(found) > 1:
                raise ValueError(f"bills file mixes energy units {found}; convert to one first")
            units = found[0] if found else None
        flag = None
        if estimated and estimated in df.columns:
            flag = "_estimated"
            df[flag] = [_read_flag(v) for v in df[estimated]]
        try:
            df[start] = pd.to_datetime(df[start])
            df[end] = pd.to_datetime(df[end])
        except (ValueError, TypeError) as e:
            raise ValueError(f"bills file has an unreadable date: {e}") from None
        df[energy] = pd.to_numeric(df[energy], errors="coerce")
        return cls.from_frame(
            df,
            start=start,
            end=end,
            energy=energy,
            estimated=flag,
            units=units,
            end_inclusive=end_inclusive,
        )

    @classmethod
    def from_reads(
        cls,
        energy: pd.Series,
        *,
        first_start=None,
        estimated=None,
        units: str | None = None,
    ) -> BillingSeries:
        """Bills from an energy series indexed by read date (see the module conventions).

        Each bill covers ``(previous read, read]``. ``first_start`` is the first bill's first day;
        without it the first bill is given the median bill length. ``estimated`` is an optional
        boolean sequence aligned with ``energy``. A series stamped on the first of every month is
        read as calendar-month labels instead.
        """
        s = pd.Series(energy).sort_index()
        idx = pd.DatetimeIndex(s.index).normalize()
        if len(idx) < 2 and first_start is None:
            raise ValueError("need two reads, or first_start, to know a bill's length")
        est = (
            np.zeros(len(s), bool)
            if estimated is None
            else pd.Series(estimated).fillna(False).to_numpy(bool)
        )
        if len(idx) and (idx.day == 1).all() and first_start is None:
            starts = idx
            ends = idx + pd.offsets.MonthBegin(1)
        else:
            ends = idx + _DAY
            if first_start is not None:
                first = pd.Timestamp(first_start).normalize()
            else:
                gap = pd.Timedelta(days=float(np.median(np.diff(idx.asi8)) / 86_400e9))
                first = (ends[0] - gap).normalize()
            starts = pd.DatetimeIndex([first]).append(ends[:-1])
        frame = pd.DataFrame(
            {"start": starts, "end": ends, "energy": s.to_numpy(float), "estimated": est}
        )
        return cls(frame, units=units)

    def merge_estimated(self) -> BillingSeries:
        """Merge each run of estimated bills into the next actual bill (provisional, 0.92).

        A utility's estimated read is trued up by the next actual read: the actual bill carries
        the correction, so neither bill alone is a measurement, but together they are. Each run of
        consecutive estimated bills followed, with no gap, by an actual bill becomes one bill
        from the first estimate's start to the actual bill's end, with the summed energy and
        ``estimated=False``. An estimated bill that no contiguous actual read follows (the last
        bill, or one before a missing bill) is dropped. :attr:`merged` lists every merge
        (``{"action": "merged", "start", "end", "n_estimated"}``) and drop
        (``{"action": "dropped", "start", "end"}``), with ``end`` the last day served; a series
        with no estimated read is returned unchanged.
        """
        f = self.frame
        if not f["estimated"].any():
            return self
        rows: list = []
        log: list = []
        run: list = []  # pending estimated bills

        def _drop(run):
            for r in run:
                log.append({"action": "dropped", "start": _ds(r.start), "end": _ds(r.end - _DAY)})

        for r in f.itertuples(index=False):
            if run and r.start != run[-1].end:  # a gap: the pending estimates cannot be trued up
                _drop(run)
                run = []
            if r.estimated:
                run.append(r)
                continue
            if run:
                energy = float(sum(x.energy for x in run) + r.energy)
                rows.append((run[0].start, r.end, energy))
                log.append(
                    {
                        "action": "merged",
                        "start": _ds(run[0].start),
                        "end": _ds(r.end - _DAY),
                        "n_estimated": len(run),
                    }
                )
                run = []
            else:
                rows.append((r.start, r.end, float(r.energy)))
        _drop(run)
        out = pd.DataFrame(rows, columns=["start", "end", "energy"])
        out["estimated"] = False
        return BillingSeries(out, units=self.units, merged=list(self.merged) + log)

    def converted(self, unit: str, *, heat_content=None, enthalpy=None) -> BillingSeries:
        """The same bills with their energy in ``unit`` (provisional, 0.92, #69).

        :attr:`units` must name a unit :func:`camber.energy_units.parse_unit` reads; a gas volume
        (``Mcf``, ``CCF``, ``m3``) needs ``heat_content`` and a steam mass (``lb``, ``klb``)
        ``enthalpy``, with no default. Raises ``ValueError`` otherwise.
        """
        from ..energy_units import energy_factor, parse_unit

        if not self.units:
            raise ValueError("these bills name no unit; set units= before converting")
        k = energy_factor(self.units, unit, heat_content=heat_content, enthalpy=enthalpy)
        f = self.frame.reset_index(drop=True)
        f["energy"] = f["energy"] * k
        return BillingSeries(
            f, units=parse_unit(unit, kind="energy").name, merged=list(self.merged)
        )

    # ------------------------------------------------------------------ views
    def __len__(self) -> int:
        return len(self.frame)

    @property
    def days(self) -> pd.Series:
        """Days per bill (the natural weight of a per-day value)."""
        return self.frame["days"]

    def per_day(self) -> pd.Series:
        """Energy per day of each bill (``energy / days``), indexed by bill start."""
        return self.frame["energy"] / self.frame["days"]

    def total(self, per_day=None) -> float:
        """Total energy, or -- given a per-day series aligned with the bills -- its day-weighted
        total (``sum(per_day * days)``), the way a per-day model's predictions sum back to bills.
        """
        if per_day is None:
            return float(self.frame["energy"].sum())
        v = np.asarray(per_day, dtype=float)
        return float(np.sum(v * self.frame["days"].to_numpy(float)))

    def period_weather(
        self,
        temp: pd.Series,
        *,
        base_f: float = 65.0,
        heating_base_f: float | None = None,
        cooling_base_f: float | None = None,
    ) -> pd.DataFrame:
        """Each bill's day-weighted mean temperature and heating / cooling degree-days.

        Temperatures are averaged per day first (sub-daily data: the hourly means), so a period's
        mean weights every covered day equally. Degree-days use the hourly temperatures when the
        series is sub-daily (so a cold morning under a mild daily mean still counts) and the daily
        mean otherwise; a period with some days missing is scaled to its full length. Columns:
        ``oat``, ``hdd``, ``cdd`` and ``coverage`` (the share of the period's days that had
        temperature data).
        """
        hb = base_f if heating_base_f is None else heating_base_f
        cb = base_f if cooling_base_f is None else cooling_base_f
        d = daily_weather(temp, heating_base_f=hb, cooling_base_f=cb)
        rows = []
        for s, e, n in zip(self.frame["start"], self.frame["end"], self.frame["days"]):
            w = d.loc[s : e - _DAY]
            cov = len(w) / float(n)
            if len(w):
                rows.append(
                    (
                        float(w["oat"].mean()),
                        float(w["hdd"].mean() * n),
                        float(w["cdd"].mean() * n),
                        cov,
                    )
                )
            else:
                rows.append((np.nan, np.nan, np.nan, 0.0))
        return pd.DataFrame(rows, index=self.frame.index, columns=["oat", "hdd", "cdd", "coverage"])

    def energy_vs_temp(
        self,
        temp: pd.Series,
        *,
        base_f: float = 65.0,
        min_coverage: float = 0.9,
    ) -> pd.DataFrame:
        """Per-day energy of each bill against its own period's weather, ready to fit.

        Columns ``energy`` (per day), ``oat`` (period mean), ``hdd`` / ``cdd`` (period totals),
        ``days``, ``start``, ``end``, ``estimated`` and ``coverage``; indexed by bill start.
        Bills with less than ``min_coverage`` of their days covered by temperature data, a
        missing energy value, or negative energy are dropped.
        """
        w = self.period_weather(temp, base_f=base_f)
        f = self.frame
        out = pd.DataFrame(
            {
                "energy": self.per_day(),
                "oat": w["oat"],
                "hdd": w["hdd"],
                "cdd": w["cdd"],
                "days": f["days"],
                "start": f["start"],
                "end": f["end"],
                "estimated": f["estimated"],
                "coverage": w["coverage"],
            },
            index=f.index,
        )
        keep = (
            out["energy"].notna()
            & out["oat"].notna()
            & (out["coverage"] >= min_coverage)
            & (out["energy"] >= 0)
        )
        out = out[keep]
        out.attrs["billing"] = True
        return out


def _ds(x) -> str:
    """A date as ``YYYY-MM-DD``."""
    return str(pd.Timestamp(x).date())


_TRUE = {"true", "t", "yes", "y", "1", "e", "est", "estimated"}
_FALSE = {"false", "f", "no", "n", "0", "a", "act", "actual", ""}


def _read_flag(v) -> bool:
    """An estimated-read flag cell as a bool (``ValueError`` on anything unrecognised)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return False
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return bool(v)
    t = str(v).strip().lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    try:  # a numeric cell read as text ("0.0", "1.0") in a column that mixes forms
        return bool(float(t))
    except ValueError:
        pass
    raise ValueError(f"unreadable estimated-read flag {v!r} (use true/false, yes/no, 1/0, E/A)")


def daily_weather(
    temp: pd.Series, *, heating_base_f: float = 65.0, cooling_base_f: float = 65.0
) -> pd.DataFrame:
    """Daily mean temperature and heating / cooling degree-days (per day) from ``temp``.

    A sub-daily series is averaged to hourly means first; each day's degree-days are then the mean
    hourly contribution (so a day with a few missing hours is not undercounted). A daily (or
    coarser) series uses its values as the daily means.
    """
    t = pd.Series(temp).dropna().sort_index()
    t.index = pd.DatetimeIndex(t.index)
    if t.empty:
        return pd.DataFrame(columns=["oat", "hdd", "cdd"], dtype=float)
    step = float(np.median(np.diff(t.index.asi8))) / 3.6e12 if len(t) > 1 else 24.0
    if step < 23.0:  # sub-daily
        h = t.resample("1h").mean().dropna()
        day = h.index.normalize()
        oat = h.groupby(day).mean()
        hdd = (heating_base_f - h).clip(lower=0).groupby(day).mean()
        cdd = (h - cooling_base_f).clip(lower=0).groupby(day).mean()
    else:
        oat = t.groupby(t.index.normalize()).mean()
        hdd = (heating_base_f - oat).clip(lower=0)
        cdd = (oat - cooling_base_f).clip(lower=0)
    return pd.DataFrame({"oat": oat, "hdd": hdd, "cdd": cdd})


def _median_spacing_days(index) -> float:
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return float("nan")
    d = np.diff(idx.sort_values().asi8) / 86_400e9
    d = d[d > 0]
    return float(np.median(d)) if len(d) else float("nan")


def is_billing_like(energy, temp) -> bool:
    """Whether ``energy`` is billing-period data to pair with a finer ``temp`` series.

    True for a :class:`BillingSeries`, or for a series whose median spacing is a week or more
    while ``temp`` is daily or finer. (Energy and temperature on the same coarse grid -- monthly
    energy with monthly mean temperatures -- are already paired and are not billing-like here.)
    """
    if isinstance(energy, BillingSeries):
        return True
    e_sp = _median_spacing_days(pd.Series(energy).dropna().index)
    t_sp = _median_spacing_days(pd.Series(temp).dropna().index)
    return bool(e_sp >= _BILLING_MIN_SPACING_DAYS and t_sp <= 1.0 + 1e-9)


def as_billing_series(energy) -> BillingSeries:
    """``energy`` as a :class:`BillingSeries` (a plain series is read with
    :meth:`BillingSeries.from_reads`)."""
    if isinstance(energy, BillingSeries):
        return energy
    return BillingSeries.from_reads(pd.Series(energy).dropna())
