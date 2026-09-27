"""The catalog sweep's report checks (scripts/catalog_sweep.py; a maintainer tool, no data needed).

#36: the "no G36 verdict without a declared sequence" check must judge verdicts, not citations --
an action plan's ``Cite`` column (``ASHRAE 62.1 (DCV) / G36``) or an ECM ``Standard`` column is a
reference, while a finding that grades the unit against the G36 default is a verdict.
"""

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sweep():
    spec = importlib.util.spec_from_file_location(
        "catalog_sweep", os.path.join(_ROOT, "scripts", "catalog_sweep.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PLAN = (
    "<h2>Recommended actions</h2><table><tr><th>#</th><th>Severity</th><th>Rule</th>"
    "<th>Recommended action</th><th>Cite</th></tr>"
    "<tr><td>1</td><td>warn</td><td>dcv_verification</td><td>Enable DCV</td>"
    "<td>ASHRAE 62.1 (DCV) / G36</td></tr>"
    "<tr><td>2</td><td>fault</td><td>unmet_setpoint_hours</td><td>Investigate zones</td>"
    "<td>ASHRAE G36 / Std-55 (comfort)</td></tr></table>"
)
_ECM = (
    "<table><tr><th>Measure</th><th>Standard</th></tr>"
    "<tr><td>Reset supply air</td><td>ASHRAE G36 (loop tuning) / PNNL Re-tuning</td></tr></table>"
)
_VERDICT = (
    "<table><tr><th>Severity</th><th>Summary</th></tr><tr><td>warn</td>"
    "<td>AHU: SAT median 55.0&deg;F vs G36 target 60.1&deg;F; below target 80% of hours</td>"
    "</tr></table>"
)
_QUALIFIED = (
    "<p>AHU: SAT vs G36 default target; no site sequence known (reference only)</p>"
    "<p>economizer lockout per G36 §5.16.2.3</p>"
)


def test_citation_columns_are_not_g36_verdicts():
    sweep = _sweep()
    assert sweep.g36_verdicts(_PLAN) == []
    assert sweep.g36_verdicts(_ECM) == []
    assert sweep.g36_verdicts(_QUALIFIED) == []


def test_an_unqualified_g36_verdict_is_still_caught():
    sweep = _sweep()
    bad = sweep.g36_verdicts(_PLAN + _VERDICT)
    assert len(bad) == 1 and "vs G36 target" in bad[0]
    # a citation-looking phrase outside a citation column is judged like any other text
    assert sweep.g36_verdicts("<p>fails ASHRAE G36 / Std-55</p>")


def test_nested_tables_and_entities_keep_the_text():
    sweep = _sweep()
    html = (
        "<table><tr><th>Cite</th><th>Note</th></tr><tr><td>G36 &amp; 90.1</td>"
        "<td><table><tr><th>x</th></tr><tr><td>vs G36 target</td></tr></table></td></tr></table>"
    )
    stripped = sweep._without_citations(html)
    assert "90.1" not in stripped and "vs G36 target" in stripped
    assert len(sweep.g36_verdicts(html)) == 1
