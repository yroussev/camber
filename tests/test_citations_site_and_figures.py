"""The next targets after B (0112-cite10, PLAN-CITE-EVAL round 10), each with the generated form
or operator that covers it (tools/cite_eval): a unit's fact said of the whole site
(promote_site in heading, conditional, bold-label and table forms, and a figure or rule said of
the site); a count of one verdict a run summary does not give ("site-a has 6 warn results"); a
unit id spelled another way naming a unit no cited fact is about, when a cited fact names its
family only in its text (id_spelling x swap_unit); a parameter's figure that is another listed
parameter's value (perturb_figure on rule_params).
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

FACTS = facttable.facts_index(facttable.load_or_collect(run.FACTS))
FIND = "F1@finding_detail-4ecd59"  # "AHU-1" economizer_high_limit (fault): ... 43% ...
RUN = "F1@rule_run-54307c"  # ... 2 fault, 4 warn, 0 info and 12 ok ...
CONDS = "C1@rule_run-54307c"  # "AHU-1" leaking_valve (ok); ... (names AHU-1 in its text)
PARAMS = "F2@rule_params-390ae2"  # economizer_high_limit defaults: ..., warn_pct = 10.0 ...
TRUST = "F1@sensor_trust-c81f17"  # site-a: 27 points judged: 23 trusted, 4 suspect


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-targets")))


def _kept(judge, draft: str, *cites) -> bool:
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": [FACTS[c] for c in cites],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return bool(r["grounded"])


@pytest.mark.parametrize(
    "draft",
    [
        f"## site-a fails economizer_high_limit [{FIND}]",
        f"If the trends are right, site-a fails economizer_high_limit [{FIND}].",
        f"**site-a:** is flagged by economizer_high_limit on 43% of those samples [{FIND}].",
        f"| Unit | Result |\n|---|---|\n| site-a | is flagged by economizer_high_limit on 43% "
        f"of those samples [{FIND}] |",
        f"site-a has economizer_high_limit on 43% of those samples [{FIND}].",
    ],
)
def test_a_units_fact_said_of_the_site_fails(judge, draft):
    assert not _kept(judge, draft, FIND)


def test_the_unit_and_the_sites_own_facts_still_pass(judge):
    assert _kept(judge, f"AHU-1 fails economizer_high_limit [{FIND}].", FIND)
    assert _kept(judge, f"## site-a has 23 trusted points [{TRUST}]", TRUST)
    assert _kept(judge, f"If the trends are right, site-a has 4 suspect points [{TRUST}].", TRUST)


def test_a_count_of_one_verdict_is_the_summarys_count_of_it(judge):
    assert _kept(judge, f"site-a has 4 warn results [{RUN}].", RUN)
    assert not _kept(judge, f"site-a has 6 warn results [{RUN}].", RUN)


def test_a_unit_spelled_another_way_is_read_as_its_id(judge):
    assert _kept(judge, f"AHU 1 passes leaking_valve [{CONDS}].", CONDS)
    assert not _kept(judge, f"AHU 3 passes leaking_valve [{CONDS}].", CONDS)


def test_a_parameters_figure_is_its_own(judge):
    said = "The default warn_pct of economizer_high_limit is"
    assert _kept(judge, f"{said} 10.0% [{PARAMS}].", PARAMS)
    assert not _kept(judge, f"{said} 20% [{PARAMS}].", PARAMS)
