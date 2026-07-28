"""Vendor ingest profiles — the documented CSV/timestamp/decimal quirks of common BAS export tools.

There is no public archive of raw per-vendor trend exports, so instead of shipping example files we
encode the **documented format conventions** of each vendor's export tool as a small dataclass, and let
the CSV loaders consume it. A profile sets the delimiter, encoding, header rows to skip, the timestamp
format + day-first flag, and the decimal/thousands separators; the loaders fall back to the shared
auto-detecting parsers (`camber.tsparse`, `camber.coerce`) when a field is left ``None``, so a profile is
a *hint*, never a hard requirement.

These are sensible starting points from public vendor documentation, not a guarantee for every site
config — every field is overridable per call. numpy/pandas + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IngestProfile:
    """The CSV/timestamp/number conventions of one BAS export tool (all fields optional)."""

    name: str = "generic"
    delimiter: str = ","
    encoding: str = "utf-8-sig"        # utf-8-sig transparently strips a UTF-8 BOM
    skiprows: int = 0                  # metadata/preamble rows above the header
    ts_col: str | None = None          # None -> first column
    ts_format: str | None = None       # None -> auto-detect (tsparse try-list)
    dayfirst: bool = False             # European DD/MM ordering
    decimal: str = "."
    thousands: str | None = ","
    status_on: frozenset | None = None    # override the status vocabulary if the site uses odd tokens
    status_off: frozenset | None = None


#: named presets keyed by lowercase vendor/tool id (documented conventions; override per site as needed)
PROFILES: dict = {
    "generic": IngestProfile("generic"),
    # Tridium Niagara N4 history→CSV: BAS-style timestamp, UTF-8, comma.
    "niagara_n4": IngestProfile("niagara_n4", ts_format="%d-%b-%y %I:%M:%S %p"),
    # Johnson Controls Metasys trend export: US month-first 12-hour clock.
    "metasys": IngestProfile("metasys", ts_format="%m/%d/%Y %I:%M:%S %p"),
    # Automated Logic WebCTRL trend export: US month-first, comma.
    "webctrl": IngestProfile("webctrl", ts_format="%m/%d/%Y %H:%M:%S"),
    # Trane Tracer SC/Ensemble export: ISO-ish, comma.
    "tracer": IngestProfile("tracer", ts_format="%Y-%m-%d %H:%M:%S"),
    # Siemens Desigo CC (European locale): semicolon-delimited, decimal comma, day-first dates.
    "desigo": IngestProfile("desigo", delimiter=";", decimal=",", thousands=".",
                            dayfirst=True, ts_format="%d.%m.%Y %H:%M:%S"),
}


def get_profile(profile) -> IngestProfile:
    """Resolve a profile: an :class:`IngestProfile`, a preset name, or None → ``generic``."""
    if profile is None:
        return PROFILES["generic"]
    if isinstance(profile, IngestProfile):
        return profile
    key = str(profile).lower()
    if key not in PROFILES:
        raise ValueError(f"unknown ingest profile {profile!r}; known: {sorted(PROFILES)}")
    return PROFILES[key]
