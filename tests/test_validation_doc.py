"""#33: the benchmark cells quoted in docs/VALIDATION.md must match the benchmark output.

Like the dossier's anti-rot tests (tests/test_dossier.py): the LBNL SDAHU drift rows are checked
against the gated baseline (`examples/lbnl_fdd/benchmark-baseline.json`), and the opt-in FPU and
chiller-plant rows against the last opt-in run's metrics (`examples/lbnl_fdd/optin-measured.json`,
written from `examples/lbnl_fdd/benchmark.py --json` with those subsets fetched). A cell that is
not regenerated after the benchmark moves fails here instead of misleading a reader.
"""

import json
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MD = os.path.join(_ROOT, "docs", "VALIDATION.md")
_BASELINE = os.path.join(_ROOT, "examples", "lbnl_fdd", "benchmark-baseline.json")
_OPTIN = os.path.join(_ROOT, "examples", "lbnl_fdd", "optin-measured.json")


def _row(md: str, first_cell: str) -> str:
    """The table row whose first cell starts with ``first_cell``."""
    for line in md.splitlines():
        if line.startswith("| " + first_cell):
            return line
    raise AssertionError(f"no docs/VALIDATION.md row starting {first_cell!r}")


def _counts(m: dict, prefix: str) -> dict:
    return {k: m[f"{prefix}.{k}"] for k in ("tp", "fn", "fp", "tn", "declined")}


@pytest.fixture(scope="module")
def md():
    return open(_MD, encoding="utf-8").read()


@pytest.fixture(scope="module")
def optin():
    return json.load(open(_OPTIN))


def test_fpu_drift_row_matches_the_opt_in_run(md, optin):
    row = _row(md, "`vav_airflow_drift`, `vav_reheat_valve_drift`")
    a = _counts(optin, "drift.vav_airflow_drift")
    r = _counts(optin, "drift.vav_reheat_valve_drift")
    assert optin["drift.vav_airflow_drift.recall"] == round(a["tp"] / (a["tp"] + a["fn"]), 4)
    want_a = f"airflow **recall {a['tp']}/{a['tp'] + a['fn']}, {a['fp']} false positives in "
    want_a += f"{a['fp'] + a['tn']}"
    assert want_a + ("**" if not a["declined"] else f", {a['declined']} declined**") in row
    want_r = f"reheat valve **recall {r['tp']}/{r['tp'] + r['fn']}, {r['fp']} false positives in "
    want_r += f"{r['fp'] + r['tn']}"
    assert want_r + ("**" if not r["declined"] else f", {r['declined']} declined**") in row


def test_series_fpu_row_matches_the_opt_in_run(md, optin):
    # the series (SFPU) boxes are scored as their own subset since 0.92.0 (#12)
    row = _row(md, "series-box (SFPU) `vav_airflow_drift`")
    for label, name in (
        ("airflow", "vav_airflow_drift"),
        ("reheat valve", "vav_reheat_valve_drift"),
    ):
        c = _counts(optin, f"drift.sfpu.{name}")
        want = f"{label} **recall {c['tp']}/{c['tp'] + c['fn']}, {c['fp']} false positives in "
        want += f"{c['fp'] + c['tn']}"
        assert want + ("**" if not c["declined"] else f", {c['declined']} declined**") in row, want


def test_chiller_sensor_reference_row_matches_the_opt_in_run(md, optin):
    row = _row(md, "sensor bias vs physical fault on the chiller plant")
    for label, pair in (
        ("chiller leaving water", "chiller_leaving_water"),
        ("tower leaving water", "tower_leaving_water"),
    ):
        c = _counts(optin, f"chiller.sensor.{pair}")
        cell = f"{label} **TPR {c['tp']}/{c['tp'] + c['fn']}, FPR {c['fp']}/{c['fp'] + c['tn']}"
        cell += f", {c['declined']} declined**" if c["declined"] else "**"
        assert cell in row, cell


def test_chiller_plant_row_matches_the_opt_in_run(md, optin):
    row = _row(md, "`chiller_efficiency`, `cooling_tower_approach`")
    for name in ("chiller_efficiency", "cooling_tower_approach"):
        c = _counts(optin, f"chiller.{name}")
        cell = f"`{name}` **TPR {c['tp']}/{c['tp'] + c['fn']}, FPR {c['fp']}/{c['fp'] + c['tn']}"
        cell += f", {c['declined']} declined**" if c["declined"] else "**"
        assert cell in row, (name, cell)
        assert optin[f"chiller.{name}.fpr"] == round(c["fp"] / (c["fp"] + c["tn"]), 4)


def test_sdahu_drift_rows_match_the_gated_baseline(md):
    base = json.load(open(_BASELINE))
    row = _row(md, "`coil_valve_drift`")
    m = re.search(r"(\d+) false alarm in (\d+)", row)
    assert m and round(int(m[1]) / int(m[2]), 4) == base["drift.coil_valve_drift.fpr"]
    row = _row(md, "`economizer_damper_drift`")
    m = re.search(r"recall (\d+)/(\d+), (\d+) false positives in (\d+)", row)
    assert m, row
    tp, pos, fp, neg = (int(g) for g in m.groups())
    assert round(tp / pos, 4) == base["drift.economizer_damper_drift.recall"]
    assert round(fp / neg, 4) == base["drift.economizer_damper_drift.fpr"]


def test_opt_in_record_holds_only_opt_in_metrics(optin):
    # the gated baseline stays the only gate; the opt-in record never overlaps it
    base = json.load(open(_BASELINE))
    assert optin and not set(optin) & set(base)
    assert all(k.startswith(("chiller.", "drift.vav_", "drift.sfpu.")) for k in optin)
