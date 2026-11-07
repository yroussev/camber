"""Write a claim as a clause: the template realiser.

:func:`realise` turns a :class:`~.facttable.Claim` (true, or a mutant from :mod:`.mutate`) into
a :class:`Clause`: a subject, a predicate, a compact status ("economizer_high_limit fault",
"trust 0.70"), a value word for parenthetical lists, and the ids it cites. ``variant`` picks one
of a few paraphrases of the same predicate (the metamorphic "paraphrase keeps the verdict"
relation compares them). A mutant's flags in ``claim.extra`` (``neg``, ``quant``, ``period``,
``check``) change how it is written; its fields are written like any claim's.
"""

from __future__ import annotations

from dataclasses import dataclass

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def in_words(n: int) -> str:
    """``n`` (0-99) in words: ``43`` -> ``forty-three``."""
    n = int(n)
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t - 2] + ("" if o == 0 else "-" + _ONES[o])


@dataclass(frozen=True)
class Clause:
    """One claim written out. ``text`` is ``subject + " " + pred``."""

    subject: str
    pred: str
    status: str  # compact: "economizer_high_limit fault", "trust 0.70"
    value_word: str  # for "A (x) and B (y)": "fault", "0.70"
    tail: str  # what a pair list shares: "on economizer_high_limit", ""
    cites: tuple
    figure: str = ""  # the formatted figure, when the claim has one
    pred_fig: str = ""  # the predicate with "{}" for the figure (respectively forms)
    neg_pred: str = ""  # the predicate negated (contrasts: "B does not")
    integer: int | None = None  # a whole figure, for numbers in words

    @property
    def text(self) -> str:
        return f"{self.subject} {self.pred}"


def _fmt(claim, v=None) -> str:
    """The claim's figure as a sentence gives it (with its measurement unit)."""
    v = claim.value if v is None else v
    a, mu = claim.attribute, claim.munit
    if v is None:
        return ""
    if a == "trust":
        return f"{float(v):.2f}"
    if a == "bias":
        unit = mu or "°F"
        return f"{float(v):+.1f} {unit}"
    if mu == "percentage points":
        return f"{float(v):g} percentage points"
    if a in ("coverage",):
        return f"{float(v):.1f}%"
    if a in ("share", "saving") or mu == "%":
        return f"{float(v):g}%"
    if mu in ("°F", "°C"):
        return f"{float(v):g} {mu}"
    if mu == "h":
        return f"{float(v):g} hours"
    if mu == "days":
        return f"{float(v):g}"
    if mu == "fraction":
        return f"{float(v):g}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


_VERDICT = {
    "fault": ("fails {r}", "has a {r} fault", "is flagged fault by {r}"),
    "warn": ("warns on {r}", "has a {r} warning", "gets a warn from {r}"),
    "ok": ("passes {r}", "is ok on {r}", "gets an ok from {r}"),
}
_VERDICT_NEG = {
    "fault": "does not fail {r}",
    "warn": "does not warn on {r}",
    "ok": "does not pass {r}",
}


def _pick(options, variant: int) -> str:
    return options[variant % len(options)]


def realise(claim, variant: int = 0) -> Clause | None:  # noqa: C901 - one branch per attribute
    """``claim`` as a :class:`Clause`, or ``None`` when no template writes it."""
    a = claim.attribute
    unit, point, rule = claim.unit, claim.point, claim.rule
    cites = (claim.fact_id,)
    fig = _fmt(claim)
    integer = None
    if isinstance(claim.value, (int, float)) and not isinstance(claim.value, bool):
        if float(claim.value).is_integer() and 0 <= claim.value < 100:
            integer = int(claim.value)
    subj = unit
    if point:
        subj = _pick((f"{unit}'s {point}", f"the {point} on {unit}"), variant // 3)
    tail = ""
    if a == "verdict":
        if claim.verdict not in _VERDICT:
            return None
        pred = _pick(_VERDICT[claim.verdict], variant).format(r=rule)
        neg = _VERDICT_NEG[claim.verdict].format(r=rule)
        status, word, tail = f"{rule} {claim.verdict}", claim.verdict, f"on {rule}"
        out = Clause(subj, pred, status, word, tail, cites, neg_pred=neg)
    elif a == "trust":
        opts = ("has a trust of {}", "has trust {}", "scores {} for trust")
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"trust {fig}", fig, "", cites, fig, pf)
    elif a == "trust_class":
        opts = ("is {}", "is rated {}", "counts as {}")
        out = Clause(
            subj, _pick(opts, variant).format(claim.label), claim.label, claim.label, "", cites
        )
        out = _with_neg(out, f"is not {claim.label}")
    elif a == "flat":
        opts = ("never moves", "is flat", "stays at {} the whole window")
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), "flat", "flat", "", cites, fig, pf, neg_pred="moves")
    elif a == "reference":
        if claim.verdict == "warn":
            opts = ["disagrees with its reference"]
            if claim.label:
                opts.append(f"{claim.label} against its reference")
            word, neg = "disagrees", "does not disagree with its reference"
        elif claim.verdict == "ok":
            opts = ["agrees with its reference", "tracks its reference"]
            word, neg = "agrees", "does not agree with its reference"
        else:
            return None
        out = Clause(subj, _pick(opts, variant), word, word, "", cites, neg_pred=neg)
    elif a == "bias":
        opts = ("has a bias of {}", "reads {} against its reference")
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"bias {fig}", fig, "", cites, fig, pf)
    elif a == "coverage":
        if not unit:
            subj = claim.site
        opts = ("has {} coverage", "has a mean coverage of {}", "is covered at {}")
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"coverage {fig}", fig, "", cites, fig, pf)
    elif a == "count":
        if not claim.site or claim.value is None:
            return None
        subj = claim.site
        lab = claim.label
        opts = ("has {} " + lab, "counts {} " + lab)
        if lab == "points":
            opts = ("stores {} points", "has {} points")
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"{fig} {lab}", fig, "", cites, fig, pf, integer=integer)
    elif a == "hours":
        opts = ("was flagged for {} by " + rule, "had {} flagged by " + rule)
        pf = _pick(opts, variant)
        out = Clause(
            subj,
            pf.format(fig),
            f"{rule} {fig}",
            fig,
            f"by {rule}",
            cites,
            fig,
            pf,
            integer=integer,
        )
    elif a == "span":
        k = claim.label.replace("span ", "")
        subj = _pick((f"Span {k} of {rule} on {unit}", f"The {rule} span {k} on {unit}"), variant)
        pf = "lasted {}"
        out = Clause(
            subj, pf.format(fig), f"span {k} {fig}", fig, "", cites, fig, pf, integer=integer
        )
    elif a == "share":
        opts = (
            f"is flagged by {rule} on {{}} of {claim.label}",
            f"has {rule} on {{}} of {claim.label}",
        )
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"{rule} {fig}", fig, f"on {rule}", cites, fig, pf)
    elif a == "saving":
        v = float(claim.value)
        if abs(v) < 0.05:
            return None
        if v > 0:
            opts = ("saved {}", "avoided {} of its energy")
        else:
            opts = ("used {} more than its baseline", "used {} more energy than its baseline")
            fig = _fmt(claim, abs(v))
        pf = _pick(opts, variant)
        out = Clause(subj, pf.format(fig), f"saving {fig}", fig, "", cites, fig, pf)
    elif a == "reporting_days":
        pf = "has {} reporting days"
        out = Clause(
            subj, pf.format(fig), f"{fig} reporting days", fig, "", cites, fig, pf, integer=integer
        )
    elif a == "rank":
        if claim.value is None:
            return None
        k = int(claim.value)
        opts = (f"has Issue {k}", f"is where Issue {k} is")
        out = Clause(unit, _pick(opts, variant), f"Issue {k}", f"Issue {k}", "", cites, integer=k)
    elif a == "issue_hours":
        subj = claim.label.capitalize()
        fig2 = f"{float(claim.value):g} violation hours"
        out = Clause(subj, f"ran {fig2}", fig2, fig2, "", cites, fig2, "ran {}", integer=integer)
    elif a == "licence":
        subj = unit
        if claim.label == "research-only" and variant % 2:
            out = Clause(subj, "is research-only", "research-only", "research-only", "", cites)
        else:
            out = Clause(
                subj, f"is licensed {claim.value}", str(claim.value), str(claim.value), "", cites
            )
    elif a == "param":
        subj = _pick(
            (f"The default {claim.label} of {rule}", f"{rule}'s default {claim.label}"), variant
        )
        pf = "is {}"
        out = Clause(subj, pf.format(fig), f"{claim.label} = {fig}", fig, "", cites, fig, pf)
    else:
        return None
    return _flags(claim, out)


def _with_neg(c: Clause, neg: str) -> Clause:
    from dataclasses import replace

    return replace(c, neg_pred=neg)


def _flags(claim, c: Clause) -> Clause | None:
    """Apply a mutant's writing flags (negation, quantifier, period, claim of checking)."""
    from dataclasses import replace

    ex = claim.extra or {}
    if ex.get("neg"):
        if not c.neg_pred:
            return None
        c = replace(c, pred=c.neg_pred)
    if ex.get("quant"):
        c = replace(c, subject=ex["quant"])
    if ex.get("site_subject"):
        c = replace(c, subject=ex["site_subject"])
    if ex.get("period"):
        c = replace(c, pred=f"{c.pred} {ex['period']}")
    if ex.get("pred"):
        c = replace(c, pred=ex["pred"])
    if ex.get("check"):
        before, after = ex["check"]
        c = replace(c, subject=before + c.subject, pred=c.pred + after)
    return c
