"""The README is also PyPI's project page, where relative links and images do not resolve (#138)."""

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_links_and_images_are_absolute():
    text = README.read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    assert targets, "no links found; the pattern is stale"
    relative = [t for t in targets if not re.match(r"^(https?:|mailto:|#)", t)]
    assert not relative, f"relative README targets break on PyPI: {relative}"


def test_readme_images_point_at_files_in_the_repo():
    root = README.parent
    text = README.read_text(encoding="utf-8")
    raw = "https://raw.githubusercontent.com/yroussev/camber/main/"
    for url in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
        if url.startswith(raw):
            assert (root / url[len(raw) :]).is_file(), url
