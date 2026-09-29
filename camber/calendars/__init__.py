"""Holiday calendars for the M&V ``occupied_day`` driver (provisional, 0.93, #68).

A calendar is a set of dates on which a building is not occupied as on an ordinary working day:
public holidays, and optionally school breaks, an academic calendar, or a site's own closures.
Calendars come from three places:

* **bundled public holidays**, keyed by ISO 3166 country code (and subdivision where holidays
  differ by region). Each is a JSON file in this package, generated from its official source by
  ``scripts/calendars_refresh.py`` and citing it (:func:`calendar_info`):

  - ``US`` -- federal holidays, observed dates, 2011-2030 (5 U.S.C. 6103 and E.O. 11582; checked
    against every year the U.S. Office of Personnel Management tabulates);
  - ``NO`` -- Norway's helligdager and 1 and 17 May, 2000-2040, computed from the statutes
    (LOV-1995-02-24-12 § 2, LOV-1947-04-26-1 § 1);
  - ``ES-<community>`` -- Spain's labour holidays per autonomous community (ISO 3166-2:ES, e.g.
    ``ES-CL`` Castilla y León), 2016-2026, from the annual BOE resolutions. Spain needs the
    community: the holidays differ by region;

* **CSV files** a user supplies (:func:`load_calendar_csv`): one date per row (``date``) or a
  range per row (``start``, ``end``, both inclusive), with an optional ``name``. A school's term
  breaks, a university's academic calendar or local holidays go here;

* **registered providers** (:func:`register_calendar`): a callable ``(year, subdivision) ->
  {date: name}`` under a country code, e.g. a wrapper around the ``holidays`` package for a
  country CAMBER does not bundle. Nothing is registered by default and no provider is imported.

:class:`HolidayCalendar` combines them; ``mv[].holiday_calendar`` in a config builds one (see
docs/MANDV.md). A year outside a bundled calendar's coverage is an error, never an empty year.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files as _files

__all__ = [
    "SCHEMA",
    "HolidayCalendar",
    "bundled",
    "calendar_info",
    "public_holidays",
    "load_calendar_csv",
    "register_calendar",
    "registered",
]

SCHEMA = "camber.calendars/1"
_FILES = {"US": "us_federal.json", "NO": "no_public.json", "ES": "es_public.json"}
_PROVIDERS: dict = {}


def bundled() -> tuple:
    """The country codes with a bundled public-holiday calendar."""
    return tuple(sorted(_FILES))


def registered() -> tuple:
    """The country codes of registered providers (:func:`register_calendar`)."""
    return tuple(sorted(_PROVIDERS))


def register_calendar(country: str, provider: Callable | None) -> None:
    """Register ``provider(year, subdivision) -> {date or ISO string: name}`` for ``country``.

    A registered provider takes precedence over a bundled calendar for that code; ``None``
    removes the registration. For example, with the third-party ``holidays`` package::

        import holidays
        register_calendar("DE", lambda y, sub: holidays.country_holidays("DE", subdiv=sub, years=y))
    """
    code = _code(country)
    if provider is None:
        _PROVIDERS.pop(code, None)
    elif not callable(provider):
        raise TypeError("a calendar provider is a callable (year, subdivision) -> {date: name}")
    else:
        _PROVIDERS[code] = provider


def _code(country) -> str:
    if not isinstance(country, str) or not country.strip():
        raise ValueError(f"a calendar country is an ISO 3166 code such as 'NO', got {country!r}")
    return country.strip().upper()


def _split(country: str, subdivision: str | None) -> tuple:
    """``("ES", "CL")`` from ``"ES-CL"`` or ``("ES", "CL")``; codes upper-cased."""
    code = _code(country)
    if "-" in code:
        code, sub = code.split("-", 1)
        if subdivision is not None and subdivision.strip().upper() != sub:
            raise ValueError(f"calendar {country!r} and subdivision {subdivision!r} disagree")
        subdivision = sub
    return code, (subdivision.strip().upper() if subdivision else None)


@cache
def _load(code: str) -> dict:
    doc = json.loads(_files(__name__).joinpath(_FILES[code]).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{_FILES[code]}: schema {doc.get('schema')!r}, expected {SCHEMA!r}")
    return doc


def calendar_info(country: str) -> dict:
    """A bundled calendar's metadata: title, coverage, subdivisions, sources, terms, note."""
    code, _sub = _split(country, None)
    if code not in _FILES:
        raise ValueError(f"no bundled calendar for {code!r}; bundled: {', '.join(bundled())}")
    return {k: v for k, v in _load(code).items() if k != "dates"}


def public_holidays(country: str, years, *, subdivision: str | None = None) -> dict:
    """``{datetime.date: name}`` of ``country``'s public holidays in ``years`` (an int or an
    iterable of ints).

    ``country`` is an ISO 3166 code, optionally with its subdivision (``"ES-CL"``). A registered
    provider serves its code first, then the bundled calendars. Raises ``ValueError`` on an
    unknown code, a subdivision the calendar needs but was not given (or does not know), or a year
    outside its coverage.
    """
    code, sub = _split(country, subdivision)
    ys = sorted({int(years)} if isinstance(years, int) else {int(y) for y in years})
    if code in _PROVIDERS:
        out: dict = {}
        for y in ys:
            for d, n in dict(_PROVIDERS[code](y, sub)).items():
                out[_as_date(d)] = str(n)
        return out
    if code not in _FILES:
        raise ValueError(
            f"no holiday calendar for {code!r}: bundled are {', '.join(bundled())}; supply a CSV "
            "file (holiday_calendar.files) or register a provider (camber.calendars."
            "register_calendar)"
        )
    doc = _load(code)
    lo, hi = doc["coverage"]
    outside = [y for y in ys if not lo <= y <= hi]
    if outside:
        raise ValueError(
            f"the bundled {code} calendar covers {lo}-{hi}; no dates for {outside}. Supply them "
            "in a CSV file (holiday_calendar.files)"
        )
    subs = doc.get("subdivisions") or {}
    if sub is None:
        if doc.get("requires_subdivision"):
            raise ValueError(
                f"{code} holidays differ by region: name the subdivision, e.g. "
                f"'{code}-{next(iter(subs))}' ({', '.join(sorted(subs))})"
            )
        key = ""
    else:
        if sub not in subs:
            known = ", ".join(sorted(subs)) if subs else "none (a national calendar)"
            raise ValueError(f"{code} calendar has no subdivision {sub!r}; known: {known}")
        key = sub
    want = {str(y) for y in ys}
    return {_dt.date.fromisoformat(d): n for d, n in doc["dates"][key].items() if d[:4] in want}


def _as_date(x) -> _dt.date:
    if isinstance(x, _dt.datetime):
        return x.date()
    if isinstance(x, _dt.date):
        return x
    import pandas as pd

    return pd.Timestamp(x).date()


def load_calendar_csv(path: str) -> dict:
    """``{datetime.date: name}`` from a calendar CSV file.

    Columns: ``date`` (one day per row), or ``start`` and ``end`` (an inclusive range per row);
    a file may mix the two. ``name`` (optional) labels the day, else the file name does. Blank
    lines and rows whose first cell starts with ``#`` are skipped. Raises ``ValueError`` on a
    missing column, an unreadable date or an end before its start.
    """
    import os

    import pandas as pd

    df = pd.read_csv(path, dtype=str, comment="#", skip_blank_lines=True, encoding="utf-8-sig")
    df.columns = [str(c).strip().lower() for c in df.columns]
    has_date, has_range = "date" in df.columns, {"start", "end"} <= set(df.columns)
    if not (has_date or has_range):
        raise ValueError(
            f"calendar file {os.path.basename(path)!r} needs a 'date' column or 'start' and "
            f"'end' columns; it has {list(df.columns)}"
        )
    label = os.path.splitext(os.path.basename(path))[0]
    out: dict = {}
    for i, row in df.iterrows():
        nm = row.get("name")
        name = (nm.strip() if isinstance(nm, str) else "") or label
        try:
            if has_date and isinstance(row.get("date"), str) and row["date"].strip():
                out[pd.Timestamp(row["date"].strip()).date()] = name
                continue
            a, b = pd.Timestamp(str(row["start"]).strip()), pd.Timestamp(str(row["end"]).strip())
        except (ValueError, TypeError, KeyError):
            raise ValueError(f"calendar file {label!r}, row {i + 2}: unreadable date") from None
        if b < a:
            raise ValueError(
                f"calendar file {label!r}, row {i + 2}: end {b.date()} is before start"
            )
        for d in pd.date_range(a.normalize(), b.normalize(), freq="D"):
            out[d.date()] = name
    return out


@dataclass(frozen=True)
class HolidayCalendar:
    """Public holidays of ``country`` (optional), plus the days in ``files`` and ``dates``.

    ``country`` is an ISO 3166 code or code-subdivision (``"NO"``, ``"US"``, ``"ES-CL"``);
    ``files`` are calendar CSV paths (:func:`load_calendar_csv`); ``dates`` are extra days.
    """

    country: str | None = None
    subdivision: str | None = None
    files: tuple = ()
    dates: tuple = ()
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_spec(cls, spec, *, base_dir: str = ".") -> HolidayCalendar:
        """A calendar from a config value: ``"NO"`` / ``"ES-CL"``, or ``{"country", "subdivision",
        "files": [...], "dates": [...]}``. Relative ``files`` resolve against ``base_dir``; each
        must exist. The country (and subdivision) is checked against the bundled calendars and
        registered providers."""
        import os

        if isinstance(spec, str):
            spec = {"country": spec}
        if not isinstance(spec, Mapping):
            raise ValueError(
                'holiday_calendar must be a country code such as "NO" or {"country", '
                '"subdivision", "files", "dates"}'
            )
        extra = set(spec) - {"country", "subdivision", "files", "dates"}
        if extra:
            raise ValueError(f"holiday_calendar: unknown key(s) {sorted(extra)}")
        files = spec.get("files") or []
        if isinstance(files, str):
            files = [files]
        paths = []
        for f in files:
            p = f if os.path.isabs(f) else os.path.join(base_dir, f)
            if not os.path.isfile(p):
                raise ValueError(f"holiday_calendar.files: no such file {f!r}")
            paths.append(p)
        days = []
        for d in spec.get("dates") or []:
            try:
                days.append(_as_date(d))
            except (ValueError, TypeError):
                raise ValueError(f"holiday_calendar.dates: {d!r} is not a date") from None
        country, sub = spec.get("country"), spec.get("subdivision")
        if country is None and sub is not None:
            raise ValueError("holiday_calendar.subdivision needs a country")
        if country is None and not paths and not days:
            raise ValueError("holiday_calendar needs a country, files or dates")
        cal = cls(
            country=None if country is None else _split(country, sub)[0],
            subdivision=None if country is None else _split(country, sub)[1],
            files=tuple(paths),
            dates=tuple(days),
        )
        cal._check()
        return cal

    def _check(self) -> None:
        if self.country is None or self.country in _PROVIDERS:
            return
        info = calendar_info(self.country)  # an unknown country raises here
        subs = info.get("subdivisions") or {}
        if self.subdivision is None and info.get("requires_subdivision"):
            public_holidays(self.country, info["coverage"][0])  # raises, naming the codes
        if self.subdivision is not None and self.subdivision not in subs:
            public_holidays(self.country, info["coverage"][0], subdivision=self.subdivision)

    def coverage(self) -> tuple | None:
        """``(first, last)`` year of the bundled public holidays, or ``None`` (no country, or a
        registered provider: every year)."""
        if self.country is None or self.country in _PROVIDERS:
            return None
        lo, hi = calendar_info(self.country)["coverage"]
        return int(lo), int(hi)

    def days(self, start, end) -> dict:
        """``{datetime.date: name}`` of every calendar day from ``start`` to ``end`` (inclusive).

        Raises ``ValueError`` when a year in the range is outside the public holidays' coverage
        (:meth:`coverage`)."""
        a, b = _as_date(start), _as_date(end)
        out: dict = {}
        if self.country is not None:
            years = range(a.year, b.year + 1)
            out.update(public_holidays(self.country, years, subdivision=self.subdivision))
        for p in self.files:
            if p not in self._cache:
                self._cache[p] = load_calendar_csv(p)
            out.update(self._cache[p])
        out.update({d: "holiday" for d in self.dates})
        return {d: n for d, n in sorted(out.items()) if a <= d <= b}

    def label(self) -> str:
        """A short description (``"NO public holidays + 1 file"``) for findings and reports."""
        parts = []
        if self.country:
            sub = f"-{self.subdivision}" if self.subdivision else ""
            parts.append(f"{self.country}{sub} public holidays")
        if self.files:
            parts.append(f"{len(self.files)} calendar file{'s' if len(self.files) > 1 else ''}")
        if self.dates:
            parts.append(f"{len(self.dates)} listed date{'s' if len(self.dates) > 1 else ''}")
        return " + ".join(parts)
