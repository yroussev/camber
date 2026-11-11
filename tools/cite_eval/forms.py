"""The grammar of forms, and the generated cases.

A *form* writes one clause (a single-claim form) or two (a pair form) as a draft: plain,
bullets, headings, bold labels, table rows, header columns, HTML comments, HTML tag attributes
(0112-cite10), numbers in words,
conditionals, existentials, time spans, coverage words, Unicode and id-spelling variants; and
for two units, two clauses, contrasts ("but", "but B does not", "unlike", "X, not Y"), status
lists, parenthetical lists, "respectively", figure lists, sets ("A and B both"), table rows and
bullets. Each form returns ``(draft, cores, region)`` or ``None`` when it cannot write the
clause; ``cores`` are the substrings that carry the claim (a claim is *kept* when every core is
still in ``verified_text``).

:func:`generate` builds every case: each true claim in each form (operator ``none``), each
mutant of it (:mod:`.mutate`), and for pairs of claims about two units the true pair, the
swapped pair, the over-generalised set and the pair with one side mutated. A case is a dict::

    {"key", "draft", "cites", "truth": "true"|"false", "form", "operator", "capability",
     "region", "cores", "claim", "group"}

``group`` ties a case to the true claim (and variant) it came from, for the metamorphic
relations. :func:`sample` draws a seeded sample that keeps every cell.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import replace

from .mutate import OPERATORS, World, mutants
from .realise import _VERDICT as _VERDICT_PREDS
from .realise import Clause, in_words, realise

NBH = "‑"  # non-breaking hyphen
NBSP = " "

SINGLE_FORMS = (
    "plain",
    "bullet",
    "heading",
    "bold_label",
    "table_row",
    "header_column",
    "html_comment",
    "html_attr",
    "numbers_in_words",
    "conditional",
    "existential",
    "time_span",
    "coverage_words",
    "unicode_hyphen",
    "id_spelling",
    "header_year",
    "header_yes_detail",
    "header_no_detail",
)
PAIR_FORMS = (
    "two_clause",
    "contrast_but",
    "contrast_but_not",
    "contrast_unlike",
    "contrast_x_not_y",
    "status_list",
    "paren_list",
    "respectively",
    "figure_list",
    "set_both",
    "table_rows",
    "bullets",
    # round 11 (C=split by default): the forms its nine corpus strips were written in
    "most_of",
    "x_not_y_bare",
    "unlike_neg",
    "comma_figure_list",
    "comparative",
    "rather_than_as",
    "unlike_point_after",
    "cited_items",
)
#: (round 11) a set followed by "their ... respectively": made of two claim pairs (_their_cases)
THEIR_FORM = "set_their_respectively"
REGION = {
    "bullet": "bullet",
    "heading": "heading",
    "bold_label": "bold",
    "table_row": "table",
    "header_column": "table_header",
    "header_year": "table_header",
    "header_yes_detail": "table_header",
    "header_no_detail": "table_header",
    "cell_parts": "table",
    "html_comment": "comment",
    "html_attr": "attribute",
    "table_rows": "table",
    "bullets": "bullet",
}


def cite(*ids) -> str:
    return " ".join(f"[{i}]" for i in ids)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _low(s: str) -> str:
    """A subject inside a sentence: its first letter lowered only for an ordinary word."""
    if s[:1].isupper() and (s.split() or [""])[0].isalpha() and s.split()[0] not in ("Span",):
        return s[:1].lower() + s[1:]
    return s


_PLURAL = {
    "fails": "fail",
    "has": "have",
    "is": "are",
    "warns": "warn",
    "passes": "pass",
    "gets": "get",
    "scores": "score",
    "was": "were",
    "reads": "read",
    "agrees": "agree",
    "disagrees": "disagree",
    "tracks": "track",
    "stores": "store",
    "counts": "count",
    "stays": "stay",
    "never": "never",
    "does": "do",
}


def _plural(pred: str) -> str:
    w = pred.split(" ", 1)
    head = _PLURAL.get(w[0], w[0])
    rest = w[1] if len(w) > 1 else ""
    if head == "never" and rest.startswith("moves"):
        rest = "move" + rest[5:]
    return (head + " " + rest).strip()


# ----------------------------------------------------------------------------- single forms


def single(form: str, claim, c: Clause):  # noqa: C901 - one branch per form
    """``(draft, cores, region)`` for one clause in ``form``, or ``None``."""
    ids = cite(*c.cites)
    region = REGION.get(form, "plain")
    check = (claim.extra or {}).get("check")
    if check and check[0] and form not in ("plain", "bullet", "heading", "html_comment"):
        return None  # "Verified: ..." opens a line, not a clause
    if form == "plain":
        s = f"{c.text} {ids}."
        return s, [s], region
    if form == "bullet":
        s = f"- {c.text} {ids}."
        return s, [c.text], region
    if form == "heading":
        s = f"## {c.text} {ids}"
        return s, [c.text], region
    if form == "bold_label":
        s = f"**{c.subject}:** {c.pred} {ids}."
        return s, [c.pred], region
    if form == "table_row":
        row = f"| {c.subject} | {c.pred} {ids} |"
        return f"| Unit | Result |\n|---|---|\n{row}", [row], region
    if form == "header_column":
        head = f"| Unit | {_cap(c.pred)} |"
        row = f"| {c.subject} | yes {ids} |"
        return f"{head}\n|---|---|\n{row}", [head, row], region
    if form == "header_year":  # (round 11) "| Meter | Against the 2024 baseline |"
        year = (claim.extra or {}).get("year")
        if not year or claim.attribute not in ("saving", "verdict", "reporting_days"):
            return None
        head = f"| Meter | Against the {year} baseline |"
        row = f"| {c.subject} | {c.pred} {ids} |"
        return f"{head}\n|---|---|\n{row}", [head, row], region
    if form in ("header_yes_detail", "header_no_detail"):
        # (round 11) "| Unit | Passes X? |" with "Yes: 43% of those samples" or, under the
        # opposite verdict's header, "No: ..."; the detail is a figure the same fact states
        detail = (claim.extra or {}).get("detail")
        if claim.attribute != "verdict" or claim.verdict not in _VERDICT_PREDS or not detail:
            return None
        if form == "header_yes_detail":
            head, ans = f"| Unit | {_cap(c.pred)}? |", "Yes"
        else:
            opp = "ok" if claim.verdict in ("fault", "warn") else "fault"
            head, ans = f"| Unit | {_cap(_VERDICT_PREDS[opp][0].format(r=claim.rule))}? |", "No"
        row = f"| {c.subject} | {ans}: {detail} {ids} |"
        return f"{head}\n|---|---|\n{row}", [head, row], region
    if form == "html_comment":
        s = f"<!-- {c.text} {ids}. -->"
        return s, [s], region
    if form == "html_attr":  # (0112-cite10) the claim in a tag's title, the subject shown
        s = f'<abbr title="{c.text}">{c.subject}</abbr> {ids}.'
        return s, [s], region
    if form == "numbers_in_words":
        if c.integer is None or not c.figure or str(c.integer) not in c.text:
            return None
        text = re.sub(rf"(?<![\d.]){c.integer}(?![\d.])", in_words(c.integer), c.text, count=1)
        if text == c.text:
            return None
        s = f"{_cap(text)} {ids}."
        return s, [s], region
    if form == "conditional":
        s = f"If the trends are right, {_low(c.subject)} {c.pred} {ids}."
        return s, [s], region
    if form == "existential":
        body = _existential(claim)
        if not body:
            return None
        s = f"{body} {ids}."
        return s, [s], region
    if form == "time_span":
        if claim.attribute not in ("flat", "span") or len(claim.window) != 2:
            return None
        a, b = claim.window
        when = f"on {a}" if a == b else f"from {a} to {b}"
        s = f"{c.text} {when} {ids}."
        return s, [s], region
    if form == "coverage_words":
        if claim.attribute == "coverage" and float(claim.value or 0) >= 100:
            pred = "was logged throughout the window"
        elif claim.attribute == "flat":
            pred = "is flat for the whole window"
        else:
            return None
        ex = claim.extra or {}
        if ex.get("neg") or ex.get("check"):
            return None
        s = f"{c.subject} {pred}{' ' + ex['period'] if ex.get('period') else ''} {ids}."
        return s, [s], region
    if form == "unicode_hyphen":
        if not re.search(r"[A-Za-z]-\d", c.text):
            return None
        s = re.sub(r"([A-Za-z])-(\d)", rf"\1{NBH}\2", c.text) + f" {ids}."
        return s, [s], region
    if form == "id_spelling":
        if not re.search(r"\bAHU-\d\b", c.text):
            return None
        s = re.sub(r"\bAHU-(\d)\b", r"AHU \1", c.text, count=1) + f" {ids}."
        return s, [s], region
    return None


def _existential(claim) -> str:
    ex = claim.extra or {}
    if ex.get("quant") or ex.get("check") or ex.get("site_subject"):
        return ""
    no = "no" if ex.get("neg") else "a"
    where = claim.unit
    if claim.attribute == "verdict" and claim.verdict in ("fault", "warn"):
        word = "fault" if claim.verdict == "fault" else "warning"
        tail = f" {ex['period']}" if ex.get("period") else ""
        return f"There is {no} {claim.rule} {word} on {where}{tail}"
    if claim.attribute == "trust_class" and claim.label in ("suspect", "untrusted"):
        return f"There is {no} {claim.label} {claim.point} on {where}"
    if claim.attribute == "flat":
        return f"There is {no} flat {claim.point} on {where}"
    return ""


# ----------------------------------------------------------------------------- pair forms


def _noun(c: Clause) -> str:
    """The figure's noun from the status ("trust 0.70" -> "trust")."""
    if c.figure and c.status.endswith(c.figure):
        return c.status[: -len(c.figure)].strip()
    return ""


def pair(form: str, a: Clause, b: Clause, differ: bool, same_value: bool):  # noqa: C901
    """``(draft, cores, region)`` for two clauses in ``form``, or ``None``. ``differ``: the
    two claims say different things of their units (contrasts need it); ``same_value``: they
    say the same thing (a set needs it)."""
    ids = cite(*a.cites, *b.cites)
    region = REGION.get(form, "plain")
    if form == "two_clause":
        s = f"{a.text} and {_low(b.subject)} {b.pred} {ids}."
    elif form == "contrast_but":
        if not differ:
            return None
        s = f"{a.text}, but {_low(b.subject)} {b.pred} {ids}."
    elif form == "contrast_but_not":
        if not differ or not a.neg_pred.startswith("does not"):
            return None
        s = f"{a.text}, but {_low(b.subject)} does not {ids}."
    elif form == "contrast_unlike":
        if not differ:
            return None
        s = f"Unlike {_low(b.subject)}, {_low(a.subject)} {a.pred} {ids}."
    elif form == "contrast_x_not_y":
        if not differ:
            return None
        s = f"{a.subject}, not {_low(b.subject)}, {a.pred} {ids}."
    elif form == "status_list":
        s = f"{a.subject}: {a.status}; {b.subject}: {b.status} {ids}."
    elif form == "paren_list":
        tail = f" {a.tail}" if a.tail and a.tail == b.tail else ""
        s = f"{a.subject} ({a.value_word}) and {_low(b.subject)} ({b.value_word}){tail} {ids}."
    elif form == "respectively":
        if not (a.pred_fig and a.pred_fig == b.pred_fig and a.figure and b.figure):
            return None
        pred = _plural(a.pred_fig.format(f"{a.figure} and {b.figure}"))
        s = f"{a.subject} and {_low(b.subject)} {pred}, respectively {ids}."
    elif form == "figure_list":
        noun = _noun(a)
        if not (noun and noun == _noun(b) and b.figure):
            return None
        s = f"{_cap(noun)} is {a.figure} on {a.subject} and {b.figure} on {_low(b.subject)} {ids}."
    elif form == "set_both":
        if not same_value:
            return None
        s = f"{a.subject} and {_low(b.subject)} both {_plural(a.pred)} {ids}."
    elif form == "most_of":
        if not same_value:
            return None
        s = f"Most of {a.subject} and {_low(b.subject)} {_plural(a.pred)} {ids}."
    elif form == "x_not_y_bare":
        if not (a.bare and b.bare and a.bare != b.bare):
            return None
        s = f"{a.subject}, not {_low(b.subject)}, {a.bare} {ids}."
    elif form == "unlike_neg":
        if not differ or not a.neg_pred or a.neg_pred == "moves":
            return None
        s = f"{_cap(b.subject)} {a.neg_pred}, unlike {_low(a.subject)} {ids}."
    elif form == "comma_figure_list":
        if not (a.pred_fig and a.pred_fig == b.pred_fig and a.figure and b.figure):
            return None
        s = f"{a.text}, {_low(b.subject)} {b.figure} {ids}."
    elif form == "comparative":
        word = _comparative(a, b)
        if not word:
            return None
        s = f"{a.subject} ({a.figure}) {word} than {_low(b.subject)} ({b.figure}) {ids}."
    elif form == "rather_than_as":
        va, _, ta = a.pred.partition(" ")
        vb, _, tb = b.pred.partition(" ")
        if not differ or not ta or ta != tb or va == vb or not re.fullmatch(r"[a-z]+s", vb):
            return None
        bs = b.subject
        if "'s " in bs and a.subject.split("'s ", 1)[1:] == bs.split("'s ", 1)[1:]:
            bs = bs.split("'s ", 1)[0] + "'s"  # "as AHU-2's does"
        s = f"{a.subject} {a.pred} rather than {_ing(vb)}, as {_low(bs)} does {ids}."
    elif form == "unlike_point_after":
        # "AHU-1 supply_air_temp disagrees with its reference, unlike AHU-2's": the point after
        # the unit without "'s", and the other unit's point said by "B's" alone; full coverage
        # in words where the figure is 100% (the frozen table has no partial coverage to set
        # against it, so the coverage side is in the corpus only)
        ma = re.fullmatch(r"(.+)'s (\S+)", a.subject)
        mb = re.fullmatch(r"(.+)'s (\S+)", b.subject)
        if not differ or not (ma and mb and ma.group(2) == mb.group(2)):
            return None
        pred = a.pred
        if _noun(a) == "coverage" and _num(a.figure) == 100.0:
            pred = "has full coverage"
        s = f"{ma.group(1)} {ma.group(2)} {pred}, unlike {_low(mb.group(1))}'s {ids}."
    elif form == "cited_items":
        # (round 11) "The ones that fail X are A [F1] and B [F2]": each item its own citation
        if not same_value:
            return None
        s = (
            f"The ones that {_plural(a.pred)} are {a.subject} {cite(*a.cites)} and "
            f"{_low(b.subject)} {cite(*b.cites)}."
        )
        return s, [s], region
    elif form == "table_rows":
        ra = f"| {a.subject} | {a.pred} {cite(*a.cites)} |"
        rb = f"| {b.subject} | {b.pred} {cite(*b.cites)} |"
        return f"| Unit | Result |\n|---|---|\n{ra}\n{rb}", [ra, rb], region
    elif form == "bullets":
        la = f"- {a.text} {cite(*a.cites)}."
        lb = f"- {b.text} {cite(*b.cites)}."
        return f"{la}\n{lb}", [a.text, b.text], region
    else:
        return None
    return s, [s], region


def _num(fig: str):
    m = re.match(r"\s*([-+−]?)(\d[\d,]*(?:\.\d+)?)", fig or "")
    if m is None:
        return None
    v = float(m.group(2).replace(",", ""))
    return -v if m.group(1) in ("-", "−") else v


def _comparative(a: Clause, b: Clause) -> str:
    """(round 11) How ``a``'s figure compares with ``b``'s: "is more suspect" (a trust that is
    lower), "is more trusted", "has a higher bias", ...; "" when the two do not compare."""
    noun = _noun(a)
    x, y = _num(a.figure), _num(b.figure)
    if not noun or noun != _noun(b) or x is None or y is None or x == y:
        return ""
    if noun == "trust":
        return "is more suspect" if x < y else "is more trusted"
    return f"has a {'higher' if x > y else 'lower'} {noun}"


_FLIP = (("more suspect", "more trusted"), ("higher", "lower"))


def _flip_comparative(draft: str) -> str:
    for x, y in _FLIP:
        if x in draft:
            return draft.replace(x, y, 1)
        if y in draft:
            return draft.replace(y, x, 1)
    return draft


def _ing(verb: str) -> str:
    """ "agrees" -> "agreeing", "passes" -> "passing", "fails" -> "failing"."""
    base = re.sub(r"(?:(?<=ss)|(?<=sh)|(?<=ch)|(?<=x))es$|s$", "", verb)
    if base.endswith("e") and not base.endswith("ee"):
        base = base[:-1]
    return base + "ing"


# ----------------------------------------------------------------------------- generation


def _key(*parts) -> str:
    return hashlib.sha1("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]


def _case(draft, cores, region, *, truth, form, operator, capability, cites, claim, group):
    return {
        "key": _key(draft, "|".join(cites)),
        "draft": draft,
        "cites": list(dict.fromkeys(cites)),
        "truth": truth,
        "form": form,
        "operator": operator,
        "capability": capability,
        "region": region,
        "cores": cores,
        "claim": claim,
        "group": group,
    }


def _pair_groups(claims: list) -> dict:
    groups: dict = {}
    for c in claims:
        if not c.unit or "@" not in c.fact_id:
            continue
        if c.attribute in ("rank", "issue_hours", "licence", "param", "reporting_days"):
            continue
        k = (c.attribute, c.point, c.rule, c.label, c.munit)
        groups.setdefault(k, []).append(c)
    return groups


def _thin(claims: list) -> list:
    """The claims to write: all of them, but at most two point coverages per unit and three
    datasets (the rest repeat the same shapes and would outweigh every other capability)."""
    out, seen = [], {}
    for c in claims:
        if c.attribute == "coverage" and c.point:
            k = ("pcov", c.unit)
        elif c.attribute == "licence":
            k = ("licence",)
        else:
            out.append(c)
            continue
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= (2 if k[0] == "pcov" else 3):
            out.append(c)
    return out


def generate(claims: list, *, variants: int = 3) -> list:
    """Every case the forms and operators make of ``claims`` (deterministic order). The
    operators know every claim; the forms write :func:`_thin`'s."""
    world = World(claims)
    claims = _thin(_with_details(claims))
    out: list = []
    seen: set = set()

    def add(case):
        if case["key"] not in seen:
            seen.add(case["key"])
            out.append(case)

    for idx, claim in enumerate(claims):
        for variant in range(variants):
            c = realise(claim, variant)
            if c is None:
                continue
            group = f"c{idx}"
            forms = SINGLE_FORMS if variant == 0 else ("plain",)
            for form in forms:
                w = single(form, claim, c)
                if w is None:
                    continue
                add(
                    _case(
                        *w,
                        truth="true",
                        form=form,
                        operator="none",
                        capability=claim.capability,
                        cites=c.cites,
                        claim=c.text,
                        group=f"{group}v{variant}",
                    )
                )
            for op in OPERATORS:
                for m in mutants(claim, op, world):
                    mc = realise(m, variant)
                    if mc is None:
                        continue
                    # a year off is said only by the header that gives it (round 11)
                    op_forms = ("header_year",) if op == "shift_year" else forms
                    if mc.text == c.text and op != "shift_year":
                        continue
                    for form in op_forms:
                        if form not in forms and variant:
                            continue
                        w = single(form, m, mc)
                        if w is None:
                            continue
                        add(
                            _case(
                                *w,
                                truth="false",
                                form=form,
                                operator=op,
                                capability=claim.capability,
                                cites=mc.cites,
                                claim=mc.text,
                                group=f"{group}v{variant}",
                            )
                        )
    out.extend(c for c in _pairs(claims, world) if c["key"] not in seen and not seen.add(c["key"]))
    return out


def _with_details(claims: list) -> list:
    """(round 11) Each claim with ``extra["detail"]``: a figure its own fact states, as the
    realiser writes it ("43% of those samples"), for the yes/no-with-details cells."""
    detail: dict = {}
    for cl in claims:
        c = realise(cl, 0)
        if c is not None and c.figure and cl.attribute in ("share", "hours", "trust", "coverage"):
            fig = f"{c.figure} of {cl.label}" if cl.attribute == "share" and cl.label else c.figure
            detail.setdefault(cl.fact_id, fig)
    return [
        replace(cl, extra={**(cl.extra or {}), "detail": detail[cl.fact_id]})
        if cl.fact_id in detail
        else cl
        for cl in claims
    ]


def _cell_parts_cases(claims: list, world: World) -> list:
    """(round 11) A table cell of two parts, each citing its own fact: "| AHU-1's oa_damper |
    has a trust of 0.73 [F4]; has 100.0% coverage [P14] |". The second part's figure perturbed
    is the false case."""
    by_subject: dict = {}
    for cl in claims:
        c = realise(cl, 0)
        if c is not None and c.figure and cl.unit and "@" in cl.fact_id:
            by_subject.setdefault(c.subject, []).append((cl, c))
    out: list = []
    for subject, items in sorted(by_subject.items()):
        items = sorted(items, key=lambda x: (x[0].fact_id, x[1].pred))
        for i, (a, ca) in enumerate(items):
            for b, cb in items[i + 1 :]:
                if a.fact_id == b.fact_id or a.capability == b.capability:
                    continue
                head = f"| Unit | {_cap(_noun(ca) or 'value')} and {_noun(cb) or 'value'} |"
                cap = f"{a.capability}+{b.capability}"
                group = _key("cell", a.fact_id, b.fact_id, subject)
                variants = [("true", "none", cb)]
                for m in mutants(b, "perturb_figure", world)[:1]:
                    mb = realise(m, 0)
                    if mb is not None and mb.pred != cb.pred:
                        variants.append(("false", "perturb_figure", mb))
                for truth, op, xb in variants:
                    row = (
                        f"| {subject} | {ca.pred} {cite(*ca.cites)}; {xb.pred} {cite(*cb.cites)} |"
                    )
                    out.append(
                        _case(
                            f"{head}\n|---|---|\n{row}",
                            [row],
                            "table",
                            truth=truth,
                            form="cell_parts",
                            operator=op,
                            capability=cap,
                            cites=[*ca.cites, *cb.cites],
                            claim=f"{ca.text} | {xb.text}",
                            group=group,
                        )
                    )
                break  # one pair per claim is enough
    return out


def _their_cases(claims: list, world: World) -> list:
    """(round 11) "A and B both P, and their N are x and y respectively": a claim true of both
    units, then a figure pair of the same two; the figures swapped is the false case."""
    groups = _pair_groups(claims)
    same: dict = {}  # (unit, unit) -> a claim pair true of both
    for _gk, ms in sorted(groups.items(), key=lambda kv: str(kv[0])):
        for c in sorted(ms, key=lambda c: (c.unit, c.fact_id)):
            for d in sorted(ms, key=lambda c: (c.unit, c.fact_id)):
                if c.unit < d.unit and not c.point and not d.point:
                    if world.true(replace(c, unit=d.unit)) and world.true(replace(d, unit=c.unit)):
                        same.setdefault((c.unit, d.unit), (c, d))
    out: list = []
    for _gk, ms in sorted(groups.items(), key=lambda kv: str(kv[0])):
        ms = sorted(ms, key=lambda c: (c.unit, c.fact_id))
        for i, a in enumerate(ms):
            for b in ms[i + 1 :]:
                if (a.unit, b.unit) not in same or a.value == b.value:
                    continue
                c, d = same[(a.unit, b.unit)]
                ca, cb, cc, cd = (realise(x, 0) for x in (a, b, c, d))
                if None in (ca, cb, cc, cd) or not (ca.figure and cb.figure):
                    continue
                noun = _noun(ca)
                if not noun or noun != _noun(cb) or c.fact_id in (a.fact_id, b.fact_id):
                    continue
                np_ = f"{a.point} {noun}" if a.point and a.point == b.point else noun
                np_ += "es" if np_.endswith("s") else "s"
                lead = f"{cc.subject} and {_low(cd.subject)} both {_plural(cc.pred)}"
                ids = cite(*cc.cites, *cd.cites, *ca.cites, *cb.cites)
                cap = (
                    a.capability
                    if a.capability == c.capability
                    else f"{c.capability}+{a.capability}"
                )
                group = _key("their", c.fact_id, d.fact_id, a.fact_id, b.fact_id)
                for truth, op, x, y in (
                    ("true", "none", ca.figure, cb.figure),
                    ("false", "swap_pair", cb.figure, ca.figure),
                ):
                    s = f"{lead}, and their {np_} are {x} and {y} respectively {ids}."
                    out.append(
                        _case(
                            s,
                            [s],
                            "plain",
                            truth=truth,
                            form=THEIR_FORM,
                            operator=op,
                            capability=cap,
                            cites=[*cc.cites, *cd.cites, *ca.cites, *cb.cites],
                            claim=f"{cc.text} | {cd.text} | {ca.text} | {cb.text}",
                            group=group,
                        )
                    )
    return out


_CITED = re.compile(r"\[([A-Z]+\d+)@[^\]\s]+\]")


def _short_id_cases(claims: list) -> list:
    """(round 13) Each claim's plain sentence with its ids shortened ("[F2]" for
    "[F2@rule_run-1a2b3c]"): a bare id is no citation, so the sentence is false as cited."""
    out: list = []
    for idx, claim in enumerate(claims):
        c = realise(claim, 0)
        w = single("plain", claim, c) if c is not None else None
        if w is None or not _CITED.search(w[0]):
            continue
        draft = _CITED.sub(r"[\1]", w[0])
        cores = [_CITED.sub(r"[\1]", x) for x in w[1]]
        out.append(
            _case(
                draft,
                cores,
                w[2],
                truth="false",
                form="plain",
                operator="shorten_id",
                capability=claim.capability,
                cites=c.cites,
                claim=c.text,
                group=f"c{idx}v0",
            )
        )
    return out


_HEDGE_WORDS = re.compile(r"\b(?:may|might|could|can|unless|if|whether|possibly|ruled out)\b")


def _caveat_cases(claims: list) -> list:
    """(round 13) A caveat a rule gave for some of its results, said of the units it names
    (true) and of the rule as a whole (false: "economizer_high_limit: 19 sample(s) ...")."""
    out: list = []
    for claim in claims:
        if claim.attribute != "caveat":
            continue
        units = list((claim.extra or {}).get("units") or [])
        said = claim.label.rstrip(" .;")
        cites = (claim.fact_id,)
        group = _key("caveat", claim.fact_id)
        named = " and ".join(units)
        variants = [
            ("true", "none", f"- On {named}, the {claim.rule} caveat: {said} {cite(*cites)}."),
            ("true", "none", f"- {named}: {said} {cite(*cites)}."),
        ]
        if not _HEDGE_WORDS.search(said):  # a hedge said of the rule reports the caveat
            variants += [
                ("false", "overgeneralise", f"- {claim.rule}: {said} {cite(*cites)}."),
                ("false", "overgeneralise", f"- The {claim.rule} check: {said} {cite(*cites)}."),
            ]
        for truth, op, draft in variants:
            out.append(
                _case(
                    draft,
                    [draft],
                    "bullet",
                    truth=truth,
                    form="caveat_scope",
                    operator=op,
                    capability=claim.capability,
                    cites=list(cites),
                    claim=said,
                    group=group,
                )
            )
    return out


#: (round 14) the other unit a total could be given in
_OTHER_ENERGY = {"kBtu": "kWh", "kWh": "kBtu", "MMBtu": "kBtu", "Btu": "kBtu", "MWh": "kWh"}


def _total_fig(value, munit: str, sign: str = "") -> str:
    v = float(value)
    size = f"{v:,.0f}" if v >= 100 else f"{round(v, 2):g}"
    return f"{sign}${size}" if munit == "$" else f"{sign}{size} {munit}"


def _total_cases(claims: list) -> list:
    """(round 14) A finding's energy or cost total, said with what it is a total of: true as
    the fact gives it, false with another figure, another unit, or the figure of another total
    the fact lists (the cooling total said as the heating one)."""
    out: list = []
    totals = [c for c in claims if c.attribute == "total"]
    for claim in totals:
        cites = (claim.fact_id,)
        group = _key("total", claim.fact_id, claim.label)
        side = " ".join(w for w in claim.label.split() if w not in ("oa", "cost"))
        fig = _total_fig(claim.value, claim.munit, claim.sign)
        variants = [
            ("true", "none", f"{claim.unit}'s {claim.label} total is {fig} {cite(*cites)}."),
            (
                "true",
                "none",
                f"On {claim.unit}, {claim.rule} gives {side} of {fig} {cite(*cites)}.",
            ),
        ]
        bumped = float(claim.value) * 1.1 + 1
        variants.append(
            (
                "false",
                "perturb_figure",
                f"{claim.unit}'s {claim.label} total is "
                f"{_total_fig(bumped, claim.munit, claim.sign)} {cite(*cites)}.",
            )
        )
        if claim.munit in _OTHER_ENERGY:
            other = _total_fig(claim.value, _OTHER_ENERGY[claim.munit], claim.sign)
            variants.append(
                (
                    "false",
                    "swap_munit",
                    f"{claim.unit}'s {claim.label} total is {other} {cite(*cites)}.",
                )
            )
        for sib in totals:
            if (
                sib.fact_id == claim.fact_id
                and sib.label != claim.label
                and sib.munit == claim.munit
                and float(sib.value) != float(claim.value)
            ):
                wrong = _total_fig(sib.value, sib.munit, sib.sign)
                variants.append(
                    (
                        "false",
                        "swap_item",
                        f"On {claim.unit}, {claim.rule} gives {side} of {wrong} {cite(*cites)}.",
                    )
                )
        for truth, op, draft in variants:
            out.append(
                _case(
                    draft,
                    [draft],
                    "plain",
                    truth=truth,
                    form="total",
                    operator=op,
                    capability=claim.capability,
                    cites=list(cites),
                    claim=f"{claim.label} {fig}",
                    group=group,
                )
            )
    return out


#: (round 14) inputs no rule of the fact table reads
_NOT_READ = ("condenser_water_temp", "zone_co2", "duct_static_pressure")


def _input_cases(claims: list) -> list:
    """(round 14) What a rule needs, from its inputs fact: true for an input it needs (by its id
    and in words), false for one it only reads when mapped, or one it does not read at all."""
    out: list = []
    by_fact: dict = {}
    for c in claims:
        if c.attribute in ("input_needs", "input_reads"):
            by_fact.setdefault(c.fact_id, []).append(c)
    for fact_id, cs in sorted(by_fact.items()):
        rule = cs[0].rule
        need = [c.label for c in cs if c.attribute == "input_needs"]
        read = [c.label for c in cs if c.attribute == "input_reads"]
        cites = (fact_id,)
        variants = []
        for role in need:
            variants += [
                ("true", "none", f"{rule} needs {role} {cite(*cites)}."),
                (
                    "true",
                    "none",
                    f"The {rule} rule needs the {role.replace('_', ' ')} {cite(*cites)}.",
                ),
            ]
        for role in read[:4]:
            variants += [
                ("true", "none", f"{rule} reads {role} when it is mapped {cite(*cites)}."),
                ("false", "optional_as_need", f"{rule} needs {role} {cite(*cites)}."),
                (
                    "false",
                    "optional_as_need",
                    f"The {rule} rule needs the {role.replace('_', ' ')} {cite(*cites)}.",
                ),
            ]
        for role in _NOT_READ:
            if role not in need + read:
                variants.append(("false", "inject_input", f"{rule} needs {role} {cite(*cites)}."))
        for truth, op, draft in variants:
            out.append(
                _case(
                    draft,
                    [draft],
                    "plain",
                    truth=truth,
                    form="rule_inputs",
                    operator=op,
                    capability=cs[0].capability,
                    cites=list(cites),
                    claim=draft,
                    group=_key("inputs", fact_id),
                )
            )
    return out


def _rmse_cases(claims: list) -> list:
    """(round 14) A reference comparison's RMSE in the point's unit: true as the fact gives
    it, false with another figure or the other temperature unit, and false said as the bias
    where the fact's bias is another figure."""
    out: list = []
    bias = {(c.fact_id, c.point): c for c in claims if c.attribute == "rmse_bias"}
    for claim in (c for c in claims if c.attribute == "rmse"):
        cites = (claim.fact_id,)
        fig = f"{float(claim.value):.1f} {claim.munit}"
        other = "°C" if claim.munit == "°F" else "°F"
        subj = f"{claim.unit}'s {claim.point}"
        variants = [
            ("true", "none", f"{subj} has an RMSE of {fig} against its reference {cite(*cites)}."),
            ("true", "none", f"{subj}'s RMSE against its reference is {fig} {cite(*cites)}."),
            (
                "false",
                "perturb_figure",
                f"{subj} has an RMSE of {float(claim.value) + 1.2:.1f} {claim.munit} against its "
                f"reference {cite(*cites)}.",
            ),
            (
                "false",
                "temp_unit",
                f"{subj} has an RMSE of {float(claim.value):.1f} {other} against its reference "
                f"{cite(*cites)}.",
            ),
        ]
        b = bias.get((claim.fact_id, claim.point))
        if b is not None and abs(float(b.value)) != float(claim.value):
            variants.append(("false", "swap_item", f"{subj} has a bias of {fig} {cite(*cites)}."))
        for truth, op, draft in variants:
            out.append(
                _case(
                    draft,
                    [draft],
                    "plain",
                    truth=truth,
                    form="rmse",
                    operator=op,
                    capability=claim.capability,
                    cites=list(cites),
                    claim=f"RMSE {fig}",
                    group=_key("rmse", claim.fact_id),
                )
            )
    return out


def _pairs(claims: list, world: World) -> list:
    out: list = _their_cases(claims, world) + _cell_parts_cases(claims, world)
    out += _short_id_cases(claims) + _caveat_cases(claims)
    out += _total_cases(claims) + _input_cases(claims) + _rmse_cases(claims)
    for gk, members in sorted(_pair_groups(claims).items(), key=lambda kv: str(kv[0])):
        members = sorted(members, key=lambda c: (c.unit, c.fact_id))
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if a.unit == b.unit:
                    continue
                out.extend(_pair_cases(a, b, world, gk))
                out.extend(_pair_cases(b, a, world, gk))
    return out


def _pair_cases(a, b, world: World, gk) -> list:
    ca, cb = realise(a, 0), realise(b, 0)
    if ca is None or cb is None:
        return []
    cap = a.capability if a.capability == b.capability else f"{a.capability}+{b.capability}"
    a_of_b = replace(a, unit=b.unit)
    differ = not world.true(a_of_b)
    same = world.true(a_of_b) and world.true(replace(b, unit=a.unit))
    group = _key("pair", a.fact_id, a.key(), b.fact_id, b.key())
    out = []

    def emit(form, x, y, truth, op, differ_, same_):
        w = pair(form, x, y, differ_, same_)
        if w is None:
            return
        out.append(
            _case(
                *w,
                truth=truth,
                form=form,
                operator=op,
                capability=cap,
                cites=[*ca.cites, *cb.cites],
                claim=f"{x.text} | {y.text}",
                group=group,
            )
        )

    for form in PAIR_FORMS:
        emit(form, ca, cb, "true", "none", differ, same)
    # the two units' claims exchanged: both clauses keep their citations
    sa, sb = replace(a, unit=b.unit), replace(b, unit=a.unit)
    if not (world.true(sa) and world.true(sb)):
        xa, xb = realise(sa, 0), realise(sb, 0)
        if xa is not None and xb is not None:
            xa, xb = replace(xa, cites=ca.cites), replace(xb, cites=cb.cites)
            for form in PAIR_FORMS:
                if form in ("set_both", "most_of"):
                    continue
                emit(form, xa, xb, "false", "swap_pair", differ, same)
    # one unit's claim said of both
    if differ:
        emit("set_both", ca, cb, "false", "overgeneralise", differ, True)
        emit("most_of", ca, cb, "false", "overgeneralise", differ, True)
    # (round 11) the comparative turned round, the figures kept
    w = pair("comparative", ca, cb, differ, same)
    if w is not None:
        flipped = _flip_comparative(w[0])
        if flipped != w[0]:
            out.append(
                _case(
                    flipped,
                    [flipped],
                    w[2],
                    truth="false",
                    form="comparative",
                    operator="flip_comparative",
                    capability=cap,
                    cites=[*ca.cites, *cb.cites],
                    claim=f"{ca.text} | {cb.text}",
                    group=group,
                )
            )
    # the second clause mutated
    for op in ("perturb_figure", "verdict", "negation"):
        for m in mutants(b, op, world)[:1]:
            mb = realise(m, 0)
            if mb is None:
                continue
            for form in ("two_clause", "status_list", "paren_list", "respectively", "figure_list"):
                emit(form, ca, mb, "false", op, differ, same)
    return out


def sample(cases: list, n: int, seed: int = 0) -> list:
    """A seeded sample of ``n`` cases that keeps at least one case of every cell
    (form x operator x capability x truth), then fills the rest at random."""
    rng = random.Random(seed)
    cells: dict = {}
    for c in cases:
        cells.setdefault((c["form"], c["operator"], c["capability"], c["truth"]), []).append(c)
    picked = []
    for k in sorted(cells):
        picked.append(rng.choice(cells[k]))
    if len(picked) >= n:
        rng.shuffle(picked)
        return sorted(picked[:n], key=lambda c: c["key"])
    chosen = {c["key"] for c in picked}
    rest = [c for c in cases if c["key"] not in chosen]
    picked += rng.sample(rest, min(n - len(picked), len(rest)))
    return sorted(picked, key=lambda c: c["key"])


# ----------------------------------------------------------------------------- variants


def fold_variants(draft: str) -> list:
    """Unicode spellings a reader cannot tell apart from ``draft``: non-breaking hyphens in
    ids, no-break spaces, a curly apostrophe. Each differs from ``draft``."""
    out = []
    v = re.sub(r"([A-Za-z])-(\d)", rf"\1{NBH}\2", draft)
    if v != draft:
        out.append(v)
    v = draft.replace("'s ", "’s ")
    if v != draft:
        out.append(v)
    v = re.sub(r"(?<=\w) (?=\w)", NBSP, draft, count=2)
    if v != draft:
        out.append(v)
    return out
