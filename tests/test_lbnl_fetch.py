"""Tests for the LBNL fetch helper's robustness (examples/lbnl_fdd/fetch.py).

The fault catalog is broadened over time, so a member that isn't in a given zip release must be
skipped (not crash), and its absence must not defeat the 'already fetched' no-op. No network: we
build a synthetic zip and point the module's DATA dir at a tmp path.
"""

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "lbnl_fdd"
    ),
)

import fetch  # noqa: E402


def _make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for m in members:
            z.writestr(m, "Datetime,x\n2024-01-01,1\n")


def test_fetch_skips_absent_members_and_extracts_present(tmp_path, monkeypatch, capsys):
    data = tmp_path / "lbnl"
    monkeypatch.setattr(fetch, "DATA", str(data))
    os.makedirs(data, exist_ok=True)
    # a zip with the REQUIRED core present but the optional leak-severity variants ABSENT
    _make_zip(str(data / "LBNL_SDAHU.zip"), fetch.REQUIRED)

    fetch._fetch_set(
        "sdahu", "http://unused", "0", fetch.MEMBERS, "LBNL_SDAHU.zip", required=fetch.REQUIRED
    )

    out = data / "sdahu"
    for m in fetch.REQUIRED:
        assert (out / os.path.basename(m)).exists()  # every core member extracted
    assert not (out / "coi_leakage_010_annual.csv").exists()  # absent optional skipped, no crash
    assert "skipped, not in zip" in capsys.readouterr().out


def test_fetch_is_noop_when_required_present(tmp_path, monkeypatch, capsys):
    data = tmp_path / "lbnl"
    monkeypatch.setattr(fetch, "DATA", str(data))
    out = data / "sdahu"
    os.makedirs(out, exist_ok=True)
    for m in fetch.REQUIRED:  # pre-place the core files
        (out / os.path.basename(m)).write_text("x")
    # no zip present; a naive all-members check would try to re-download the missing optionals
    fetch._fetch_set(
        "sdahu", "http://unused", "0", fetch.MEMBERS, "LBNL_SDAHU.zip", required=fetch.REQUIRED
    )
    assert "already present" in capsys.readouterr().out  # no-op, no download attempt


def test_required_is_a_subset_of_members():
    assert set(fetch.REQUIRED) <= set(fetch.MEMBERS)
    assert "LBNL_FDD_Dataset_SDAHU/AHU_annual.csv" in fetch.REQUIRED  # baseline always required
