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
    "Calendarized",
    "as_billing_series",
    "calendarize",
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
        cols = ["start", "end", "days", "energy", "estimated"]
        if "cost" in f.columns:  # 0.94 (#72): the billed cost, carried only when given
            f["cost"] = pd.to_numeric(f["cost"], errors="coerce").astype(float)
            cols.append("cost")
        self.frame = f[cols]

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
        cost: str | None = None,
    ) -> BillingSeries:
        """Bills from a table with a start date, an end date and the energy per bill.

        ``end_inclusive`` (default) reads the end date as the last day served, as bills print it;
        pass ``False`` when it is already the exclusive end (the next bill's start). ``cost``
        (0.94, #72) names a column of each bill's billed cost, carried as ``frame["cost"]``.
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
        if cost:
            out["cost"] = pd.to_numeric(df[cost], errors="coerce").to_numpy()
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
        cost: str | None = None,
    ) -> BillingSeries:
        """Bills from a CSV file: one row per bill (provisional, 0.92).

        The columns are named by ``start``, ``end`` and ``energy``; ``estimated`` names an optional
        estimated-read flag (``true``/``false``, ``yes``/``no``, ``1``/``0``, ``E``/``A`` -- a
        missing column means every read is actual). ``units`` names the energy unit; without it a
        ``units_column`` holding one unit is used, and more than one unit in that column is an
        error. Spellings of one unit (``kWh`` / ``kwh`` / ``kilowatt-hours``) are one unit (0.93,
        #70): the column's unit is then the canonical name. ``end_inclusive`` is as in
        :meth:`from_frame`, and so is ``cost`` (0.94: a column of billed cost, read only when
        named). Raises ``ValueError`` on a missing column or an unreadable date or flag.
        """
        df = pd.read_csv(path, encoding="utf-8-sig")
        missing = [
            c for c in (start, end, energy, *([cost] if cost else [])) if c not in df.columns
        ]
        if missing:
            raise ValueError(f"bills file has no column(s) {missing}; it has {list(df.columns)}")
        if units is None and units_column in df.columns:
            units = _one_unit(df[units_column])
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
            cost=cost,
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
        has_cost = "cost" in f.columns
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
                c = float(sum(x.cost for x in run) + r.cost) if has_cost else np.nan
                rows.append((run[0].start, r.end, energy, c))
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
                rows.append((r.start, r.end, float(r.energy), r.cost if has_cost else np.nan))
        _drop(run)
        out = pd.DataFrame(rows, columns=["start", "end", "energy", "cost"])
        if not has_cost:
            out = out.drop(columns="cost")
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
        heating_base_f: float | None = None,
        cooling_base_f: float | None = None,
    ) -> pd.DataFrame:
        """Per-day energy of each bill against its own period's weather, ready to fit.

        Columns ``energy`` (per day), ``oat`` (period mean), ``hdd`` / ``cdd`` (period totals),
        ``days``, ``start``, ``end``, ``estimated`` and ``coverage``; indexed by bill start.
        Bills with less than ``min_coverage`` of their days covered by temperature data, a
        missing energy value, or negative energy are dropped.

        ``heating_base_f`` / ``cooling_base_f`` (0.94, #72) give the two legs their own bases
        (each defaults to ``base_f``); the frame's ``attrs`` record the bases its HDD / CDD are
        at. A ``cost`` column is carried when the bills have one.
        """
        hb = base_f if heating_base_f is None else heating_base_f
        cb = base_f if cooling_base_f is None else cooling_base_f
        w = self.period_weather(temp, heating_base_f=hb, cooling_base_f=cb)
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
        if "cost" in f.columns:
            out["cost"] = f["cost"]
        keep = (
            out["energy"].notna()
            & out["oat"].notna()
            & (out["coverage"] >= min_coverage)
            & (out["energy"] >= 0)
        )
        out = out[keep]
        out.attrs["billing"] = True
        out.attrs["heating_base_f"] = float(hb)
        out.attrs["cooling_base_f"] = float(cb)
        return out

    def calendarize(self, temp=None, **kw) -> Calendarized:
        """The bills prorated into calendar months (:func:`calendarize`; provisional, 0.94).
        Months touched by a merged run of estimated reads are flagged estimated."""
        spans = [
            (pd.Timestamp(m["start"]), pd.Timestamp(m["end"]) + _DAY)
            for m in self.merged
            if m.get("action") == "merged"
        ]
        return calendarize(self.frame, temp, estimated_spans=spans, units=self.units, **kw)


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


def _one_unit(column) -> str | None:
    """The one unit a bills file's ``units`` column names, or ``None`` when it is empty.

    Spellings of the same unit count as one (0.93, #70): each distinct spelling is parsed
    (:func:`camber.energy_units.parse_unit`), and one that does not parse is compared ignoring case
    and spacing. A single spelling is returned as written, several spellings of one unit as the
    canonical name; two different units raise ``ValueError``.
    """
    from ..energy_units import _norm, parse_unit

    found = sorted({str(u).strip() for u in column.dropna()})
    if len(found) <= 1:
        return found[0] if found else None
    keys: dict = {}
    for u in found:
        try:
            k = parse_unit(u).name
        except ValueError:
            k = "?" + _norm(u)
        keys.setdefault(k, []).append(u)
    if len(keys) > 1:
        raise ValueError(f"bills file mixes energy units {found}; convert to one first")
    (k,) = keys
    return k if not k.startswith("?") else found[0]


# --------------------------------------------------------------------------- calendarization
# (provisional, 0.94, #72)


@dataclass
class Calendarized:
    """Bills prorated into calendar months (:func:`calendarize`; provisional, 0.94).

    ``months`` has one row per calendar month the bills touch, indexed by the month's first day:
    ``days_in_month``, ``days_covered`` (days served by exactly one bill), ``complete`` (every day
    of the month served, the first and the last included, by exactly one bill), ``energy``,
    ``energy_per_day``, ``cost`` (when the bills carry it), ``n_bills``, ``estimated`` (a day of
    the month came from an estimated read) and, with a temperature series, ``oat``, ``hdd``,
    ``cdd`` and ``weather_coverage``. ``gaps`` / ``overlaps`` list the unserved and doubly served
    stretches (``start``, ``end`` inclusive, ``days``). ``declined_reason`` is set when a gap or
    overlap is longer than ``max_gap_days``: Portfolio Manager computes no metric then, and neither
    do :meth:`annual` and :meth:`total` (they return ``None``).
    """

    months: pd.DataFrame
    gaps: list
    overlaps: list
    units: str | None = None
    heating_base_f: float | None = None
    cooling_base_f: float | None = None
    max_gap_days: int = 0
    declined_reason: str | None = None

    @property
    def declined(self) -> bool:
        return self.declined_reason is not None

    def total(self, start, end) -> dict | None:
        """Totals over the calendar months ``start`` .. ``end`` (any date inside each; inclusive),
        or ``None`` when declined or when a month in the range is incomplete or missing."""
        if self.declined:
            return None
        a = pd.Timestamp(start).to_period("M").to_timestamp()
        b = pd.Timestamp(end).to_period("M").to_timestamp()
        want = pd.date_range(a, b, freq="MS")
        m = self.months.reindex(want)
        if m["complete"].isna().any() or not m["complete"].astype(bool).all():
            return None
        out = {
            "months": [str(x.date())[:7] for x in want],
            "energy": float(m["energy"].sum()),
            "days": int(m["days_in_month"].sum()),
            "estimated_months": int(m["estimated"].astype(bool).sum()),
        }
        for c in ("cost", "hdd", "cdd"):
            if c in m.columns:
                out[c] = float(m[c].sum())
        return out

    def annual(self) -> list:
        """Calendar-year totals: one dict per year with all 12 months complete (``[]`` when
        declined)."""
        if self.declined:
            return []
        out = []
        for y in sorted(set(self.months.index.year)):
            t = self.total(f"{y}-01-01", f"{y}-12-31")
            if t is not None:
                out.append({"year": int(y), **{k: v for k, v in t.items() if k != "months"}})
        return out

    def as_dict(self) -> dict:
        m = self.months
        rows = []
        for ts, r in m.iterrows():
            d: dict = {"month": str(ts.date())[:7]}
            for k, v in r.items():
                if isinstance(v, (bool, np.bool_)):
                    d[k] = bool(v)
                elif isinstance(v, (int, np.integer)):
                    d[k] = int(v)
                else:
                    fv = float(v)
                    d[k] = round(fv, 6) if np.isfinite(fv) else None
            rows.append(d)
        return {
            "method": "ENERGY STAR Portfolio Manager: energy per day of each bill, prorated to "
            "calendar months (Technical Reference, Thermal Energy Conversions, Figure 1 step 3)",
            "units": self.units,
            "heating_base_f": self.heating_base_f,
            "cooling_base_f": self.cooling_base_f,
            "max_gap_days": int(self.max_gap_days),
            "declined": self.declined,
            "declined_reason": self.declined_reason,
            "gaps": list(self.gaps),
            "overlaps": list(self.overlaps),
            "months": rows,
            "annual": self.annual(),
        }


def calendarize(
    bills,
    temp=None,
    *,
    heating_base_f: float = 65.0,
    cooling_base_f: float = 65.0,
    max_gap_days: int = 0,
    estimated_spans=(),
    units: str | None = None,
) -> Calendarized:
    """Prorate bills into calendar months, the ENERGY STAR Portfolio Manager way (provisional).

    Each bill's energy (and cost) is divided by its days and each day is assigned to its calendar
    month, so a bill from January 15 to February 14 splits by its days in each (Portfolio Manager
    Technical Reference, *Thermal Energy Conversions*, Figure 1 step 3). ``bills`` is a
    :class:`BillingSeries` or a table with ``start``, ``end`` (exclusive) and ``energy``, and
    optionally ``estimated`` and ``cost``; unlike :class:`BillingSeries` a table may overlap, so the
    overlap can be reported. Portfolio Manager computes no metric when bills leave a gap or
    overlap; here a gap or overlap longer than ``max_gap_days`` (default 0: any) sets
    ``declined_reason`` and the totals are withheld, while the months are still listed with their
    coverage. ``estimated_spans`` (``(start, exclusive end)`` pairs, e.g. merged estimated reads)
    flag the months they touch as estimated, like an estimated bill does.

    With ``temp`` the months also get their mean temperature and HDD / CDD at the given bases,
    from the same daily series the bills are paired with (:func:`daily_weather`); a month with
    some days missing is scaled to its length, as a bill is.

    The calendarized months are an output view: models are fitted on the bills' own periods, and
    a month is never fed back into a fit.
    """
    f = bills.frame if isinstance(bills, BillingSeries) else pd.DataFrame(bills)
    if isinstance(bills, BillingSeries) and units is None:
        units = bills.units
    f = f.reset_index(drop=True)
    f["start"] = pd.DatetimeIndex(f["start"]).normalize()
    f["end"] = pd.DatetimeIndex(f["end"]).normalize()
    f = f.sort_values("start", kind="stable").reset_index(drop=True)
    days = ((f["end"] - f["start"]) / _DAY).astype(int)
    if (days <= 0).any():
        raise ValueError("every bill must end after it starts")
    energy = pd.to_numeric(f["energy"], errors="coerce").to_numpy(float)
    has_cost = "cost" in f.columns
    cost = pd.to_numeric(f["cost"], errors="coerce").to_numpy(float) if has_cost else None
    est = f["estimated"].fillna(False).astype(bool).to_numpy() if "estimated" in f else None
    lo, hi = f["start"].min(), f["end"].max()
    cal = pd.date_range(lo, hi - _DAY, freq="D")
    n = len(cal)
    served = np.zeros(n, int)
    e_day = np.zeros(n)
    c_day = np.zeros(n)
    est_day = np.zeros(n, bool)
    bill_id = np.full(n, -1)
    base = lo.value
    for k, (s0, d) in enumerate(zip(f["start"], days)):
        i0 = int((s0.value - base) // 86_400_000_000_000)
        i1 = i0 + int(d)
        served[i0:i1] += 1
        e_day[i0:i1] += energy[k] / d
        if cost is not None:
            c_day[i0:i1] += cost[k] / d
        if est is not None and est[k]:
            est_day[i0:i1] = True
        bill_id[i0:i1] = k
    for a, b in estimated_spans or ():
        a, b = pd.Timestamp(a).normalize(), pd.Timestamp(b).normalize()
        i0 = max(0, int((a.value - base) // 86_400_000_000_000))
        i1 = min(n, int((b.value - base) // 86_400_000_000_000))
        if i1 > i0:
            est_day[i0:i1] = True

    def runs(mask) -> list:
        out = []
        i = 0
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                out.append({"start": _ds(cal[i]), "end": _ds(cal[j - 1]), "days": int(j - i)})
                i = j
            else:
                i += 1
        return out

    gaps, overlaps = runs(served == 0), runs(served > 1)
    worst = max([g["days"] for g in gaps + overlaps] or [0])
    why = None
    if worst > int(max_gap_days):
        bits = []
        if gaps:
            bits.append(f"{len(gaps)} gap(s) ({sum(g['days'] for g in gaps)} days)")
        if overlaps:
            bits.append(f"{len(overlaps)} overlap(s) ({sum(o['days'] for o in overlaps)} days)")
        why = (
            " and ".join(bits)
            + " between bills: Portfolio Manager computes no metric across a gap "
            "or an overlap, so the calendar totals are withheld"
        )
    # full calendar months touched by the bills (the first and last may be partial)
    m0, m1 = lo.to_period("M").to_timestamp(), (hi - _DAY).to_period("M").to_timestamp()
    mdays = pd.date_range(m0, m1 + pd.offsets.MonthEnd(0), freq="D")
    s = pd.DataFrame(
        {"served": 0, "energy": 0.0, "cost": 0.0, "est": False, "bill": -1},
        index=mdays,
    )
    s.loc[cal, "served"] = served
    s.loc[cal, "energy"] = e_day
    s.loc[cal, "cost"] = c_day
    s.loc[cal, "est"] = est_day
    s.loc[cal, "bill"] = bill_id
    month = s.index.to_period("M").to_timestamp()
    g = s.groupby(month)
    months = pd.DataFrame(
        {
            "days_in_month": g.size().astype(int),
            "days_covered": g["served"].apply(lambda v: int((v == 1).sum())),
            "energy": g["energy"].sum(),
            "estimated": g["est"].any(),
            "n_bills": g["bill"].apply(lambda v: int(len(set(v[v >= 0])))),
        }
    )
    months["complete"] = months["days_covered"] == months["days_in_month"]
    months["energy_per_day"] = months["energy"] / months["days_covered"].where(
        months["days_covered"] > 0
    )
    if has_cost:
        months["cost"] = g["cost"].sum()
    months = months[
        ["days_in_month", "days_covered", "complete", "energy", "energy_per_day"]
        + (["cost"] if has_cost else [])
        + ["n_bills", "estimated"]
    ]
    if temp is not None:
        d = daily_weather(temp, heating_base_f=heating_base_f, cooling_base_f=cooling_base_f)
        d = d.reindex(mdays)
        gw = d.groupby(month)
        cnt = gw["oat"].count()
        months["oat"] = gw["oat"].mean()
        months["hdd"] = gw["hdd"].mean() * months["days_in_month"]
        months["cdd"] = gw["cdd"].mean() * months["days_in_month"]
        months["weather_coverage"] = cnt / months["days_in_month"]
    months.index.name = "month"
    return Calendarized(
        months,
        gaps,
        overlaps,
        units=units,
        heating_base_f=float(heating_base_f) if temp is not None else None,
        cooling_base_f=float(cooling_base_f) if temp is not None else None,
        max_gap_days=int(max_gap_days),
        declined_reason=why,
    )
