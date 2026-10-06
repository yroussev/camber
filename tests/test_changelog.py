"""The changelog stays renderable on GitHub and tidy.

GitHub stops rendering a Markdown file somewhere between 210 KB and 244 KB, so ``CHANGELOG.md``
and each ``docs/changelog/`` archive must stay under 200 KB (RELEASING.md: archive the oldest
versions once ``CHANGELOG.md`` passes 150 KB). Each version section has one heading per type, and
a released section carries no HTML comments (only a section still marked Unreleased may keep the
per-branch markers). Every archive is linked from ``CHANGELOG.md``, the changelog index page and
the mkdocs nav, and no version appears in two files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CURRENT = ROOT / "CHANGELOG.md"
ARCHIVE_DIR = ROOT / "docs" / "changelog"
ARCHIVES = sorted(ARCHIVE_DIR.glob("changelog-*.md"))
MAX_BYTES = 200_000
_VERSION = re.compile(r"^## \[([^\]]+)\]")


def _sections(path: Path) -> list[tuple[str, list[str]]]:
    """(``## [...]`` heading, its lines) for each version section; a section ends at any ``## ``."""
    out: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = [] if _VERSION.match(line) else None
            if current is not None:
                out.append((line, current))
        elif current is not None:
            current.append(line)
    return out


def _all_files() -> list[Path]:
    return [CURRENT, *ARCHIVES]


def test_there_is_an_archive():
    assert ARCHIVES, "docs/changelog/ has no changelog-*.md archive"


@pytest.mark.parametrize("path", _all_files(), ids=lambda p: p.name)
def test_size_under_the_github_render_limit(path: Path):
    size = path.stat().st_size
    assert size <= MAX_BYTES, (
        f"{path.name} is {size} bytes (> {MAX_BYTES}); GitHub stops rendering Markdown files "
        "above about 210 KB. Move the oldest versions into a docs/changelog/ archive "
        "(RELEASING.md)."
    )


@pytest.mark.parametrize("path", _all_files(), ids=lambda p: p.name)
def test_one_heading_per_type_and_no_comments_in_released_sections(path: Path):
    problems = []
    for head, lines in _sections(path):
        seen: set[str] = set()
        for line in lines:
            if line.startswith("### "):
                kind = re.split(r" (?:—|--) ", line[4:].strip())[0]
                if kind in seen:
                    problems.append(f"{head}: repeated heading {line!r}")
                seen.add(kind)
        if "unreleased" not in head.lower() and any(
            "<!--" in line or "-->" in line for line in lines
        ):
            problems.append(f"{head}: HTML comment in a released section")
    assert not problems, "\n".join(problems)


def test_no_version_in_two_files():
    where: dict[str, str] = {}
    dupes = []
    for path in _all_files():
        for head, _ in _sections(path):
            v = _VERSION.match(head).group(1)  # type: ignore[union-attr]
            if v in where:
                dupes.append(f"{v} in {where[v]} and {path.name}")
            where[v] = path.name
    assert not dupes, dupes


def test_every_archive_is_linked():
    current = CURRENT.read_text(encoding="utf-8")
    index = (ARCHIVE_DIR / "index.md").read_text(encoding="utf-8")
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for path in ARCHIVES:
        assert f"](docs/changelog/{path.name})" in current, f"CHANGELOG.md lacks {path.name}"
        assert f"]({path.name})" in index, f"docs/changelog/index.md does not link {path.name}"
        assert f"changelog/{path.name}" in nav, f"mkdocs.yml nav does not list {path.name}"
