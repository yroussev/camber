"""A name no fact of the session gave fails closed (0112-cite10), kept narrow: a slug that
reads as a unit's, site's or dataset's name ("irish-ahu", not "site-wide" or "duct-logger" of a
fact's "duct logger"), and a rule name. A rule named with a run's summary must be named by a
cited fact; a caveat, masked samples or a skipped rule must be said by a cited fact; and a
caveat that hedges supports no sentence that states it as a finding. The session's names are
those its ledger recorded (a view carries them to a worker).
"""

import os
import pickle
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities._ledger import FactLedger  # noqa: E402
from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

FACTS = facttable.facts_index(facttable.load_or_collect(run.FACTS))
DATASET = "F1@dataset_entry-a824c5"  # at-30bldg-sensors
AHU2_FAILS = "F3@rule_run-54307c"  # "AHU-2" simultaneous_heat_cool (fault)
RUN = "F1@rule_run-54307c"  # the run's summary: no rule named
CAVEAT = {
    "id": "V1@rule_run-54307c",
    "kind": "caveat",
    "value": "caveat",
    "rule": "leaking_valve",
    "equip": "AHU-1",
    "finding_ids": ["leaking_valve@AHU-1"],
    "text": '"AHU-1" leaking_valve caveat: a heat rise downstream of the coils (fan heat, '
    "supply-sensor placement) can read as a heating leak -- a coil leaving-air sensor, where "
    "trended, settles it",
}
SKIPPED = {
    "id": "K1@rule_run-54307c",
    "kind": "skipped_rules",
    "facility_id": "site-a",
    "value": 1,
    "rules": ["condenser_bypass_leak"],
    "text": "1 rule(s) skipped for missing inputs on site-a, so not judged: "
    'condenser_bypass_leak on "CH-1" (missing cw_flow)',
}


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-names")))


def _run(judge, draft, *facts):
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": [FACTS[f] if isinstance(f, str) else f for f in facts],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return r


def _kept(judge, draft, *facts) -> bool:
    return bool(_run(judge, draft, *facts)["grounded"])


def test_a_dataset_no_fact_named_fails(judge):
    r = _run(judge, f"irish-ahu is licensed CC-BY-NC-SA-4.0 [{DATASET}].", DATASET)
    assert not r["grounded"]
    assert "'irish-ahu': no fact of this session names it" in r["sentences"][0][2]


def test_a_named_dataset_and_ordinary_compounds_pass(judge):
    assert _kept(judge, f"at-30bldg-sensors is licensed CC-BY-NC-SA-4.0 [{DATASET}].", DATASET)
    assert _kept(
        judge, f"Site-wide, AHU-2 fails simultaneous_heat_cool [{AHU2_FAILS}].", AHU2_FAILS
    )
    assert _kept(
        judge,
        f"AHU-2 fails simultaneous_heat_cool, an AHU-level result [{AHU2_FAILS}].",
        AHU2_FAILS,
    )


def test_a_name_issued_in_the_session_but_not_cited_is_not_denied_here(judge):
    # the session named irish-ahu (another fact, uncited): the deny does not fire; what the
    # sentence says of it is read as before
    other = {
        "id": "F2@dataset_search-000000",
        "kind": "dataset",
        "text": "irish-ahu (Irish industrial AHU): real, licence CC-BY-4.0, access open",
    }
    r = _run(judge, f"irish-ahu is licensed CC-BY-NC-SA-4.0 [{DATASET}].", DATASET, other)
    assert "no fact of this session names it" not in r["sentences"][0][2]


def test_a_rule_named_with_the_runs_summary_needs_a_fact_that_names_it(judge):
    said = "The condenser_bypass_leak rule was skipped for missing inputs"
    assert not _kept(judge, f"{said} [{RUN}].", RUN, SKIPPED)
    assert _kept(judge, f"{said} [{SKIPPED['id']}].", SKIPPED)
    # no fact of the session names the rule at all
    r = _run(judge, f"{said} [{RUN}].", RUN)
    assert "rule 'condenser_bypass_leak'" in r["sentences"][0][2]


def test_a_caveat_needs_a_caveat_fact(judge):
    said = "The rule's own caveat says fan heat or sensor placement can read as a heating leak"
    assert not _kept(judge, f"{said} [{AHU2_FAILS}].", AHU2_FAILS)
    assert _kept(judge, f"{said} on AHU-1 [{CAVEAT['id']}].", CAVEAT)


def test_a_hedging_caveat_supports_no_finding(judge):
    assert not _kept(judge, f"AHU-1 has a heating leak [{CAVEAT['id']}].", CAVEAT)
    assert _kept(
        judge, f"On AHU-1 a heat rise can read as a heating leak [{CAVEAT['id']}].", CAVEAT
    )


def test_masked_and_skipped_need_a_fact_that_says_so(judge):
    assert not _kept(judge, f"Many samples on AHU-2 were masked [{AHU2_FAILS}].", AHU2_FAILS)
    assert not _kept(judge, f"Some rules were skipped on AHU-2 [{AHU2_FAILS}].", AHU2_FAILS)


def test_the_ledger_keeps_the_names_its_facts_gave():
    led = FactLedger(max_bytes=200)
    led.record([CAVEAT, SKIPPED, FACTS[DATASET]])
    names = led.names()
    assert {"leaking_valve", "condenser_bypass_leak", "ahu-1", "ch-1"} <= names
    assert "at-30bldg-sensors" in names  # a forgotten fact's names stay (max_bytes is tiny)
    view = led.view([CAVEAT["id"]])
    assert view.names() == names
    assert pickle.loads(pickle.dumps(view)).names() == names
