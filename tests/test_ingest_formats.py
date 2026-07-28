"""Per-vendor format corpus: the SAME data written in each vendor's export format must normalize to
the SAME frame. Since no public per-vendor archive exists, we synthesize each documented format and
assert equivalence — the ingest-robustness proof.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.io import load_csv  # noqa: E402

# canonical data: 3 hourly instants, two point columns
_IDX = pd.to_datetime(["2024-07-01 00:00:00", "2024-07-01 01:00:00", "2024-07-01 02:00:00"])
_POWER = [1234.5, 2000.0, 1500.25]
_TEMP = [55.0, 56.0, 57.0]


def _reference():
    return pd.DataFrame({"power": _POWER, "temp": _TEMP}, index=_IDX)


# (profile, encoding, per-row timestamp strings, delimiter, value-formatter) for each vendor
def _rows_generic():
    return [t.strftime("%Y-%m-%d %H:%M:%S") for t in _IDX]


def _rows_niagara():
    return [t.strftime("%d-%b-%y %I:%M:%S %p") + " PDT" for t in _IDX]


def _rows_metasys():
    return [t.strftime("%m/%d/%Y %I:%M:%S %p") for t in _IDX]


def _rows_webctrl():
    return [t.strftime("%m/%d/%Y %H:%M:%S") for t in _IDX]


def _rows_desigo():
    return [t.strftime("%d.%m.%Y %H:%M:%S") for t in _IDX]


_VENDORS = {
    "generic": (None, "utf-8", _rows_generic, ",", lambda v: f"{v}"),
    "niagara_n4": ("niagara_n4", "utf-8", _rows_niagara, ",", lambda v: f"{v}"),
    "metasys": ("metasys", "utf-8", _rows_metasys, ",", lambda v: f"{v}"),
    "webctrl": ("webctrl", "utf-8", _rows_webctrl, ",", lambda v: f"{v}"),
    "desigo": (
        "desigo",
        "utf-8",
        _rows_desigo,
        ";",
        lambda v: f"{v}".replace(".", ","),
    ),  # decimal comma
}


def _write(tmp_path, vendor):
    profile, enc, rowfn, delim, vfmt = _VENDORS[vendor]
    tstrs = rowfn()
    lines = [delim.join(["timestamp", "power", "temp"])]
    for t, p, tp in zip(tstrs, _POWER, _TEMP):
        lines.append(delim.join([t, vfmt(p), vfmt(tp)]))
    path = tmp_path / f"{vendor}.csv"
    path.write_text("\n".join(lines) + "\n", encoding=enc)
    return str(path), profile


@pytest.mark.parametrize("vendor", list(_VENDORS))
def test_each_vendor_format_normalizes_to_reference(tmp_path, vendor):
    path, profile = _write(tmp_path, vendor)
    df = load_csv(path, profile=profile)
    pd.testing.assert_frame_equal(
        df[["power", "temp"]], _reference(), check_freq=False, check_names=False
    )


def test_all_vendor_variants_agree(tmp_path):
    frames = []
    for vendor in _VENDORS:
        path, profile = _write(tmp_path, vendor)
        frames.append(load_csv(path, profile=profile)[["power", "temp"]])
    for f in frames[1:]:
        pd.testing.assert_frame_equal(f, frames[0], check_freq=False, check_names=False)


def test_epoch_timestamp_file_normalizes(tmp_path):
    # a historian that stamps epoch seconds — tsparse auto-detects it (no profile needed)
    epochs = (_IDX.view("int64") // 10**9).tolist()
    lines = ["timestamp,power,temp"] + [f"{e},{p},{t}" for e, p, t in zip(epochs, _POWER, _TEMP)]
    path = tmp_path / "epoch.csv"
    path.write_text("\n".join(lines) + "\n")
    df = load_csv(str(path))
    pd.testing.assert_frame_equal(
        df[["power", "temp"]], _reference(), check_freq=False, check_names=False
    )


def test_bom_and_thousands_and_null_tokens(tmp_path):
    # UTF-8 BOM header + thousands-grouped value + a null token, generic profile
    text = '﻿timestamp,power\n2024-07-01 00:00:00,"1,234.5"\n2024-07-01 01:00:00,N/A\n'
    path = tmp_path / "bom.csv"
    path.write_text(text, encoding="utf-8")
    df = load_csv(str(path))
    assert df["power"].iloc[0] == 1234.5 and np.isnan(df["power"].iloc[1])
