"""Haystack ``hisRead`` adapter: a real client behind an injectable transport.

Project Haystack defines an HTTP API where ``hisRead`` returns a point's
historized time-series as a grid. The client logic here is real -- it issues a
``hisRead`` per point, parses the returned grid, and assembles a wide frame on a
common time grid -- with the one network-bound piece (the actual HTTP call)
factored into a ``transport`` callable. Inject a live transport to talk to a
server; inject a canned one to test the parse/assemble path offline.

Without a transport the adapter raises on read, documenting that no HTTP client is
bundled (keeping the package dependency-light) and that a live server is required.

Grid value encoding: Haystack JSON tags scalars with type prefixes -- ``t:`` for
timestamps (``t:2024-01-01T00:00:00-08:00 Los_Angeles``) and ``n:`` for numbers
with an optional unit (``n:23.5 °C``). :func:`parse_his_grid` decodes those into a
numeric, datetime-indexed Series.
"""

from __future__ import annotations

import pandas as pd


def _ts_parts(val):
    """``(ISO text or datetime, Haystack tz name or None)`` of one Haystack timestamp scalar.

    Handles the v3 JSON string form (``t:2024-... Tz``), a plain ISO string, a Python
    ``datetime``, the Hayson v4 nested form (``{"_kind": "dateTime", "val": "...", "tz": ...}``),
    and a typed client object exposing ``.val`` (e.g. a phable/pyhaystack DateTime kind).
    """
    tz = None
    if isinstance(val, dict):  # Hayson: {"_kind":"dateTime","val":...,"tz":...}
        tz = val.get("tz")
        val = val.get("val", "")
    elif not isinstance(val, str) and hasattr(val, "val"):  # typed client kind -> unwrap
        tz = getattr(val, "tz", None)
        val = val.val
    if isinstance(val, str) and val.startswith("t:"):
        val = val[2:]
    # a trailing " Timezone_Name" the JSON encoding appends after the offset
    if isinstance(val, str) and " " in val.strip():
        val, tz = val.strip().split(" ", 1)
    if hasattr(val, "isoformat"):  # a datetime: its tz is in its offset
        tzinfo = getattr(val, "tzinfo", None)
        key = getattr(tzinfo, "key", None) or getattr(tzinfo, "zone", None)
        tz = tz or key
        val = val.isoformat()
    return val, (str(tz).strip() if tz else None)


def _decode_ts(val):
    """Decode a Haystack timestamp scalar to a Timestamp (as written, offset kept)."""
    return pd.Timestamp(_ts_parts(val)[0])


def haystack_tz(name) -> str | None:
    """The IANA zone of a Haystack timezone name (``New_York`` -> ``America/New_York``), or
    ``None`` when there is none (0.108, #244).

    Haystack names a zone by the city part of its IANA name; ``UTC`` and full IANA names pass
    through. A name that matches no zone gives ``None``."""
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    try:
        from zoneinfo import available_timezones
    except ImportError:  # pragma: no cover - Python < 3.9
        return None
    zones = _zones(available_timezones)
    if name in zones:
        return name
    if name.upper() in ("UTC", "GMT", "REL"):
        return "UTC"
    hits = sorted(z for z in zones if z.rsplit("/", 1)[-1] == name)
    if not hits:
        return None
    for prefix in ("America/", "Europe/", "Asia/", "Australia/", "Africa/", "Pacific/"):
        for z in hits:
            if z.startswith(prefix):
                return z
    return hits[0]


_ZONES: set = set()


def _zones(available) -> set:
    if not _ZONES:
        _ZONES.update(available())
    return _ZONES


def _decode_unit(val) -> str:
    """The unit of a Haystack number scalar (``n:23.5 °C``, Hayson ``unit``), or ``""``."""
    if isinstance(val, dict):
        return str(val.get("unit") or "")
    if isinstance(val, str):
        body = val[2:] if val.startswith("n:") else val
        parts = body.strip().split(" ", 1)
        return parts[1].strip() if len(parts) == 2 else ""
    unit = getattr(val, "unit", None)
    return str(unit) if unit else ""


def _decode_num(val):
    """Decode a Haystack number scalar (v3 ``n:`` string, Hayson dict, or plain)."""
    if isinstance(val, dict):  # Hayson: {"_kind":"number","val":..,"unit":..}
        val = val.get("val")
    elif not isinstance(val, (str, int, float)) and hasattr(val, "val"):
        val = val.val  # typed client kind (e.g. phable Number) -> unwrap
    if isinstance(val, str):
        if val.startswith("n:"):
            val = val[2:]
        val = val.strip().split(" ", 1)[0]  # strip a trailing unit token
        if val in ("INF", "+INF"):
            return float("inf")
        if val == "-INF":
            return float("-inf")
        if val in ("NaN", "", "null"):
            return float("nan")
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def parse_his_grid(
    grid,
    name: str = "val",
    *,
    timezone: str | None = None,
    strict_timezone: bool = False,
) -> pd.Series:
    """Parse a Haystack ``hisRead`` grid into a numeric Series.

    ``grid`` may be a JSON dict with ``grid['rows']``, or a typed client **Grid object**
    exposing ``.rows`` (e.g. phable / pyhaystack). Each row is ``{"ts": <t>, "val": <n>}`` where
    the scalars may be v3 strings, Hayson nested dicts, Python ``datetime``/numbers, or typed
    client kinds — :func:`_decode_ts`/:func:`_decode_num` normalize all of these.

    0.108 (#244): timestamps go through :func:`camber.tsparse.parse_timestamps`, so the index is
    the site's wall clock (tz-naive) like every other adapter's, and a range across a DST change
    parses. ``timezone`` is the site's IANA zone; without it the Haystack tz name on the
    timestamps (``New_York``; see :func:`haystack_tz`) is used, and without either the clock is
    kept as written (a :class:`~camber.tsparse.TimezoneWarning`; ``strict_timezone`` raises).
    The unit of the numbers is in ``Series.attrs["unit"]`` (``""`` when none).
    """
    from ..tsparse import parse_timestamps

    if grid is None:
        rows = []
    elif isinstance(grid, dict):
        rows = grid.get("rows", [])
    else:
        rows = getattr(grid, "rows", [])  # typed client Grid object
    raw, vals, units, tz_names = [], [], [], []
    for r in rows:
        if "ts" not in r:
            continue
        text, tzn = _ts_parts(r["ts"])
        raw.append(text)
        if tzn:
            tz_names.append(tzn)
        v = r.get("val")
        vals.append(_decode_num(v))
        u = _decode_unit(v)
        if u:
            units.append(u)
    if timezone is None and tz_names:
        timezone = haystack_tz(tz_names[0])
    if raw:
        idx = parse_timestamps(raw, timezone=timezone, strict_timezone=strict_timezone)
    else:
        idx = pd.DatetimeIndex([])
    s = pd.Series(vals, index=idx, name=name, dtype="float64")
    s = s[s.index.notna()].sort_index()
    unit = ""
    if units:
        unit = max(set(units), key=units.count)
        if len(set(units)) > 1:
            import warnings

            warnings.warn(
                f"Haystack point {name!r} mixes units {sorted(set(units))}; keeping {unit!r}",
                UserWarning,
                stacklevel=2,
            )
    s.attrs["unit"] = unit
    return s


class HaystackAdapter:
    """SourceAdapter over a Project Haystack server (hisRead)."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        point_refs: dict | None = None,
        transport=None,
        range_str: str = "today",
        timezone: str | None = None,
        strict_timezone: bool = False,
    ):
        """``timezone``: the site's IANA zone (0.108, #244; else the Haystack tz name on the
        timestamps); see :func:`parse_his_grid`."""
        self.base_url = base_url
        self.auth_token = auth_token
        # point_refs: {point_name -> Haystack Ref id}; supplied by a site config
        self.point_refs = point_refs or {}
        # transport(op, params) -> grid dict; None until a live client is wired
        self.transport = transport
        self.range_str = range_str
        self.timezone, self.strict_timezone = timezone, strict_timezone
        self._units: dict = {}

    def point_names(self):
        """Sorted point names from the configured point->Ref map."""
        return sorted(self.point_refs)

    def _his_read(self, ref: str, range_str: str) -> dict:
        if self.transport is None:
            raise NotImplementedError(
                "HaystackAdapter has no transport: inject a callable "
                "transport(op, params) -> grid to reach a live server, or a "
                "canned one to test offline. No HTTP client is bundled."
            )
        return self.transport("hisRead", {"id": ref, "range": range_str})

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        """hisRead each named point, parse, and assemble a wide frame."""
        cols = {}
        for name in names:
            ref = self.point_refs.get(name)
            if ref is None:
                continue
            grid = self._his_read(ref, self.range_str)
            s = parse_his_grid(
                grid, name=name, timezone=self.timezone, strict_timezone=self.strict_timezone
            )
            if s.attrs.get("unit"):
                self._units[name] = s.attrs["unit"]
            if not s.empty:
                cols[name] = s
        if not cols:
            return pd.DataFrame()
        df = pd.concat(cols, axis=1)
        if resample:
            df = df.resample(resample).mean(numeric_only=True)
        return df

    def units(self) -> dict:
        """Per-point units, as the hisRead grids gave them (0.108, #244; filled by
        :meth:`load_points`)."""
        return dict(self._units)


# --------------------------------------------------------------------------- #
# Transports — pluggable backends for HaystackAdapter(transport=...)
#
# A transport is ``transport(op, params) -> grid_dict``. Two are provided: a
# dependency-free stdlib JSON client that works against the standard Haystack HTTP
# JSON API, and a generic wrapper that adapts any third-party client (phable,
# pyhaystack, ...) by its hisRead function. Pick whichever fits your server.
# --------------------------------------------------------------------------- #


def http_json_transport(base_url: str, *, token: str | None = None, timeout: float = 30.0):
    """A stdlib (urllib) transport for a Haystack server's HTTP JSON API.

    Issues ``GET <base_url>/<op>?id=<id>&range=<range>`` with
    ``Accept: application/json`` (and a Bearer ``token`` if given) and returns the
    parsed JSON grid. No third-party dependency. The response is consumed by
    :func:`parse_his_grid`, which handles both the v3 string-scalar and Hayson
    nested-dict encodings.

    Note: real Haystack auth (SCRAM) and Zinc negotiation vary by server; for those
    use a maintained client (see :func:`client_transport` and the ``[haystack]``
    extra). This covers token/JSON-capable servers without a dependency.
    """
    import json as _json
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    base = base_url.rstrip("/")

    def transport(op: str, params: dict) -> dict:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url = f"{base}/{op}?{qs}" if qs else f"{base}/{op}"
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urlopen(Request(url, headers=headers), timeout=timeout) as resp:  # noqa: S310
            return _json.loads(resp.read().decode("utf-8"))

    return transport


def client_transport(his_read):
    """Adapt any client's hisRead into a transport for :class:`HaystackAdapter`.

    ``his_read`` is a callable ``his_read(point_id, range_str) -> grid`` from a
    maintained Haystack client (e.g. phable or pyhaystack). The returned grid only
    needs ``parse_his_grid``-compatible rows. This is the one-line seam for plugging
    a third-party client in without CAMBER depending on it directly::

        # pip install camber-toolkit[haystack]
        adapter = HaystackAdapter(url, point_refs=refs,
                                  transport=client_transport(my_client.his_read))
    """

    def transport(op: str, params: dict) -> dict:
        return his_read(params["id"], params.get("range"))

    return transport


def phable_transport(client, *, his_read_attr: str = "his_read"):
    """A ready-made transport for a `phable <https://github.com/rick-jennings/phable>`_ client.

    phable is the modern, dependency-light Haystack client (the ``[haystack]`` extra on
    Python >= 3.11). Its ``Client.his_read(point_ref, range)`` returns a phable ``Grid`` whose
    rows carry typed values (a ``datetime`` ts, a ``Number`` val); :func:`parse_his_grid` now
    consumes those natively, so this is just the one-line wiring::

        from phable import Client, Ref
        client = Client(uri, user, password); client.open()
        refs = {"AHU1_SAT": Ref("p:demo:r:ahu1.sat")}
        adapter = HaystackAdapter(uri, point_refs=refs,
                                  transport=phable_transport(client))

    ``point_refs`` should hold phable ``Ref`` objects (passed through unchanged). For pyhaystack
    or any other client, use :func:`client_transport` with the client's hisRead callable.
    """
    his_read = getattr(client, his_read_attr)

    def transport(op: str, params: dict):
        return his_read(params["id"], params.get("range"))

    return transport
