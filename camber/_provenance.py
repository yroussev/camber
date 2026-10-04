"""Report-facing data provenance of a store facility (dataset, licence, citation).

``camber datasets ingest`` records each facility's provenance on its registry entry under the
namespaced ``"dataset"`` key. :func:`facility_provenance` turns that entry into the dict every
report's "Data source & licence" block renders (with the non-commercial / do-not-redistribute
banner for research-only data). :meth:`camber.store.ParquetStore.read_role_frame` also stamps it on
the frame it returns (``frame.attrs[PROVENANCE_ATTR]``), so a report built straight from store
frames (:func:`camber.report.dashboard.build_dashboard`,
:func:`camber.report.site.build_site_report`) carries the banner even when the caller passes no
``data_sources``.
"""

from __future__ import annotations

PROVENANCE_ATTR = "camber_data_sources"

_KEYS = (
    "dataset_id",
    "title",
    "publisher",
    "licence",
    "access",
    "citation",
    "dois",
    "landing_url",
    "attribution_required",
    "redistribution",
    "access_reason",
    "fetched_at",
    "content_hash",
    "known_issues",
)

__all__ = ["PROVENANCE_ATTR", "facility_provenance", "facility_timezone", "frame_sources"]


def facility_provenance(meta: dict, facility_id: str) -> dict:
    """The report-facing provenance of a store facility (empty when it has none recorded)."""
    block = (meta or {}).get("dataset") or {}
    if not isinstance(block, dict):
        return {}
    src = {k: block[k] for k in _KEYS if k in block}
    if not src:
        return {}
    src.setdefault("facility_id", facility_id)
    return src


def _valid_zone(tz) -> str | None:
    """``tz`` as a validated IANA zone, or None when it is not one (blank, prose, unknown)."""
    from .tsparse import check_timezone

    if not isinstance(tz, str) or not tz.strip():
        return None
    try:
        return check_timezone(tz.strip())
    except ValueError:
        return None


def catalog_dataset_timezone(dataset_id) -> str | None:
    """The IANA zone the dataset catalog records for ``dataset_id`` (``ingest.local_timezone``,
    else the entry's ``timezone``), or None for an unknown id or a zone that is not IANA."""
    if not dataset_id:
        return None
    try:
        from .datasets import get as _get_dataset

        entry = _get_dataset(dataset_id)
    except Exception:  # an unknown / retired id, or a catalog that fails to load
        return None
    # 0.93 (#68): only an IANA zone -- an entry's ``timezone`` is often a prose description of
    # its clock (e.g. "naive timestamps on one uniform hourly grid ..."), not a zone
    return _valid_zone((entry.ingest or {}).get("local_timezone") or entry.timezone or None)


def facility_timezone(meta: dict) -> str | None:
    """The site's IANA zone for a facility's registry entry, when the store or registry records one.

    The store holds naive wall-clock time in this zone. One lookup order for every reader -- the
    read API (``/facilities``, the trend viewer's time axis) and config runs on a store source
    (0.100, #104): a ``timezone`` on the registry entry, the dataset-catalog block on the entry
    (``local_timezone`` of a per-site ingest, else ``timezone``), then the catalog entry the
    facility was ingested from, then the zone an open-fdd ingest recorded in its ``"openfdd"``
    provenance (0.99.1, #96). Anything that is not a valid IANA zone is skipped.
    """
    meta = meta or {}
    block = meta.get("dataset")
    if not isinstance(block, dict):
        block = {}
    for tz in (meta.get("timezone"), block.get("local_timezone"), block.get("timezone")):
        if _valid_zone(tz):
            return _valid_zone(tz)
    tz = catalog_dataset_timezone(block.get("dataset_id"))
    if tz:
        return tz
    ofdd = meta.get("openfdd")
    if isinstance(ofdd, dict):
        return _valid_zone(ofdd.get("timezone"))
    return None


def frame_sources(frame) -> list:
    """Provenance dicts stamped on a frame read from a store (``[]`` when there are none)."""
    attrs = getattr(frame, "attrs", None) or {}
    got = attrs.get(PROVENANCE_ATTR) or []
    return [dict(s) for s in got if isinstance(s, dict)]
