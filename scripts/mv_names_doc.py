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
**CalTRACK 2.0 and CAMBER's own whole-building baselines.** `caltrack_daily_savings`,
`caltrack_billing_savings` and `caltrack_hourly_savings` implement the CalTRACK 2.0 methods as
published, with `eemeter_compat=True` to reproduce eemeter 2.10 (see
[CalTRACK 2.0 methods](#caltrack-20-methods)). `daily_changepoint_savings` and
`towt_savings_hourly` are CAMBER's own baselines, not CalTRACK: a daily change-point model
(`best_model`, 2P–5P by BIC) and a pooled TOWT, each with IPMVP avoided energy and a G14 band.
They were called `caltrack_savings` and `caltrack_savings_hourly` before 0.105; those names are
deprecated aliases with the same numbers (#156). The pooled TOWT differs from CalTRACK Hourly:

| CalTRACK Hourly (`caltrack_hourly_savings`) | CAMBER `mandv.towt` (`towt_savings_hourly`) |
|---|---|
| per-calendar-month segmented models, weighted | one pooled model over the baseline |
| six **fixed** temperature bin edges | `n_temp_segments` **quantile-spaced** breakpoints |
| occupancy from an hour-of-week lookup per month, from a preliminary model's residuals | occupancy from bin-mean load vs the median of bin means |
| > 90 % of each month's hours, per month segment | hour count **plus** per-hour-of-week-bin coverage |

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
#: full); the registry's, so the table and ``ensemble_description`` cite alike
_SHORT = N.SHORT_CITATIONS

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
