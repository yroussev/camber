#!/usr/bin/env python3
"""Emit the site-neutrality denylist patterns, one POSIX extended regex per line.

CAMBER is vendor- and site-neutral: no committed file may name a real client
building, its BAS site code, or a licence-encumbered third-party dataset.

The patterns are stored base64-encoded on purpose. A guard that spelled the
private site name out in plaintext would re-document in the repository exactly
what it exists to keep out of the repository -- and would surface it to code
search, forks, and scrapers. Encoding keeps the guard fully functional (it
decodes at runtime) while leaving no greppable plaintext occurrence. This is
obfuscation, not secrecy: it is trivially reversible by a maintainer, which is
all that is wanted.

Every pattern is CONTEXTUAL. A bare place name on its own never matches: the
place is a legitimate public weather station and TMY3/EPW climate reference,
and `weather/` content must keep using it freely. What is blocked is the
private-building narrative around it, the quoted BAS site code, and the
licence-encumbered dataset tokens.

Generic ASHRAE / CTI standards citations are deliberately NOT matched -- those
are legitimate engineering references.

A rule may carry ``exempt_paths``: repository paths the TRACKED-FILE scans skip
for that one rule. Only the third-party dataset host and its DOI prefix are
exempt, and only in ``camber/datasets/catalog.json`` -- the catalog must link
the data as published. Commit messages, CHANGELOG.md and the release artefacts
are scanned with every rule and ignore exemptions, and the licence-encumbered
dataset rules are never exempt anywhere.

Output: one rule per line, TAB-separated ``regex<TAB>reason<TAB>exempt_paths``
where ``exempt_paths`` is a comma-separated list (empty when the rule has none).

Usage:  python3 .github/scripts/site_neutrality_patterns.py
"""

from __future__ import annotations

import base64

# The one file the dataset-host rules exempt (see the module docstring).
CATALOG_PATH = "camber/datasets/catalog.json"

# (encoded regex, human-readable reason shown when the pattern fires[, exempt_paths])
_ENCODED: list[tuple] = [
    (
        "ZWxbWzpzcGFjZTpdLl8tXSpjZW50cm9bWzpzcGFjZTpdXSooYnVpbGRpbmd8Y291cnRob3VzZXxiYXNcYnxzaXRlXGIp",
        "private building narrative (place name used as a building/site label)",
    ),
    (
        "c2FtZVtbOnNwYWNlOl1dK2VsW1s6c3BhY2U6XS5fLV0qY2VudHJv",
        "private building narrative ('same <site>' back-reference)",
    ),
    (
        "JVtbOnNwYWNlOl1dKmF0W1s6c3BhY2U6XV0rZWxbWzpzcGFjZTpdLl8tXSpjZW50cm8=",
        "building-specific result metric ('<n>% at <site>')",
    ),
    (
        "KG5hZnxuYXMpW1s6c3BhY2U6XS5fLV0qZWxbWzpzcGFjZTpdLl8tXSpjZW50cm8=",
        "private installation designator",
    ),
    (
        "ZWxbWzpzcGFjZTpdLl8tXSpjZW50cm9bWzpzcGFjZTpdXSsocmVwcm98bW9udGhseVtbOnNwYWNlOl1dK2RhdGF8dHJlbmQp",
        "private BAS/trend data reference",
    ),
    (
        "Y291cnRob3VzZQ==",
        "specific-site building-type label",
    ),
    (
        "IkVMQyI=",
        "private BAS site code (quoted form)",
    ),
    (
        "YmxkZzpFTEM=",
        "private BAS site code (Brick namespace form)",
    ),
    (
        "cnBbLV8gXT8xMDQz",
        "licence-encumbered third-party chiller dataset",
    ),
    (
        "Y29tc3RvY2s=",
        "licence-encumbered third-party dataset",
    ),
    (
        "Zmlnc2hhcmU=",
        "third-party dataset host (licence status unreliable)",
        (CATALOG_PATH,),
    ),
    (
        "MTBcLjYwODQv",
        "third-party dataset DOI prefix",
        (CATALOG_PATH,),
    ),
    (
        "KC9Vc2Vycy9bQS1aYS16MC05Ll8tXSsvfC9ob21lL1tBLVphLXowLTkuXy1dKy98L3ByaXZhdGUvKHRtcHx2YXIpL3xbQS1aYS16XTpcXFVzZXJzXFx8Y2xhdWRlLVswLTldezMsfSk=",
        "absolute local filesystem path (use a repo-relative path)",
    ),
    (
        "c2xvbmE=",
        "local account name",
    ),
]


def rules() -> list[tuple[str, str, tuple[str, ...]]]:
    """Return [(regex, reason, exempt_paths), ...] with the regexes decoded."""
    out = []
    for item in _ENCODED:
        enc, why = item[0], item[1]
        exempt = tuple(item[2]) if len(item) > 2 else ()
        out.append((base64.b64decode(enc).decode("utf-8"), why, exempt))
    return out


def patterns() -> list[tuple[str, str]]:
    """Return [(regex, reason), ...] with the regexes decoded (every rule, exemptions ignored)."""
    return [(regex, why) for regex, why, _exempt in rules()]


def main() -> None:
    for regex, why, exempt in rules():
        # TAB-separated so the shell guard can split reliably; regexes, reasons
        # and paths contain no tabs, and paths contain no commas.
        print(f"{regex}\t{why}\t{','.join(exempt)}")


if __name__ == "__main__":
    main()
