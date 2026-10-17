"""The counts the docs state are computed from the code (#220), so a new role cannot stale them."""

from pathlib import Path

from camber.interop.export import ROLE_TO_BRICK_PART, ROLE_TO_BRICK_POINT_CLASS
from camber.interop.semantic223 import _NO_223_QUANTITY, role_223_quantity
from camber.model.roles import Role

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = (ROOT / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")


def test_brick_export_count():
    exportable = set(ROLE_TO_BRICK_POINT_CLASS) | set(ROLE_TO_BRICK_PART)
    assert f"**{len(exportable)} of {len(Role)} roles**" in ONTOLOGY


def test_223_quantity_count():
    mapped = [r for r in Role if role_223_quantity(r) is not None]
    assert f"today **{len(mapped)} of {len(Role)} roles** map" in ONTOLOGY
    assert f"remaining {len(_NO_223_QUANTITY)} roles" in ONTOLOGY
