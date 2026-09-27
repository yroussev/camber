"""Tests for the unified validation dossier (camber.dossier) + `camber validate`.

Beyond the usual plumbing, two **anti-rot** tests keep the cited real-data figures honest: the BDG2
numbers are checked *exactly* against the committed benchmark baseline, and the LBNL numbers against
the `docs/VALIDATION.md` table they quote — so a cited figure that drifts fails CI instead of
silently misleading.
"""

import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import cli, dossier, faultlab, fleetlab  # noqa: E402
from camber.eval import benchmark  # noqa: E402
from camber.validation import metrics_with_ci  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BDG2_BASELINE = os.path.join(_ROOT, "examples", "bdg2", "benchmark-baseline.json")
_BDG2_SAVINGS_BASELINE = os.path.join(_ROOT, "examples", "bdg2", "savings-benchmark-baseline.json")
_VALIDATION_MD = os.path.join(_ROOT, "docs", "VALIDATION.md")


def test_build_dossier_has_five_tracks_two_live_three_cited():
    d = dossier.build_dossier()
    assert [t.key for t in d.tracks] == [
        "synthetic_fdd",
        "fleet_fdd",
        "lbnl_fdd",
        "bdg2_mv",
        "bdg2_mv_savings",
    ]
    kinds = {t.key: t.kind for t in d.tracks}
    assert kinds["synthetic_fdd"] == "live-recomputed"
    assert kinds["fleet_fdd"] == "live-recomputed"
    assert kinds["lbnl_fdd"] == "cited-reference"
    assert kinds["bdg2_mv"] == "cited-reference"
    assert kinds["bdg2_mv_savings"] == "cited-reference"


def test_live_synthetic_track_matches_faultlab():
    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    ci = metrics_with_ci(rep.overall)
    track = next(t for t in dossier.build_dossier().tracks if t.key == "synthetic_fdd")
    assert track.rates["tpr"].rate == ci["true_positive_rate"].rate
    assert track.rates["tpr"].lo == ci["true_positive_rate"].lo
    assert track.rates["fpr"].rate == ci["false_positive_rate"].rate


def test_live_fleet_track_matches_fleetlab():
    rep = benchmark(fleetlab.labeled_records(), fleetlab.targets())
    ci = metrics_with_ci(rep.overall)
    track = next(t for t in dossier.build_dossier().tracks if t.key == "fleet_fdd")
    assert track.rates["tpr"].rate == ci["true_positive_rate"].rate
    assert track.rates["fpr"].rate == ci["false_positive_rate"].rate
    # attribution folded into metrics, mean == 1.0 on the generated fleet
    assert track.metrics["fleet.mean_attribution"] == 1.0


def test_reference_bdg2_matches_committed_baseline_exactly():
    """Anti-rot: the cited BDG2 figures must equal the committed benchmark baseline exactly."""
    base = json.load(open(_BDG2_BASELINE))
    ref = dossier._REFERENCE["bdg2_mv"]["rates"]
    for label, prefix in (
        ("acceptance", "pooled"),
        ("acceptance_chilledwater", "chilledwater"),
        ("acceptance_electricity", "electricity"),
    ):
        ci = ref[label]
        assert ci.rate == base[f"{prefix}.acceptance_rate"], label
        assert ci.lo == base[f"{prefix}.acceptance_ci_lo"], label
        assert ci.hi == base[f"{prefix}.acceptance_ci_hi"], label
        assert ci.n == base[f"{prefix}.n_buildings"], label


def test_reference_bdg2_savings_matches_committed_baseline_exactly():
    """Anti-rot: each cited placebo UICF is the committed savings baseline's uicf and n, with the
    95% Wilson interval recomputed from them -- and the headline quotes the same percentages."""
    from camber.validation import wilson_interval

    base = json.load(open(_BDG2_SAVINGS_BASELINE))
    ref = dossier._REFERENCE["bdg2_mv_savings"]
    assert set(ref["rates"]) == {
        f"placebo_uicf_{k}_{m}" for k in ("g14", "exact") for m in ("electricity", "chilledwater")
    }
    for label, ci in ref["rates"].items():
        _, _, kernel, meter = label.split("_")
        key = f"{meter}.placebo.forecast_{kernel}"
        rate, n = base[f"{key}.uicf"], base[f"{key}.n"]
        lo, hi = wilson_interval(round(rate * n), n)
        assert (ci.rate, ci.n) == (rate, n), label
        assert (ci.lo, ci.hi) == (round(lo, 4), round(hi, 4)), label
        assert f"{100 * rate:.0f}%" in ref["headline"], label
    assert f"{base['electricity.n_eligible']:,}" in ref["headline"]
    assert f"{base['chilledwater.n_eligible']:,}" in ref["headline"]
    total = base["electricity.n_eligible"] + base["chilledwater.n_eligible"]
    assert f"{total:,} BDG2 meters" in ref["coverage"]


def test_bdg2_savings_published_figures_match_the_baseline():
    """Anti-rot: every figure of the published M&V savings paragraph in docs/VALIDATION.md is
    recomputed from the committed savings baseline (the maintainer-approved text of #21)."""
    base = json.load(open(_BDG2_SAVINGS_BASELINE))
    md = " ".join(open(_VALIDATION_MD).read().split())
    start = md.index("Representative result (2016 baseline, 2017 reporting")
    para = md[start : md.index("### Monte Carlo coverage", start)]

    def pct(x):
        return f"{100 * x:.0f}"

    e, c = "electricity", "chilledwater"
    assert f"{base[e + '.n_eligible']:,} electricity and {base[c + '.n_eligible']:,}" in para
    for k in ("forecast_g14", "forecast_exact", "forecast_g14_95"):
        pe, pc = pct(base[f"{e}.placebo.{k}.uicf"]), pct(base[f"{c}.placebo.{k}.uicf"])
        assert re.search(rf"\b{pe}% (/|of electricity and) {pc}%", para), k
    methods = ("forecast_g14", "forecast_exact", "backcast_g14", "backcast_exact",
               "standard_conditions_exact")  # fmt: skip
    for meter, pts in ((e, 5), (c, 7)):
        errs = [base[f"{meter}.inject10.{m}.abs_error_p50"] for m in methods]
        assert all(round(100 * x) == pts for x in errs), meter
    for meter, want in ((e, "82–84%"), (c, "68–75%")):
        sig = [base[f"{meter}.inject10.{m}.significant_rate"] for m in methods]
        assert f"{pct(min(sig))}–{pct(max(sig))}%" == want and want in para, meter
    s20 = "steps.single_20"
    assert f"finds {pct(base[f'{e}.{s20}.detect_rate'])}% of planted 20% steps" in para
    assert f"({pct(base[f'{c}.{s20}.detect_rate'])}% on chilled water)" in para
    assert base[f"{e}.{s20}.date_error_p50"] == base[f"{c}.{s20}.date_error_p50"] == 0.0
    se, sc = (base[f"{m}.{s20}.spurious_per_series"] for m in (e, c))
    sp = f"{se:.2f} / {sc:.2f}"
    assert sp + " spurious" in para
    assert (
        base[f"{e}.static.full.recovery_error_p50"]
        == base[f"{c}.static.full.recovery_error_p50"]
        == 0.0
    )
    mid_e = round(100 * base[f"{e}.static.mid.recovery_error_p50"], 1)
    mid_c = round(100 * base[f"{c}.static.mid.recovery_error_p50"], 1)
    assert f"median error of {mid_e:g} points (electricity) and {mid_c:g} points" in " ".join(
        para.split()
    )
    assert "B59" not in para and "lbnl-b59" not in para


def test_reference_lbnl_matches_validation_doc():
    """Anti-rot: the cited LBNL pooled TPR must match the docs/VALIDATION.md table row."""
    md = open(_VALIDATION_MD).read()
    # the pooled row: | **Pooled** | **78% [45–94%]** | 0% | 13 |
    pat = r"Pooled\*\*\s*\|\s*\*\*(\d+)%\s*\[(\d+)[–-](\d+)%\]\*\*\s*\|\s*(\d+)%\s*\|\s*(\d+)"
    m = re.search(pat, md)
    assert m, "could not find the pooled OA-fraction row in docs/VALIDATION.md"
    tpr, lo, hi, fpr, n = (int(g) for g in m.groups())
    ci = dossier._REFERENCE["lbnl_fdd"]["rates"]["tpr"]
    assert round(ci.rate * 100) == tpr and round(ci.lo * 100) == lo and round(ci.hi * 100) == hi
    assert ci.n == n
    # the bare pooled FPR is carried in the headline text (not fabricated as an interval)
    assert f"FPR {fpr}%" in dossier._REFERENCE["lbnl_fdd"]["headline"]


def test_dossier_text_names_each_track_with_kind_tag():
    text = dossier.build_dossier().to_text()
    for t in dossier.build_dossier().tracks:
        assert t.title in text
    assert "[LIVE]" in text and "[CITED]" in text


class _Extract(HTMLParser):
    def error(self, message):  # pragma: no cover - HTMLParser abstract in <3.10 shim
        raise AssertionError(message)


def test_dossier_html_is_self_contained_and_parses():
    h = dossier.build_dossier().to_html()
    assert h.startswith("<!doctype html>")
    assert "http://" not in h and "https://" not in h  # no external assets
    assert "<script" not in h and "src=" not in h
    _Extract().feed(h)  # parses without raising


def test_dossier_is_deterministic():
    assert dossier.build_dossier().as_dict() == dossier.build_dossier().as_dict()
    assert dossier.build_dossier(full=True).as_dict() == dossier.build_dossier(full=True).as_dict()


def test_dossier_as_dict_is_json_serializable():
    payload = dossier.build_dossier(full=True).as_dict()
    round_tripped = json.loads(json.dumps(payload))
    assert len(round_tripped["tracks"]) == 5


def test_cli_validate_writes_json_and_html(tmp_path):
    j, h = tmp_path / "d.json", tmp_path / "d.html"
    assert cli.main(["validate", "--json", str(j), "--html", str(h)]) == 0
    assert j.exists() and h.exists()
    payload = json.load(open(j))
    assert [t["key"] for t in payload["tracks"]] == [
        "synthetic_fdd",
        "fleet_fdd",
        "lbnl_fdd",
        "bdg2_mv",
        "bdg2_mv_savings",
    ]


def test_cli_validate_full_smoke():
    assert cli.main(["validate", "--full"]) == 0
