"""The grammar of forms, and the generated cases.

A *form* writes one clause (a single-claim form) or two (a pair form) as a draft: plain,
bullets, headings, bold labels, table rows, header columns, HTML comments, numbers in words,
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
    "numbers_in_words",
    "conditional",
    "existential",
    "time_span",
    "coverage_words",
    "unicode_hyphen",
    "id_spelling",
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
)
REGION = {
    "bullet": "bullet",
    "heading": "heading",
    "bold_label": "bold",
    "table_row": "table",
    "header_column": "table_header",
    "html_comment": "comment",
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
    if form == "html_comment":
        s = f"<!-- {c.text} {ids}. -->"
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
    claims = _thin(claims)
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
                    if mc is None or mc.text == c.text:
                        continue
                    for form in forms:
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


def _pairs(claims: list, world: World) -> list:
    out: list = []
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
                if form == "set_both":
                    continue
                emit(form, xa, xb, "false", "swap_pair", differ, same)
    # one unit's claim said of both
    if differ:
        emit("set_both", ca, cb, "false", "overgeneralise", differ, True)
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
