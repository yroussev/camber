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

__all__ = ["PROVENANCE_ATTR", "facility_provenance", "frame_sources"]


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


def frame_sources(frame) -> list:
    """Provenance dicts stamped on a frame read from a store (``[]`` when there are none)."""
    attrs = getattr(frame, "attrs", None) or {}
    got = attrs.get(PROVENANCE_ATTR) or []
    return [dict(s) for s in got if isinstance(s, dict)]
