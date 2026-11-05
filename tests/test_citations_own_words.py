"""0.112 (#328 follow-up): check_citations accepts the server's own words.

A fact's text cited with its own id, and a NOTICE line repeating a notice word for word, are
the server's words: they are supported as a whole however many sentences they hold and whatever
verdict, negation or caveat they state. And no prompt recommends a sentence the check rejects.

Every case runs on small stores in tmp directories (the sensor-bias catalog entry is generated
locally); nothing reaches the network.
"""

import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber import datasets  # noqa: E402
from camber.capabilities import CallContext, FactLedger, default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import check_citations  # noqa: E402
from camber.model.roles import Role  # noqa: E402

REG = default_registry()
_DS, _BIAS = "synthetic-ahu-sensor-bias", "ds-synthetic-ahu-sensor-bias"
_LEAK = "leaking_valve@AHU__sat_bias"
_RESET = "supply_air_reset_compliance@AHU__sat_bias"


def _meter(days=150, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


def _gappy():
    idx = pd.date_range("2026-03-02", periods=48, freq="h")
    sat = pd.Series(55.0, index=idx)
    sat.iloc[10:20] = np.nan
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: 60.0}, index=idx)


def _write(root, name, config) -> str:
    path = os.path.join(str(root), name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return path


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    """``demo-fac`` (the RCx fixture's two AHUs), ``gap-site`` and ``mv-site`` in one store, and
    the sensor-bias dataset (a 3 F supply-air offset against a logger) in another, each with a
    run config."""
    root = tmp_path_factory.mktemp("ownwords")
    st = fx.make_store(root, meta=None)
    st.write_role_frame(_gappy(), facility_id="gap-site", equip="AHU-G", equip_class="AHU")
    st.write_role_frame(_meter(), facility_id="mv-site", equip="MainMeter", equip_class="METER")
    st.points()
    rcx_cfg = _write(root, "rcx.json", fx.config())
    mv = {
        "source": {"kind": "store", "store": "store", "facility_id": "mv-site"},
        "equipment": [{"class": "METER"}],
        "mv": [
            {
                "class": "METER",
                "role": "power",
                "period": ["2024-01-01", "2024-03-31"],
                "reporting_period": ["2024-04-01", None],
            }
        ],
    }
    mv_cfg = _write(root, "mv.json", mv)
    bias_store = str(root / "bias")
    datasets.ingest(_DS, bias_store, data_dir=str(root / "cache"))
    bias_cfg = _write(
        root,
        "bias.json",
        datasets.config_template(_DS, bias_store, exercise="data-sensor-conditional"),
    )
    return {
        "demo": (st.root, (fx.FID, "gap-site", "mv-site"), (rcx_cfg, mv_cfg)),
        "bias": (bias_store, (_BIAS,), (bias_cfg,)),
    }


def _ctx(stores, which):
    store, allowed, configs = stores[which]
    return CallContext(
        store=store,
        allowed_facilities=frozenset(allowed),
        config=configs if len(configs) > 1 else configs[0],
        ledger=FactLedger(),
    )


#: one or more calls of every capability: (store, name, arguments)
CALLS = [
    ("demo", "facilities", {}),
    ("demo", "points", {"facility_id": fx.FID}),
    ("demo", "coverage", {"facility_id": fx.FID}),
    ("demo", "coverage", {"facility_id": "gap-site"}),
    ("demo", "sensor_trust", {"facility_id": fx.FID}),
    ("demo", "rule_run", {"facility_id": fx.FID, "min_severity": "ok"}),
    (
        "demo",
        "finding_detail",
        {"facility_id": fx.FID, "finding_id": "economizer_high_limit@DemoAHU"},
    ),
    ("demo", "rcx_report", {"facility_id": fx.FID}),
    ("demo", "mv_summary", {"facility_id": "mv-site", "claim": "12%"}),
    ("demo", "mv_summary", {"facility_id": "mv-site", "claim": "saves 15% (42,000 kWh) a year"}),
    ("demo", "drift", {"facility_id": fx.FID, "baseline": ["2026-03-02", "2026-03-15"]}),
    ("demo", "dataset_search", {"query": "sensor"}),
    ("demo", "dataset_entry", {"dataset_id": "at-30bldg-sensors"}),
    ("demo", "rule_params", {}),
    ("demo", "check_citations", {"text": "The unit has a fault [F9@nowhere]."}),
    ("bias", "points", {"facility_id": _BIAS}),
    ("bias", "coverage", {"facility_id": _BIAS}),
    ("bias", "sensor_trust", {"facility_id": _BIAS}),
    ("bias", "sensor_trust", {"facility_id": _BIAS, "view": "report"}),
    ("bias", "rule_run", {"facility_id": _BIAS, "min_severity": "ok"}),
    ("bias", "rule_run", {"facility_id": _BIAS}),
    ("bias", "finding_detail", {"facility_id": _BIAS, "finding_id": _LEAK}),
    ("bias", "finding_detail", {"facility_id": _BIAS, "finding_id": _RESET}),
    ("bias", "rcx_report", {"facility_id": _BIAS}),
]


@pytest.fixture(scope="module")
def issued(stores):
    """Every fact each call issued, with the context (and so the ledger) it was issued in."""
    out = []
    for which, name, args in CALLS:
        ctx = _ctx(stores, which)
        res = REG.call(name, args, ctx)
        out += [(f"{name} {args}", ctx, f) for f in res.facts]
    return out


def _check(ctx, text) -> dict:
    return REG.call("check_citations", {"text": text, "detail": True}, ctx).data


def test_every_capability_issued_facts(issued):
    names = {label.split()[0] for label, _, _ in issued}
    assert names == {c.name for c in REG.capabilities()}
    kinds = {f.get("kind") for _, _, f in issued}
    assert {"notice", "claimed_figure", "finding", "evidence_span", "issue"} <= kinds


def test_every_fact_cited_for_its_own_text_is_supported(issued):
    failed = []
    for label, ctx, fact in issued:
        data = _check(ctx, f"{fact['text']} [{fact['id']}]")
        if not data["grounded"]:
            why = [s.get("why", "") for s in data["sentences"] if s["verdict"] != "supported"]
            failed.append((label, fact["id"], fact["text"][:120], why))
    assert not failed, "\n".join(map(str, failed))


def test_a_fact_quoted_in_a_list_or_after_another_sentence_is_supported(issued):
    label, ctx, fact = next(i for i in issued if ". " in i[2]["text"])
    cite = f"[{fact['id']}]"
    for text in (
        f"- {fact['text']} {cite}",
        f"2. {fact['text']} {cite}.",
        f"{fact['text']} {cite} {fact['text']} {cite}",
    ):
        data = _check(ctx, text)
        assert data["grounded"], (label, text, data["sentences"])
        assert data["verified_text"] == text


def test_a_notice_quoted_word_for_word_is_supported_whole(issued):
    label, ctx, notice = next(
        i for i in issued if i[2].get("kind") == "notice" and ". " in i[2]["text"]
    )
    cite = f"[{notice['id']}]"
    for prefix in ("NOTICE: ", "Notice: ", "**Notice:** ", "- NOTICE: ", ""):
        text = f"{prefix}{notice['text']} {cite}"
        data = _check(ctx, text)
        assert data["grounded"] and data["n_sentences"] == 1, (prefix, data["sentences"])


# --------------------------------------------------------------------------- what still fails


def test_a_quote_with_words_added_is_judged_as_before(issued):
    """Only the fact's own text, whole and with its own id, is taken as said: a prefix, a part
    of it, another fact's id or a word changed is judged like any other sentence."""
    label, ctx, fact = next(
        i for i in issued if i[2].get("kind") == "finding" and "(ok)" in i[2]["text"]
    )
    t, cite = fact["text"], f"[{fact['id']}]"
    other = next(f for lb, c, f in issued if c is ctx and f["id"] != fact["id"])
    for text in (
        f"In fact the valve leaks. {t} {cite}",  # the sentence before it: judged, uncited
        f"{t}, and the valve leaks {cite}",  # words between the quote and its citation
        f"{t.replace('(ok)', '(fault)')} {cite}",
        f"{t} [{other['id']}]",
    ):
        assert not _check(ctx, text)["grounded"], text


def test_a_notice_line_with_more_words_is_judged():
    notice = (
        "1 listed finding(s) rest on an input whose sensor reference disagrees with it. "
        "sensor_trust shows the comparison."
    )
    nfact = {"id": "N1@rule_run-aaaaaa", "kind": "notice", "text": notice}
    fault = {"id": "F1@rule_run-aaaaaa", "kind": "finding", "text": '"AHU-1" leaks (fault)'}
    facts = [nfact, fault]
    ctx = CallContext(store="/nonexistent", allowed_facilities=frozenset())
    ok = check_citations(ctx, {"text": f"NOTICE: {notice} [N1@rule_run-aaaaaa]", "facts": facts})
    assert ok.data["grounded"] and ok.data["n_sentences"] == 1
    # uncited, a NOTICE line repeating a notice fact is kept and not judged, however labelled
    for label in ("NOTICE:", "Notice:", "**Notice:**", "- NOTICE:"):
        kept = check_citations(ctx, {"text": f"{label} {notice}", "facts": facts})
        assert kept.data["grounded"] and kept.data["n_sentences"] == 0, label
    for text in (
        f"NOTICE: {notice} The valve is fine. [N1@rule_run-aaaaaa]",
        f"NOTICE: {notice[:-1]} and all sensors pass. [N1@rule_run-aaaaaa]",
        f"NOTICE: {notice} [F1@rule_run-aaaaaa]",
    ):
        res = check_citations(ctx, {"text": text, "facts": facts})
        assert not res.data["grounded"], text


# --------------------------------------------------------------------------- the prompts

#: an example sentence a prompt recommends: a double-quoted span that cites a fact id
_EXAMPLE = re.compile(r'"([^"\n]*\[[A-Z]\d+@[^\]"\n]*\][^"\n]*)"')
_ARGS = {
    "facility_id": "mv-site",
    "equip": "DemoAHU",
    "finding_id": "economizer_high_limit@DemoAHU",
    "equips": "DemoAHU, DemoAHU2",
}


def _examples() -> list:
    out = []
    for p in REG.prompts():
        args = {a.name: _ARGS.get(a.name, "12%") for a in p.arguments}
        for message in p.render(args):
            out += [(p.name, s) for s in _EXAMPLE.findall(message["text"])]
    return out


def test_the_prompts_recommend_these_sentences():
    assert [name for name, _ in _examples()] == ["check_savings_claim"]


@pytest.mark.parametrize("claim", ["12%", "a 12% saving", "saves 12% a year", "12% less energy"])
def test_every_sentence_a_prompt_recommends_is_supported(stores, claim):
    """Each example names a fact id with its token left out ("...@mv_summary-..."): the fact the
    named call issues for that id takes its place."""
    ctx = _ctx(stores, "demo")
    res = REG.call("mv_summary", {"facility_id": "mv-site", "claim": claim}, ctx)
    for name, sentence in _examples():

        def real(m):
            prefix = m.group(1).split("@")[0] + "@" + m.group(2)
            return "[" + next(f["id"] for f in res.facts if f["id"].startswith(prefix)) + "]"

        text = re.sub(r"\[([A-Z]\d+@)(mv_summary)-\.\.\.\]", real, sentence)
        assert text != sentence, (name, sentence)
        data = _check(ctx, text)
        assert data["grounded"], (name, claim, text, data["sentences"])


# --------------------------------------------------------------------------- shares listed together

_REJUDGED = (
    '"AHU-1" leaking_valve (fault) re-judged with its input(s) corrected for the configured '
    'reference\'s bias ("supply_air_temp" by -3.0 °F): ok, "AHU-1": no valve leak -- with both '
    "valves shut, air holds near the 2F fan-heat allowance (median +0.4F beyond it); HW-leak 0% / "
    "CHW-leak 0% of both-valves-shut samples; so the finding is explained by the sensor offset"
)
_FAULT = (
    '"AHU-1" leaking_valve (fault): "AHU-1": with both valves shut, air shifts (median +3.4F '
    "beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples"
)


@pytest.mark.parametrize(
    "text, grounded",
    [
        # a share listed before the one that says what it is of is a zero of that too
        ("HW-leak 0% / CHW-leak 0% of both-valves-shut samples [R1@finding_detail-aaaaaa].", True),
        ("HW-leak 86% / CHW-leak 0% of both-valves-shut samples [F2@rule_run-aaaaaa].", True),
        ("HW-leak 0% / CHW-leak 0% of both-valves-shut samples [F2@rule_run-aaaaaa].", False),
        ("HW-leak 0% / CHW-leak 86% of both-valves-shut samples [F2@rule_run-aaaaaa].", False),
    ],
)
def test_shares_listed_together_are_read_like_the_facts_list(text, grounded):
    facts = [
        {"id": "R1@finding_detail-aaaaaa", "kind": "reference_effect", "text": _REJUDGED},
        {"id": "F2@rule_run-aaaaaa", "kind": "finding", "text": _FAULT},
    ]
    ctx = CallContext(store="/nonexistent", allowed_facilities=frozenset())
    assert check_citations(ctx, {"text": text, "facts": facts}).data["grounded"] is grounded
