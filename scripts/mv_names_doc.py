#!/usr/bin/env python3
# ruff: noqa: E501
"""Regenerate the "Method correspondence" section of docs/MANDV.md from the M&V names registry.

The section (from its ``## Method correspondence`` heading to the next ``## `` heading) is written
from ``camber/mandv/names.py`` and the citations in ``camber/references.py``, so the page always
gives each model and method the label, standard name, citation and differences the reports use.
Run it after changing the registry; ``--check`` exits 1 (without writing) when the section is out
of date, which ``tests/test_mv_names.py`` and ``scripts/gates.sh`` assert too.

    python scripts/mv_names_doc.py          # rewrite the section
    python scripts/mv_names_doc.py --check  # verify only
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.mandv import names as N  # noqa: E402
from camber.references import MV_REFERENCES, REFERENCES  # noqa: E402

DOC = os.path.join(ROOT, "docs", "MANDV.md")
HEADING = "## Method correspondence\n"

_INTRO = """\
Every M&V model kind, method and function, by the label CAMBER's reports show, the **standard
name** to report it under, the citation and the **known differences** from that standard. The
table is generated from the registry `camber.mandv.names` (by `scripts/mv_names_doc.py`), which
the reports, the `mv_baseline` summary and the chart legends read too; the `kind` codes in data
and JSON are unchanged. An empty standard name marks a CAMBER construct with no standard
counterpart. See [Naming and citations](#naming-and-citations) for how to cite them.
"""

_OUTRO = """\
**The `caltrack_*` functions are not CalTRACK.** `caltrack_savings` is a daily change-point
baseline (`best_model`, 2P–5P by BIC) with IPMVP avoided energy and a G14 band; it does not apply
CalTRACK 2.0 Daily's candidate models, balance-point grid, qualifying tests, adjusted-R² selection
or data-sufficiency rules, so its numbers are not eemeter-comparable. `caltrack_savings_hourly`
is a pooled TOWT, not CalTRACK Hourly:

| CalTRACK Hourly | CAMBER `mandv.towt` |
|---|---|
| per-calendar-month segmented models, weighted | one pooled model over the baseline |
| six **fixed** temperature bin edges | `n_temp_segments` **quantile-spaced** breakpoints |
| occupancy from a month × hour-of-week lookup, from a preliminary daily model's residuals | occupancy from bin-mean load vs the median of bin means |
| 365-day data sufficiency, hard limits enforced | hour count **plus** per-hour-of-week-bin coverage |

Both are being renamed in 0.105, when faithful CalTRACK 2.0 Daily, Billing and Hourly methods
take the `caltrack_*` names (#156). Until then they are defensible IPMVP Option-C savings on a
published baseline model, reported under the names in the table above.

**Expect an hourly band comparable to the daily method, not tighter.** Hourly residuals are
strongly serially correlated (ρ≈0.85 is ordinary), and the effective-sample-size correction
bites: measured on a 20-week synthetic, n=3360 hours becomes n_eff=282 and the band widens from
1.6% at ρ=0 to 9.7% at ρ=0.85. Finer data does not buy proportionally more certainty.

**Billing/monthly** data use the same change-point models on monthly data (the looser CV(RMSE)
tier), fitted per bill and weighted by days, or the variable-base degree-day models of
`mandv.basetemp` (see [Billing data](#billing-data)).
"""


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ") or "—"


#: short link texts for the table's Citation column (the References list below it gives each in
#: full)
_SHORT = {
    "ashrae-g14-2014": "ASHRAE G14-2014",
    "ashrae-rp1050-imt": "Kissock et al. 2003 (RP-1050 IMT)",
    "fels-1986-prism": "Fels 1986 (PRISM)",
    "mathieu-2011-towt": "Mathieu et al. 2011",
    "granderson-2015-baselines": "Granderson et al. 2015",
    "lbnl-187225": "LBNL-187225",
    "lbnl-6963e": "LBNL-6963E",
    "caltrack-2": "CalTRACK 2.0",
    "evo-ipmvp-core-2022": "IPMVP Core Concepts 2022",
    "doe-sep-mv-2019": "SEP 50001 M&V Protocol 2019",
}

_HEADINGS = {
    "change-point model": "Change-point models",
    "degree-day model": "Degree-day models",
    "other baseline model": "Other baseline models",
    "savings method": "Savings methods (SEP 50001 M&V Protocol §6.2; the config's `mv.method`)",
    "IPMVP option": "IPMVP options",
    "savings function": "Whole-building savings functions",
    "tracking and forecasting": "Tracking and forecasting",
}


def _cite(ids) -> str:
    return "; ".join(f"[{_SHORT.get(i, i)}]({REFERENCES[i].url})" for i in ids) or "—"


def render_section() -> str:
    """The whole "Method correspondence" section, heading included."""
    out = [HEADING, _INTRO]
    for group in N.GROUPS:
        rows = [r for r in N.MV_NAMES.values() if r.group == group]
        if not rows:
            continue
        out.append(f"**{_HEADINGS[group]}**\n")
        out.append("| Label | CAMBER | Standard name | Citation | Differences |")
        out.append("|---|---|---|---|---|")
        for r in rows:
            fn = r.callable.replace("camber.", "", 1)
            code = f"`{fn}`" if r.code == fn.rsplit(".", 1)[-1] else f"`{r.code}`, `{fn}`"
            out.append(
                f"| {_cell(r.label)} | {code} | {_cell(r.standard_name)} | "
                f"{_cite(r.citation_id)} | {_cell(r.differences)} |"
            )
        out.append("")
    out.append("**References**\n")
    for rid in MV_REFERENCES:
        ref = REFERENCES[rid]
        out.append(f"- `{rid}`: {ref.citation()} <{ref.url}>")
    out.append("")
    out.append(_OUTRO)
    return "\n".join(out)


def splice(text: str, section: str) -> str:
    """``text`` with its Method correspondence section replaced by ``section``."""
    start = text.index(HEADING)
    end = text.index("\n## ", start + len(HEADING)) + 1
    return text[:start] + section.rstrip("\n") + "\n\n" + text[end:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 if out of date")
    args = ap.parse_args(argv)
    with open(DOC, encoding="utf-8") as fh:
        text = fh.read()
    new = splice(text, render_section())
    if new == text:
        print("docs/MANDV.md Method correspondence is up to date.")
        return 0
    if args.check:
        print(
            "docs/MANDV.md Method correspondence is out of date: run scripts/mv_names_doc.py",
            file=sys.stderr,
        )
        return 1
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("rewrote the Method correspondence section of docs/MANDV.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
