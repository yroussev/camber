"""The model form of a config ``mv`` entry: change-point only, or change-point + drivers (private).

``mv[].model`` picks the form (#21 phase 21c's multivariable model, wired into the config path in
0.90):

* ``"change_point"`` (the default) -- :func:`~camber.mandv.models.best_model` on daily mean
  outdoor temperature, as before;
* ``"cp_driver"`` -- :func:`~camber.mandv.multivariable.fit_cp_driver_model` with the
  ``mv[].drivers`` columns. A driver is ``"weekday"`` (1 Monday to Friday, else 0),
  ``"occupied_day"`` (1 on ``mv[].occupied_weekdays``, default Monday to Friday, except the dates
  in ``mv[].holidays`` and the days of ``mv[].holiday_calendar``), or the name of a mapped numeric
  role (its daily mean, e.g. ``"occupancy"``).

``mv[].holiday_calendar`` (0.93, #68; provisional) is a :class:`camber.calendars.HolidayCalendar`
spec: a country code (``"NO"``, ``"US"``, ``"ES-CL"``) for its bundled public holidays, or
``{"country", "subdivision", "files", "dates"}`` adding calendar CSV files (school breaks, an
academic calendar) and single dates. Relative ``files`` resolve against the config's folder
(:func:`with_base_dir`). A day of the calendar is unoccupied, like a weekend.

``"break_day"`` (0.93) is for days that are neither: a school's term break, when staff, cleaning
and after-school care keep part of the building running. It is 1 on an occupied weekday (not a
holiday) that falls in ``mv[].break_calendar`` (the same spec form), so the model fits the break
its own coefficient instead of forcing it to a weekday or a weekend.

The drivers travel **as columns of the daily frame** (``drv:<name>``), so every slice of it --
baseline, reporting, intermediate, a rebaseline window -- carries them, and the design a model
needs is chosen by the model itself (:func:`design_rows`): a frozen change-point + driver model
reads its own driver columns wherever it is used. A frame without driver columns fits and
predicts exactly as before.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PREFIX = "drv:"
MODELS = ("change_point", "cp_driver")
CALENDAR = ("weekday", "occupied_day", "break_day")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def spec_of(entry: dict) -> tuple:
    """Validate ``model`` / ``drivers`` of an ``mv`` entry: ``(model, drivers)``.

    Raises ``ValueError`` (a config error) on an unknown form, a ``cp_driver`` entry without
    drivers, drivers without ``cp_driver``, an unknown driver name, or a method the form cannot
    serve.
    """
    from ..model.roles import Role

    model = entry.get("model") or "change_point"
    if model not in MODELS:
        raise ValueError(f"mv.model must be one of {MODELS}, got {model!r}")
    drivers = entry.get("drivers")
    if model == "change_point":
        if drivers:
            raise ValueError('mv.drivers needs mv.model "cp_driver"')
        return model, ()
    if not isinstance(drivers, (list, tuple)) or not drivers:
        raise ValueError('mv.model "cp_driver" needs drivers: a non-empty list, e.g. ["weekday"]')
    names = []
    for d in drivers:
        if not isinstance(d, str):
            raise ValueError(f"mv.drivers: each driver is a name, got {d!r}")
        if d not in CALENDAR:
            try:
                Role(d)
            except ValueError:
                raise ValueError(
                    f"mv.drivers: {d!r} is neither {' nor '.join(CALENDAR)} nor a mapped role"
                ) from None
            if d == Role.OAT.value:
                raise ValueError(
                    "mv.drivers: outdoor temperature is already the model's first term"
                )
        if d in names:
            raise ValueError(f"mv.drivers: {d!r} is listed twice")
        names.append(d)
    method = entry.get("method")
    if method in ("auto", "standard_conditions"):
        raise ValueError(
            f'mv.method {method!r} is not available with mv.model "cp_driver": '
            + (
                "the SEP method proposal ranks temperature-only models"
                if method == "auto"
                else "normal_year gives standard temperatures only, not standard driver values"
            )
        )
    days_drivers = {"occupied_day", "break_day"} & set(names)
    for key in ("occupied_weekdays", "holidays", "holiday_calendar"):
        if entry.get(key) is not None and not days_drivers:
            raise ValueError(f'mv.{key} needs the "occupied_day" (or "break_day") driver')
    if ("break_calendar" in entry) != ("break_day" in names):
        raise ValueError('mv.break_calendar and the "break_day" driver go together')
    _occupied_weekdays(entry)
    _holidays(entry)
    _calendar(entry)
    _calendar(entry, "break_calendar")
    return model, tuple(names)


def with_base_dir(entry: dict, base_dir: str) -> dict:
    """``entry`` with its calendars' ``files`` made absolute against ``base_dir`` (a copy;
    the entry itself when there is nothing to resolve)."""
    import os

    out = entry
    for key in ("holiday_calendar", "break_calendar"):
        cal = entry.get(key)
        if not isinstance(cal, dict) or not cal.get("files"):
            continue
        fs = [cal["files"]] if isinstance(cal["files"], str) else list(cal["files"])
        fs = [
            f if not isinstance(f, str) or os.path.isabs(f) else os.path.join(base_dir, f)
            for f in fs
        ]
        out = {**out, key: {**cal, "files": fs}}
    return out


def _calendar(entry: dict, key: str = "holiday_calendar"):
    """The entry's :class:`~camber.calendars.HolidayCalendar` under ``key``, or ``None`` (a
    config error raises ``ValueError`` naming ``mv.<key>``)."""
    spec = entry.get(key)
    if spec is None:
        return None
    from ..calendars import HolidayCalendar

    try:
        return HolidayCalendar.from_spec(spec)
    except ValueError as e:
        raise ValueError(f"mv.{key}: {e}") from None


def _calendar_days(entry: dict, key: str, idx: pd.DatetimeIndex) -> tuple:
    """``(set of dates, per-row "known" flags)`` of the ``key`` calendar over ``idx``. A row in a
    year outside the calendar's bundled coverage is not known (the driver cannot say)."""
    cal = _calendar(entry, key)
    if cal is None or not len(idx):
        return set(), np.ones(len(idx), dtype=bool)
    years = sorted(set(idx.year))
    cov = cal.coverage()
    lo, hi = (years[0], years[-1]) if cov is None else cov
    known = (idx.year >= lo) & (idx.year <= hi)
    inside = [y for y in years if lo <= y <= hi]
    if not inside:
        return set(), np.asarray(known)
    a = max(idx.min(), pd.Timestamp(f"{inside[0]}-01-01"))
    b = min(idx.max(), pd.Timestamp(f"{inside[-1]}-12-31"))
    try:
        return set(cal.days(a, b)), np.asarray(known)
    except ValueError as e:
        raise ValueError(f"mv.{key}: {e}") from None


def driver_roles(entry: dict) -> tuple:
    """The mapped roles an entry's drivers read (calendar drivers need none)."""
    from ..model.roles import Role

    _model, names = spec_of(entry)
    return tuple(Role(d) for d in names if d not in CALENDAR)


def _occupied_weekdays(entry: dict) -> set:
    days = entry.get("occupied_weekdays") or ["mon", "tue", "wed", "thu", "fri"]
    out = set()
    for d in days:
        k = str(d).strip().lower()[:3]
        if k not in _DAYS:
            raise ValueError(f"mv.occupied_weekdays: unknown day {d!r}")
        out.add(_DAYS.index(k))
    return out


def _holidays(entry: dict) -> set:
    out = set()
    for h in entry.get("holidays") or []:
        try:
            out.add(pd.Timestamp(h).date())
        except (ValueError, TypeError):
            raise ValueError(f"mv.holidays: {h!r} is not a date") from None
    return out


def add_drivers(daily: pd.DataFrame, entry: dict, full: pd.DataFrame | None = None):
    """``daily`` with one ``drv:<name>`` column per driver of ``entry`` (unchanged without any).

    A numeric role's column is its daily mean in ``full`` (the resolved hourly frame); a day
    without it is dropped, since the model cannot be evaluated there. So is a day in a year outside
    the coverage of ``mv[].holiday_calendar``'s bundled public holidays (0.93).
    """
    _model, names = spec_of(entry)
    if not names or daily is None:
        return daily
    out = daily.copy()
    idx = pd.DatetimeIndex(out.index)
    for name in names:
        col = PREFIX + name
        if name == "weekday":
            out[col] = (idx.dayofweek < 5).astype(float)
        elif name == "occupied_day":
            occ = _occupied_weekdays(entry)
            hol, known = _calendar_days(entry, "holiday_calendar", idx)
            hol = hol | _holidays(entry)
            out[col] = [
                float(ts.dayofweek in occ and ts.date() not in hol) if ok else np.nan
                for ts, ok in zip(idx, known)
            ]
        elif name == "break_day":
            occ = _occupied_weekdays(entry)
            hol, known = _calendar_days(entry, "holiday_calendar", idx)
            hol = hol | _holidays(entry)
            brk, known_b = _calendar_days(entry, "break_calendar", idx)
            out[col] = [
                float(ts.dayofweek in occ and ts.date() not in hol and ts.date() in brk)
                if ok and ok_b
                else np.nan
                for ts, ok, ok_b in zip(idx, known, known_b)
            ]
        else:
            from ..model.roles import Role

            role = Role(name)
            if full is None or role not in full.columns:
                raise ValueError(f"mv.drivers: no {name!r} data mapped for this meter")
            s = full[role].dropna().sort_index().resample("D").mean()
            if s.index.tz is not None and idx.tz is None:
                s.index = s.index.tz_localize(None)
            out[col] = s.reindex(idx).to_numpy(dtype=float)
    return out.dropna(subset=[PREFIX + n for n in names])


def driver_columns(frame: pd.DataFrame) -> list:
    """The ``drv:`` columns of a daily frame, in order."""
    return [c for c in frame.columns if isinstance(c, str) and c.startswith(PREFIX)]


def design_rows(frame: pd.DataFrame, model) -> np.ndarray:
    """The rows ``model.predict`` takes for ``frame``: outdoor temperature, then the model's own
    drivers for a change-point + driver model (read from the frame's ``drv:`` columns)."""
    names = getattr(model, "driver_names", None)
    T = frame["oat"].to_numpy(dtype=float)
    if not names:
        return T
    missing = [n for n in names if PREFIX + n not in frame.columns]
    if missing:
        raise ValueError(
            f"the model needs driver(s) {missing}: declare them in mv.drivers "
            '(mv.model "cp_driver")'
        )
    return np.column_stack([T] + [frame[PREFIX + n].to_numpy(dtype=float) for n in names])


def row_days(frame: pd.DataFrame):
    """Each row's day count when the frame's rows are bills (a ``days`` column), else ``None``.

    The weights of a billing fit and the multipliers that sum per-day values back to energy
    (0.92, provisional); a daily frame has no ``days`` column and is unweighted, as before."""
    if frame is None or "days" not in frame.columns:
        return None
    return frame["days"].to_numpy(dtype=float)


def ledger_rows(frame: pd.DataFrame, model) -> dict:
    """``index`` / ``drivers`` / ``measured`` rows of ``frame`` for the adjustments ledger.

    Daily rows pass through. Bills are expanded to their days (each day carries its bill's
    drivers and per-day energy), so every ledger sum is ``sum(days * value)`` -- energy -- and an
    adjustment is dated to the day, not the bill."""
    X = design_rows(frame, model)
    d = row_days(frame)
    if d is None:
        return {"index": frame.index, "drivers": X, "measured": frame["energy"].to_numpy(float)}
    n = d.astype(int)
    starts = pd.DatetimeIndex(frame["start"]) if "start" in frame.columns else frame.index
    idx = pd.DatetimeIndex(
        np.concatenate(
            [pd.date_range(s, periods=k, freq="D").to_numpy() for s, k in zip(starts, n)]
        )
    )
    return {
        "index": idx,
        "drivers": np.repeat(X, n, axis=0),
        "measured": np.repeat(frame["energy"].to_numpy(float), n),
    }


def fit(frame: pd.DataFrame):
    """The entry's model on a daily frame: change-point + drivers when the frame carries driver
    columns, else the best change-point model (``None`` when none can be fitted). A billing frame
    (:func:`row_days`) is fitted with the bills' days as weights."""
    cols = driver_columns(frame)
    T = frame["oat"].to_numpy(dtype=float)
    y = frame["energy"].to_numpy(dtype=float)
    if not cols:
        from .models import best_model

        return best_model(T, y, time_index=frame.index, weights=row_days(frame))
    from .multivariable import fit_cp_driver_model

    return fit_cp_driver_model(
        T,
        frame[cols].to_numpy(dtype=float),
        y,
        driver_names=[c[len(PREFIX) :] for c in cols],
        time_index=frame.index,
    )


def n_params(model) -> int:
    """The model's parameter count ``p`` (change points included; plus drivers)."""
    if getattr(model, "driver_names", None):
        return int(model.p)
    from .models import N_PARAMS

    return int(N_PARAMS[model.kind])


def metrics(model) -> dict:
    """Driver terms for a Finding's metrics (empty for a change-point model)."""
    names = getattr(model, "driver_names", None)
    if not names:
        return {}
    return {
        "model_form": "cp_driver",
        "drivers": list(names),
        "driver_coef": [round(float(c), 6) for c in model.driver_coef],
    }
