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


def _decode_ts(val):
    """Decode a Haystack timestamp scalar to a Timestamp.

    Handles the v3 JSON string form (``t:2024-... Tz``), a plain ISO string, and
    the Hayson v4 nested form (``{"_kind": "dateTime", "val": "..."}``).
    """
    if isinstance(val, dict):                # Hayson: {"_kind":"dateTime","val":...}
        val = val.get("val", "")
    if isinstance(val, str) and val.startswith("t:"):
        val = val[2:]
    # drop a trailing " Timezone_Name" the JSON encoding appends after the offset
    if isinstance(val, str) and " " in val:
        val = val.split(" ", 1)[0]
    return pd.Timestamp(val)


def _decode_num(val):
    """Decode a Haystack number scalar (v3 ``n:`` string, Hayson dict, or plain)."""
    if isinstance(val, dict):                # Hayson: {"_kind":"number","val":..,"unit":..}
        val = val.get("val")
    if isinstance(val, str):
        if val.startswith("n:"):
            val = val[2:]
        val = val.strip().split(" ", 1)[0]   # strip a trailing unit token
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


def parse_his_grid(grid: dict, name: str = "val") -> pd.Series:
    """Parse a Haystack JSON ``hisRead`` grid into a numeric Series.

    Expects ``grid['rows']`` as a list of ``{"ts": <t-scalar>, "val": <n-scalar>}``
    dicts (the ``hisRead`` response shape). Tolerates a plain ISO/number too.
    """
    rows = (grid or {}).get("rows", [])
    idx, vals = [], []
    for r in rows:
        if "ts" not in r:
            continue
        idx.append(_decode_ts(r["ts"]))
        vals.append(_decode_num(r.get("val")))
    s = pd.Series(vals, index=pd.DatetimeIndex(idx), name=name, dtype="float64")
    return s.sort_index()


class HaystackAdapter:
    """SourceAdapter over a Project Haystack server (hisRead)."""

    def __init__(self, base_url: str, *, auth_token: str | None = None,
                 point_refs: dict | None = None, transport=None,
                 range_str: str = "today"):
        self.base_url = base_url
        self.auth_token = auth_token
        # point_refs: {point_name -> Haystack Ref id}; supplied by a site config
        self.point_refs = point_refs or {}
        # transport(op, params) -> grid dict; None until a live client is wired
        self.transport = transport
        self.range_str = range_str

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
            s = parse_his_grid(grid, name=name)
            if not s.empty:
                cols[name] = s
        if not cols:
            return pd.DataFrame()
        df = pd.concat(cols, axis=1)
        if resample:
            df = df.resample(resample).mean(numeric_only=True)
        return df

    def units(self) -> dict:
        """Per-point units; empty (units come from the hisRead grid instead)."""
        return {}


# --------------------------------------------------------------------------- #
# Transports — pluggable backends for HaystackAdapter(transport=...)
#
# A transport is ``transport(op, params) -> grid_dict``. Two are provided: a
# dependency-free stdlib JSON client that works against the standard Haystack HTTP
# JSON API, and a generic wrapper that adapts any third-party client (phable,
# pyhaystack, ...) by its hisRead function. Pick whichever fits your server.
# --------------------------------------------------------------------------- #

def http_json_transport(base_url: str, *, token: str | None = None,
                        timeout: float = 30.0):
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

        # pip install camber[haystack]
        adapter = HaystackAdapter(url, point_refs=refs,
                                  transport=client_transport(my_client.his_read))
    """
    def transport(op: str, params: dict) -> dict:
        return his_read(params["id"], params.get("range"))

    return transport
