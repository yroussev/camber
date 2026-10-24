"""The built-in prompts (0.110, #278): packaged workflows over the read-only capabilities.

Each prompt renders one user message that names the capabilities to call and in what order,
asks for every statement to cite the fact ids those calls return (``[F1@rule_run]``), and ends
with a :mod:`check_citations <camber.capabilities._cap_check_citations>` call on the draft. The
argument values are reduced to one short line each, so a value cannot smuggle instructions in
on new lines.
"""

from __future__ import annotations

import json
import re

from . import CapabilityError, Prompt, PromptArg

_CITE_RULES = (
    "Rules for the answer: state only what the tool results say. End every sentence with the "
    "fact id(s) it rests on in square brackets, exactly as the results give them, token "
    "included (for example [F2@rule_run-1a2b3c]; each call's ids carry their own token). "
    "Copy every number exactly as a cited fact states it and state no number "
    "that is not in a fact you cite. A declined check, a skipped rule or an unevaluated "
    "equipment is not a pass: say it was not judged. If a call is refused (not_allowed, "
    "refused_licence), say so and stop; do not work around it."
)

_FINISH = (
    "Finally, call check_citations with your draft as text and, as facts, every 'facts' array "
    "the calls above returned (combined). Fix or remove each sentence it does not mark "
    "supported, then give the corrected text."
)


def _arg(args: dict, name: str, *, required: bool = True, default: str = "") -> str:
    """One argument as a single short line (whitespace collapsed, at most 200 characters)."""
    raw = (args or {}).get(name)
    value = re.sub(r"\s+", " ", str(raw if raw is not None else "")).strip()[:200]
    if required and not value:
        raise CapabilityError("invalid_params", f"the prompt needs the argument {name!r}")
    return value or default


def _j(**arguments) -> str:
    """Capability arguments as the JSON a model passes."""
    return json.dumps(arguments)


def _message(*parts: str) -> list:
    return [{"role": "user", "text": "\n\n".join(p for p in parts if p)}]


def _steps(*steps: str) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))


def triage_ahu(args: dict) -> list:
    fid, equip = _arg(args, "facility_id"), _arg(args, "equip")
    return _message(
        f"Triage the air handler {equip} at facility {fid}: what is wrong, how sure we are, and "
        "what to do first.",
        _steps(
            f"Call points with {_j(facility_id=fid, equip=equip)} to see what it trends.",
            f"Call sensor_trust with {_j(facility_id=fid, equips=[equip])}; a fault resting on "
            "an untrusted input is a sensor problem first.",
            f"Call rule_run with {_j(facility_id=fid, equips=[equip])}.",
            "For each fault or warn finding (at most five, worst first), call finding_detail "
            "with its finding_id and the same facility_id and equips.",
            "Write a short triage: the likely causes ranked, the evidence hours, which inputs "
            "are suspect, and the first checks to make on site.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def rcx_executive_summary(args: dict) -> list:
    fid = _arg(args, "facility_id")
    return _message(
        f"Draft the executive summary of the retro-commissioning (RCx) report for facility {fid}.",
        _steps(
            f"Call rcx_report with {_j(facility_id=fid)}.",
            "Write one or two paragraphs for a building owner: how many issues, the top issues "
            "with their equipment and cost (or why they are uncosted), what is conditional on a "
            "sensor fix, and how much of the data the verdicts rest on. Cite the report's own "
            "fact ids (K1@summary-<token>, C1@<issue key>-<token>, ...) or the F ids of the "
            "result.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def explain_finding(args: dict) -> list:
    fid, finding = _arg(args, "facility_id"), _arg(args, "finding_id")
    return _message(
        f"Explain the finding {finding} at facility {fid} to a facilities technician.",
        _steps(
            f"Call finding_detail with {_j(facility_id=fid, finding_id=finding)} "
            "(if it is not found, call rule_run for the facility and pick the finding id from "
            "its results).",
            "Call rule_params with the finding's rule to read what each parameter means and "
            "where its default comes from.",
            f"Call sensor_trust with {_j(facility_id=fid)} and equips set to the finding's "
            "equipment, to check the inputs it rests on.",
            "Explain in plain words what the rule checks, what it saw (the hours and the time "
            "spans), the threshold it used, how far the inputs can be trusted, and which "
            "evidence chart shows it.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def check_savings_claim(args: dict) -> list:
    fid = _arg(args, "facility_id")
    claim = _arg(args, "claim", required=False, default="")
    about = f' The claim to check: "{claim}".' if claim else ""
    return _message(
        f"Check a savings claim for the M&V project configured for facility {fid}.{about}",
        _steps(
            f"Call mv_summary with {_j(facility_id=fid)}.",
            "Compare the claim with the mv_savings results: the avoided energy, the savings %, "
            "the uncertainty (fsu) and the baseline fit statistics. Note any declined result "
            "and its reason, any rebaseline trigger, and whether the reporting period lies "
            "outside the baseline's conditions.",
            "Give a verdict: supported, partly supported, or not supported by the M&V results, "
            "and what would be needed to support it.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def data_quality_review(args: dict) -> list:
    fid = _arg(args, "facility_id")
    return _message(
        f"Review the data quality of facility {fid} before anyone relies on its analytics.",
        _steps(
            f"Call coverage with {_j(facility_id=fid, group_by='equip')}, then again for the "
            "worst equipment with their equips to see the points and gaps.",
            f"Call sensor_trust with {_j(facility_id=fid)}.",
            "Write a review: overall coverage, the equipment and points with the worst gaps, "
            "the untrusted or suspect sensors and their flags, and what to fix first (trend "
            "setup, sensor calibration or replacement).",
        ),
        _CITE_RULES,
        _FINISH,
    )


def compare_equipment(args: dict) -> list:
    fid, equips = _arg(args, "facility_id"), _arg(args, "equips")
    names = [e.strip() for e in equips.split(",") if e.strip()][:20]
    if len(names) < 2:
        raise CapabilityError("invalid_params", "equips needs two or more comma-separated ids")
    return _message(
        f"Compare these equipment at facility {fid}: {', '.join(names)}.",
        _steps(
            f"Call coverage with {_j(facility_id=fid, equips=names, group_by='equip')}.",
            f"Call sensor_trust with {_j(facility_id=fid, equips=names)}.",
            f"Call rule_run with {_j(facility_id=fid, equips=names, min_severity='ok')}.",
            "Compare them side by side: data coverage, sensor trust, and the findings each "
            "rule gave each unit (one unit failing where its peers pass is the signal). Name "
            "the unit to look at first and why.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def sensor_trust_review(args: dict) -> list:
    fid = _arg(args, "facility_id")
    return _message(
        f"List the sensors at facility {fid} that need attention, for a calibration work order.",
        _steps(
            f"Call sensor_trust with {_j(facility_id=fid)}, and again with "
            f"{_j(facility_id=fid, view='report')} to see the points judged in their operating "
            "state.",
            f"Call rule_run with {_j(facility_id=fid)} to see which findings rest on suspect "
            "inputs (their caveats name untrusted roles).",
            "List each sensor below the trust floor or suspect, with its equipment, role, "
            "score, the flags that explain it, and what it blocks. Order by impact.",
        ),
        _CITE_RULES,
        _FINISH,
    )


def plan_site_visit(args: dict) -> list:
    fid = _arg(args, "facility_id")
    return _message(
        f"Plan a site visit to facility {fid}: what to look at, in what order, and what to bring.",
        _steps(
            f"Call rcx_report with {_j(facility_id=fid)} for the ranked issues.",
            f"Call sensor_trust with {_j(facility_id=fid)} for the sensors to verify.",
            "For the top three issues, call finding_detail on one finding of each (rule_run "
            "lists the finding ids) to get the time spans to reproduce on site.",
            "Write the plan: a walk order by equipment, for each stop what to check and what "
            "reading would confirm or rule out the issue, the sensors to spot-check with a "
            "reference instrument, and the trends to pull before leaving.",
        ),
        _CITE_RULES,
        _FINISH,
    )


_FID = PromptArg("facility_id", "a facility_id from the facilities capability", True)

PROMPTS = (
    Prompt(
        "triage_ahu",
        "Triage an air handler",
        "Find what is wrong with one air handler: its points, sensor trust, rule findings and "
        "the evidence behind the worst of them, ranked, with cited facts.",
        (_FID, PromptArg("equip", "the air handler's equipment id (see points)", True)),
        triage_ahu,
    ),
    Prompt(
        "rcx_executive_summary",
        "RCx executive summary",
        "Draft the executive summary of a facility's retro-commissioning report from its "
        "structured data and fact index, every sentence cited.",
        (_FID,),
        rcx_executive_summary,
    ),
    Prompt(
        "explain_finding",
        "Explain a finding",
        "Explain one rule finding in plain words: what the rule checks, what it saw and when, "
        "the threshold used and how far its inputs can be trusted.",
        (_FID, PromptArg("finding_id", "a finding_id from rule_run", True)),
        explain_finding,
    ),
    Prompt(
        "check_savings_claim",
        "Check a savings claim",
        "Check a claimed energy saving against the facility's configured M&V results (model "
        "fit, avoided energy, uncertainty, declines and triggers).",
        (_FID, PromptArg("claim", "the claim to check, in a sentence (optional)")),
        check_savings_claim,
    ),
    Prompt(
        "data_quality_review",
        "Data quality review",
        "Review a facility's data coverage, gaps and sensor trust, and say what to fix first.",
        (_FID,),
        data_quality_review,
    ),
    Prompt(
        "compare_equipment",
        "Compare equipment",
        "Compare two or more equipment of one facility side by side: coverage, sensor trust "
        "and the findings of each rule.",
        (_FID, PromptArg("equips", "two or more equipment ids, comma-separated", True)),
        compare_equipment,
    ),
    Prompt(
        "sensor_trust_review",
        "Sensor trust review",
        "List the sensors that need calibration or replacement, with the flags that explain "
        "each and the findings they block.",
        (_FID,),
        sensor_trust_review,
    ),
    Prompt(
        "plan_site_visit",
        "Plan a site visit",
        "Plan a site visit from the ranked RCx issues, the suspect sensors and the evidence "
        "time spans: a walk order with what to check at each stop.",
        (_FID,),
        plan_site_visit,
    ),
)
