"""Citation extraction + grounding verification for generated answers.

An answer is **grounded** when (a) every ``[id]`` it cites resolves to a real fact in the
:class:`~camber.agent.context.Context`, and (b) every claim it makes can be matched to a cited fact.
Requiring claim-*traceability* (rather than a citation token in the very same sentence) is what
lets a multi-sentence fact -- e.g. a recommendation whose ``[id]`` leads a paragraph of specifics --
stay grounded, while a fabricated figure the model invented is caught.

What a sentence must satisfy (0.112, #294, #328) -- precise rather than clever, and failing
closed. "Supported" means every word below that the sentence uses was matched against the facts
it cites; it does not mean the sentence is true, and a word outside these lists is not read:

- **figures** (``uncited-number``): each number must be one a cited fact states, with the same
  sign (``-45%`` is not ``45%``), the same unit when the sentence gives one (``%``, ``°F``,
  ``kWh``, hours, percentage points, ...), the same counted thing when the sentence names one
  (hours, samples, points, rules, faults, ...; for a percentage, what it is "of"), and the same
  role: a figure the fact gives as a threshold, limit, target, setpoint or tier (``OAT < 60°F``,
  ``above 20% min``, ``the 30% CV(RMSE) tier``) is not a result, and a result is not one
  (``the target is 55°F`` against a measured 55°F fails). A fact's figures are those its text
  states (outside quoted names: a number in a name from the data is not a figure) and its
  numeric ``value`` (with its ``unit``, when it has one), never the other numbers of its data;
  in a fact that lists several figures, a bare figure takes the unit or counted thing of its
  neighbour (``9 trusted, 2 suspect`` of ``11 points``; ``0.83 vs 0.35 in. w.c.``), and keeps
  the words next to it: a sentence giving it with the words of another figure of that list ("the
  cooling valve leaked on 86%" against ``HW-leak 86% / CHW-leak 0%``) fails (#332). Labels of a
  closed family -- the side (heating: heat, HW, hot water, HHW, reheat, preheat, steam, boiler;
  cooling: cool, CHW, chilled water, DX, refrigerant), the trust class (trusted, suspect,
  untrusted) and the G36 fault condition (FC10) -- are matched as such: a figure's label must be
  the fact's figure's, and where the fact tells its figures apart by a family the sentence must
  name one, or the figure fails ("the hot-water valve leaked on 60%" against ``HW-leak 0% /
  CHW-leak 60%``, "1 point is suspect" against ``1 untrusted; 1 below the trust floor``, "the
  valve leaked on 60%", #336). A bias "taken off" is the correction with its sign changed
  ("the +3.0 °F bias taken off" against "corrected ... by -3.0 °F"). A date, time or year
  ("the 2016 baseline") must be one a cited fact carries (in its text or in a date field); a list
  marker, a step and an ordinal are labels, not figures. A number written in words
  (``forty-six``, ``half``, ``one``) is a figure too. A derived number (a difference, sum or
  ratio of figures) is not supported unless a fact states it;
- **claims** (``unsupported-claim``):

  - **names**: an equipment, meter or facility the sentence names (an id with a digit or an
    underscore, a quoted name, the ``equip`` / ``facility_id`` / ``meter`` of a cited fact, or a
    facility id or registry name the caller passes) must be named by a cited fact. A name from
    the data shields its own words only when it is quoted or reads as a plain name: a name that
    itself states a figure, verdict or negation (``AHU-25 saves 777 kWh``) is read like any other
    words. A rule name must be one a cited fact names when the cited facts name a rule; tool and
    field names are not names of anything;
  - **point roles**: a point the sentence names (supply, return, mixed or outdoor air, the
    heating or cooling valve, a setpoint) must be one a cited fact is about or mentions;
  - **verdicts**: a verdict word (fault, fail, warn, ok, pass, passes, healthy, accurate, meets,
    significant, suspect, trusted, explained, persists, flat, ...) must be one a cited fact
    states, with the same polarity ("passes G14" against "does not meet G14" fails); so must an
    assurance word (verified, confirmed, clean, real, genuine, confident, certain, proven,
    guaranteed, reliable, within tolerance) or a denial (false, untrue, incorrect, wrong): those
    pass only where a cited fact uses them. A verdict word under "if", "until", "unless",
    "could" or "to" is not a claim; a cause or effect ("accounts for", "due to", "will clear")
    is the verdict "explained", hedged or not. A re-judged finding's first verdict only names
    the finding ("the leaking_valve fault"): it supports no "is still a fault", "stands",
    "remains valid" or "still holds" (#332, #336). "The least trusted" is a rank, not the
    verdict "trusted";
  - **directions**: a direction word (rose, fell, increased, saved, avoided, wastes, ...) must
    be one a cited fact uses, and "high" / "low" or "above" / "below" must not be the opposite
    of the cited facts' ("the figures above" is a place in the text, #336);
  - **negations, both ways**: a negation ("did not leak", "never moves", "without", "no
    <thing>") must be one a cited fact makes about the same thing (a fact that negates something
    else does not count), and a sentence must not state what a cited fact negates ("the valve
    leaks" against "no valve leak", "moves" against "never moves"); a closing "not" is never
    supported; a fact's "measured_fan_heat_f not set" is about that setting, so a sentence must
    name it ("no fan heat was set" fails, #336). A zero ("0 <things>", "no <things>", "none of
    them") is read with its own clause
    and the fact's with its own list item: the fact's zero must count the same thing, with its
    words and first verb in the sentence, and the sentence must not deny what its counted
    things are (#332);
  - **quantifiers and ranks**: "all" / "every" needs a fact that says all, every or 100%, "most"
    one that says most or a share above half; a whole count ("all 11", "8 of the 8", "8 out of
    8", "the 11 points are all ...") a fact that says all of that many or gives the count again;
    "Issue 3" must be a rank a cited fact gives, when a cited fact gives ranks; a rank said as
    one ("the second issue", "#1") or a rank word ("worst", "top", "least trusted") one a cited
    fact gives, or its rank 1 ("best" and "most trusted" are not rank 1) (#332, #336);
  - **counted things** (#336): what a sentence says of a counted thing as a whole, none or a
    part -- with a quantifier before or after the noun ("all", "each", "every one of", "both",
    "N of the N", "N out of N", "the 8 hours ... all fall", "none of", "no", "not a single",
    "not one of", "zero", "neither", "nothing", "some", "most", "k of the N"), or with none
    ("the 11 points are trusted", "the flagged hours fall": all of them) -- must agree with
    what a cited fact says of the same thing: its count of that label ("9 trusted" of "11
    points"; "14 fault" for "no rule returned a fault", "fault-free"), or its count with that
    statement ("none of the 8 flagged hours falls in the window"; "outside" it is the rest of
    them). One thing is not two labels of one family ("both suspect points are trusted"). A
    cause named after its effect ("declined because of the untrusted input") is a cited fact's
    when one states it ("declined because an input scored below the trust floor ..."). Since
    #338: a verdict given to a whole set ("The 5 rules passed") needs every verdict count of
    the cited facts to be that verdict; a fact's count with no words of its own takes its
    clause's verb ("flagged 8 hours (32 of 37 judged samples)"); a share ("86.5% of", "at least
    half of", "the majority of") is compared as a share; "there are 8 X in Y" says 8 of them are
    in Y; "Faults: 0", "faults were not found", "there aren't any", "fault free" and "free of
    suspect points" are zeros; a bare count after "and" counts what the one before it counts;
    "a single" is one and "over 9" more than 9; "they are all trusted" or "all trusted" with no
    counted thing needs a cited fact counting all of them with that label;
  - **sets of items** (#338, #339): one rule for any claim about two or more equipment,
    points, meters or facilities no cited fact counts -- "both", "each", "every", "all", "the
    N", "the pair of", a definite or possessive plural opening its clause ("the AHUs", "fac-b's
    7 AHUs"), "each AHU's ...", "both" or "each" with no noun, a negated set ("neither A nor B",
    "neither AHU", "no AHU", "none of the AHUs") and names joined in any position (a colon,
    dash or table-cell lead-in too; "B doesn't either") -- read against the items it names or
    the cited facts are about: each item needs the cited facts about it to state the claim of
    it, its figures included (a figure the sentence gives another named item, or a list of one
    figure each, aside), with its own clause only; a negated set needs the negation stated of
    each; a cited fact saying otherwise of one item fails it; "both" or N needs that many, and
    no number at least two. A set form found but not read (a negated set not opening its
    clause, a set's figure with no cited fact about its items) fails closed. A saving's
    direction follows its sign ("avoided energy -3.1%" is an increase), and a trust class or
    verdict alone before a colon or in a table cell counts its figure ("Suspect: 0").

Citations count only in the visible text: one inside an HTML comment or tag does not. ``[A, B]``
and ``[A; B]`` cite both ids (0.112).

:func:`check` enforces this. In ``strict`` mode it *repairs* the text -- dropping sentences that
fail and stripping unknown ``[id]`` tokens -- so a partly-hallucinated LLM answer degrades to its
grounded subset instead of misleading. The repaired text keeps the draft's lines: headings, blank
lines, list markers and list structure stay where they were, and only the failing sentences go
(0.112, #322, #328). In non-strict mode the same problems are recorded in ``flagged`` but the text
is left intact.

``require_cite_each_sentence=True`` (0.107, #194) is the stricter mode the RCx report's prose slots
use: every sentence must carry at least one ``[id]`` that resolves, and each claim it makes must
trace to a fact *that sentence* cites. A sentence that fails is flagged (``uncited-sentence``,
``uncited-number`` or ``unsupported-claim``) and, in ``strict`` mode, dropped. Without it, a
sentence's figures may trace to any fact the text cites, and only a sentence that cites a fact
itself has its names, verdicts, directions and negations checked (against the facts it cites).

Every line that makes a claim is judged, a heading, a bold line or a lead-in ending in a colon
included (0.112, #328). A heading or label that states nothing -- no citation, figure, date,
negation, assurance word, verb outside a relative clause or advice, such as ``# AHU-1
triage``, ``Findings:``, ``## Points that never move`` or ``1. Sensor offset.`` -- is kept and
not judged. A ``NOTICE:`` line (``Notice:`` or bold too) repeating one of the ``notices`` passed
in (a server's own banners) is kept and not judged either.

A fact's own text quoted word for word and cited with that fact's id is the server's words, and
supported as one sentence however many sentences, verdicts, negations or caveats it holds (#328
follow-up): it must open a line (after a list marker or a ``NOTICE:`` label) or a sentence, run
whole up to its citation, and be followed by nothing but a full stop, a space or the line's end.
A notice is a fact (``N1@...``), so a NOTICE line that repeats one and cites it is supported. A
quote with a word added, changed or left out, or citing another fact, is judged like any other
sentence (and words before it on the line are a sentence of their own).

A fact id may be context-qualified, ``[F1@ctx]`` (0.107, #194): the part after ``@`` names where the
fact lives (the RCx report uses ``summary``, ``appA``, ``appB`` and an issue's key), so ids from
several contexts can share one whitelist. Plain ``[F1]`` ids work as before.

Pure: regex + the fact whitelist, no LLM, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Any

_ID = r"[A-Za-z]+\d+(?:@[A-Za-z0-9_\-]+)?"
#: a citation of one id: ``[F1]``, or context-qualified ``[F1@ctx]`` (0.107, #194); ``ctx`` is
#: letters, digits, ``_`` and ``-``
_CITE = re.compile(rf"\[({_ID})\]")
#: a citation of one id or of several, comma- or semicolon-separated: ``[F1, F2@x]`` (0.112)
_CITES = re.compile(rf"\[\s*({_ID}(?:\s*[,;]\s*{_ID})*)\s*\]")
_ID_SEP = re.compile(r"\s*[,;]\s*")
#: a fact id outside brackets (``(F1@x)``, as a result shows an id it does not cite): no claim
_FACT_ID = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]+\d+@[A-Za-z0-9_\-]+")
#: a sentence end: end-punctuation *followed by whitespace* -> never inside "5.16" or "Ch.5"
_SENT_END = re.compile(r"(?<=[.!?])[ \t]+")
#: a full stop that ends an abbreviation, not a sentence (#328): "vs.", "e.g.", "approx.", and
#: "in." after a figure or before "w.c." / "H2O"
_ABBREV = re.compile(r"(?:\b(?:vs|e\.g|i\.e|approx|incl|cf|ca|resp|fig|w\.c|w\.g)\.)\Z", re.I)
_INCH_BEFORE = re.compile(r"\d\s*in\.\Z", re.I)
_INCH_AFTER = re.compile(r"(?:w\.?\s?c\b|wc\b|wg\b|h2o\b)", re.I)
#: a list marker ("1.", "2)", "(3)", "-", "*", "•", ">") that starts a sentence: it is part of the
#: sentence that follows it, and its number is not a figure to trace (#314)
_MARKER = re.compile(r"(?:\(?\d{1,3}[.)]|[-*•>])(?=\s|\Z)")
#: a heading-shaped line: markdown ``#``, a short line ending in a colon, or a bold-only line
_HEADING = re.compile(r"\s*(?:#{1,6}\s+\S.*|[^.!?]{1,80}:|\*\*[^*]+\*\*:?)\s*")
_NOTICE = re.compile(r"\s*(?:[-*•>]\s+)?(?:\*\*)?NOTICE\s*:(?:\*\*)?\s*(.*?)\s*", re.I)
#: what may open a line before a quoted fact (#328): a list marker, a ``NOTICE:`` label (any case,
#: bold or not), or both
_QUOTE_LEAD = re.compile(
    r"\s*(?:(?:\(?\d{1,3}[.)]|[-*•>])\s+)?(?:\*\*)?(?:notice\s*:)?(?:\*\*)?\s*", re.I
)
#: what a reader does not see: HTML comments (an unterminated one: to the end), tags,
#: zero-width marks, and a markdown link's target
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_INVISIBLE = re.compile("[​-‏⁠-⁤﻿­]")
_LINK = re.compile(r"(\[[^\[\]]*\])\([^()\s]*(?:\s+\"[^\"]*\")?\)")


@dataclass
class Grounded:
    """A verified answer: the (possibly repaired) text plus what it cites and whether it's
    grounded."""

    text: str
    cited: list = field(default_factory=list)  # ids cited AND present in context (order-stable)
    facts: list = field(default_factory=list)  # the resolved Fact objects (may be empty)
    grounded: bool = True  # no unknown cite AND every sentence supported
    flagged: list = field(default_factory=list)  # [{"reason", "text"[, "why"]}] problems found
    source: str = "llm"  # "llm" | "template"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["facts"] = [f.as_dict() if hasattr(f, "as_dict") else f for f in self.facts]
        return d


def _visible(text: str) -> str:
    """``text`` as a reader sees it: without HTML comments and tags, zero-width marks, markdown
    emphasis and link targets (a citation hidden there does not count)."""
    s = _INVISIBLE.sub("", str(text or ""))
    s = _COMMENT.sub(" ", s)
    s = _TAG.sub(" ", s)
    return _LINK.sub(r"\1", s).replace("`", "").replace("**", "")


def extract_cites(text: str) -> list:
    """The ``[id]`` tokens cited in ``text``, in order, de-duplicated; ``[A, B]`` cites both."""
    seen, out = set(), []
    for m in _CITES.finditer(text or ""):
        for cid in _ID_SEP.split(m.group(1).strip()):
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


# --------------------------------------------------------------------------- segments


@dataclass(frozen=True)
class _Seg:
    start: int
    end: int
    kind: str  # "sentence" (judged) | "quote" (a fact's own text) | "heading" | "notice" (kept)


def _ends(text: str, a: int, b: int) -> list:
    """The sentence ends in ``text[a:b]`` as ``(end of sentence, start of the next)``: a full
    stop, ``!`` or ``?`` before whitespace, not after an abbreviation (#328)."""
    out = []
    for m in _SENT_END.finditer(text, a, b):
        head = text[max(a, m.start() - 12) : m.start()]
        if _ABBREV.search(head) or (
            head.lower().endswith("in.")
            and (_INCH_BEFORE.search(head) or _INCH_AFTER.match(text, m.end(), b))
        ):
            continue
        out.append((m.start(), m.end()))
    return out


def _unquoted(text: str) -> str:
    """``text`` with each quoted name blanked (same length), so no sentence ends inside one: a
    name from the data may hold ". " (#328)."""
    return _QUOTED.sub(lambda m: '"' + "x" * (len(m.group(0)) - 2) + '"', text)


def _split_sentences(text: str) -> list:
    """``text`` (a fact's) cut into sentences: at each sentence end and line break, never inside
    a quoted name."""
    out = []
    for line in str(text or "").split("\n"):
        cut = 0
        for a, b in _ends(_unquoted(line), 0, len(line)):
            out.append(line[cut:a])
            cut = b
        out.append(line[cut:])
    return [s for s in out if s.strip()]


def _quoted_rx(fact_text: str):
    """A pattern for ``fact_text`` at the end of a string, any run of whitespace standing for
    any other (None for an empty text). Whole sentences of it in a row match too ("A. B." of
    "A. B. C"), longest first (#332): each is the server's words."""
    sents = [s.split() for s in _split_sentences(str(fact_text or ""))][:40]
    sents = [s for s in sents if s]
    if not sents:
        return None
    runs = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            words = [w for s in sents[i : j + 1] for w in s]
            last = re.escape(words[-1].rstrip(".")) + r"\.?"
            runs.append((len(words), r"\s+".join([*map(re.escape, words[:-1]), last])))
    alts = [r for _n, r in sorted(runs, key=lambda x: -x[0])]
    return re.compile("(?:" + "|".join(alts) + r")\s*\Z")


def _quotes(text: str, masked: str, s0: int, e0: int, quotes: dict) -> list:
    """The spans ``(start, end)`` of one line ``text[s0:e0]`` that quote a fact word for word
    and cite it (#328 follow-up): a fact's whole text followed by a citation that names that
    fact's id, opening the line (after a list marker or a ``NOTICE:`` label) or a sentence, and
    with nothing after the citation (and a full stop) but a space or the line's end. The span
    runs from the line's start, or the quote's, to the end of its citation."""
    out: list = []
    floor = s0
    for m in _CITES.finditer(masked, s0, e0):
        ids = _ID_SEP.split(m.group(1).strip())
        end = m.end() + (1 if masked[m.end() : m.end() + 1] == "." else 0)
        if end < e0 and masked[end] not in " \t":
            continue
        best = None
        for fid in ids:
            rx = quotes.get(fid)
            q = rx.search(text, floor, m.start()) if rx is not None else None
            if q is not None and (best is None or q.start() < best):
                best = q.start()
        if best is None:
            continue
        lead = text[floor:best]
        if floor == s0 and _QUOTE_LEAD.fullmatch(lead):
            start = s0
        elif text[best - 1] in " \t" and text[s0:best].rstrip()[-1:] in (".", "!", "?", "]"):
            start = best
        else:
            continue
        out.append((start, end))
        floor = end
    return out


def _segments(text: str, notices=(), quotes=None) -> list:
    """The sentences, headings, known notices and quoted facts of ``text``, as spans of it, in
    order. Lines are split at sentence ends, never inside a quoted fact (``quotes`` maps a fact
    id to a pattern of its text, :func:`_quoted_rx`); an HTML comment is never split."""
    text = text or ""
    masked = _COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    masked = "\n".join(_unquoted(line) for line in masked.split("\n"))
    known = {" ".join(str(n).split()) for n in notices or ()}
    out: list = []
    pos = 0
    for line in masked.split("\n"):
        ls = pos
        pos += len(line) + 1
        if not line.strip():
            continue
        s0 = ls + (len(line) - len(line.lstrip()))
        e0 = ls + len(line.rstrip())
        m = _NOTICE.fullmatch(text[ls : ls + len(line)])
        if m is not None and " ".join(m.group(1).split()) in known:
            out.append(_Seg(s0, e0, "notice"))
            continue
        if _HEADING.fullmatch(line) and _is_heading(text[s0:e0]):
            out.append(_Seg(s0, e0, "heading"))
            continue
        quoted = _quotes(text, masked, s0, e0, quotes) if quotes else []
        ends = _ends(masked, s0, e0)
        if quoted:  # a quoted fact is one piece: no end inside it, one at each side of it
            ends = [(a, b) for a, b in ends if not any(q0 <= a < q1 for q0, q1 in quoted)]
            for q0, q1 in quoted:
                pe = len(text[:q0].rstrip())
                if q0 > s0 and all(a != pe for a, _ in ends):
                    ends.append((pe, q0))
                nxt = q1 + len(text[q1:e0]) - len(text[q1:e0].lstrip())
                if q1 < e0:
                    ends.append((q1, nxt))
            ends.sort()
        starts = {q0 for q0, _ in quoted}
        spans, cut = [], s0
        for a, b in ends:
            spans.append((cut, a))
            cut = b
        spans.append((cut, e0))
        spans = [(a, b) for a, b in spans if a < b]
        merged: list = []
        lead = None
        for a, b in spans:
            piece = text[a:b]
            if _MARKER.fullmatch(piece):  # "1." alone: it belongs to the next sentence
                lead = a if lead is None else lead
                continue
            if merged and lead is None and _CITES.sub("", piece).strip(" \t.;,") == "":
                merged[-1][1] = b  # "... saves 77%. [F1]": the citation is that sentence's
                continue
            merged.append([a if lead is None else lead, b])
            lead = None
        if lead is not None:
            merged.append([lead, e0])
        for a, b in merged:
            # a short label that states nothing ("1. Sensor offset.") is kept, not judged
            if a in starts:
                kind = "quote"
            else:
                kind = "heading" if _is_label(text[a:b]) else "sentence"
            out.append(_Seg(a, b, kind))
    return out


#: verbs and auxiliaries that make a heading-shaped line a statement (#328)
_STATING = frozenset(
    (
        "is are was were be been being am has have had do does did will would shall should can "
        "could may might must need needs needed require requires required leaks leaked reads "
        "moves moved drifts drifted hunts hunted cycles cycled runs ran stays stayed exceeds "
        "exceeded shows showed found finds fixed replaced works worked passes passed fails "
        "failed meets met rose fell increased decreased dropped reduced saved avoided wasted "
        "wastes saves reduces rises falls grew grows costs means indicates indicated suggests "
        "suggested confirms confirmed verified proves proved"
    ).split()
)
#: an imperative that opens a line of advice ("Replace the valve"); the ones that are nouns too
#: ("Reset gap", "Check 2") only before a determiner or a name
_ADVICE = frozenset(
    (
        "replace repair fix inspect recalibrate calibrate verify investigate retune swap "
        "install remove schedule confirm"
    ).split()
)
_ADVICE_OR_NOUN = frozenset(
    (
        "check test adjust reset tune clean review trend measure log stop start open close "
        "lower raise increase decrease cut change"
    ).split()
)
_DETERMINERS = frozenset(
    "the a an its their this that these those all each every both it them his her".split()
)
_RELATIVE = frozenset("that which who whose where".split())
_QUESTION = frozenset("how what which why where when who whether".split())


def _is_heading(line: str) -> bool:
    """A heading line that states nothing (#328): no citation, figure, date, negation or
    verdict, no verb outside a relative clause ("## Points that never move" is a heading), and
    no advice ("**Replace the valve now**" is not)."""
    vis = _visible(line)
    if _CITES.search(vis):
        return False
    body = vis.strip().lstrip("#").strip().strip("*").strip()
    body = _unlisted(body).rstrip(":").strip()
    if not body:
        return True
    if body[-1] in ".!?" and len(body.split()) > 1:
        return False  # a whole sentence
    words = [w.lower() for w in re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", body)]
    if words and words[0] in _QUESTION:
        return True  # "How far the inputs can be trusted:" asks, it does not state
    return _states_nothing(body)


def _states_nothing(body: str) -> bool:
    """Whether a heading's or a label's ``body`` makes no claim (see :func:`_is_heading`)."""
    whole = body
    # names say nothing: "RTU__d3_stuck_000" states no claim
    body = re.sub(r"\S*_\S*|\S*[A-Za-z]\d\S*", " ", _QUOTED.sub(" ", body)).strip() or body
    parts = [p.strip() for p in re.split(r":", body) if p.strip()]
    for part in parts:
        tokens = part.split()
        words = [w.lower() for w in re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", part)]
        if len(words) > 1 and len(tokens) > 1:
            second = tokens[1].strip("\"'*").lower()
            named = "_" in tokens[1] or any(c.isdigit() for c in tokens[1])
            if words[0] in _ADVICE or (
                words[0] in _ADVICE_OR_NOUN and (second in _DETERMINERS or named)
            ):
                return False
        # a relative clause names what the heading is about ("Points that never move"): drop
        # its pronoun, its negation and its verb
        kept, skip = [], 0
        for w in words:
            if w in _RELATIVE:
                skip = 1
                continue
            if skip and (w in _NEG or _negates(w)):
                continue
            if skip:
                skip = 0
                continue
            kept.append(w)
        if any(w in _STATING or w in _NEG or _negates(w) for w in kept):
            return False
        if any(_VERDICT.get(w) in _ASSURANCE for w in kept):
            return False
    p = _parse(whole, ())
    return not (p.qty or p.dates or p.times)


def _is_label(sentence: str) -> bool:
    """A short list label that cites nothing and states nothing ("1. Sensor offset.")."""
    vis = _visible(sentence)
    if _CITES.search(vis) or _FACT_ID.search(vis):
        return False
    body = _unlisted(vis.strip()).strip().rstrip(".:").strip()
    if not body or len(body.split()) > 8 or re.search(r"[.!?;]", body):
        return False
    return _states_nothing(body)


def _sentences(text: str, notices=()) -> list:
    """The sentences of ``text`` that are judged, in order (headings and known notices are not)."""
    return [text[s.start : s.end] for s in _segments(text, notices) if s.kind == "sentence"]


def _unlisted(sentence: str) -> str:
    """``sentence`` without its leading list markers."""
    while True:
        m = _MARKER.match(sentence)
        if m is None:
            return sentence
        sentence = sentence[m.end() :].lstrip()


# --------------------------------------------------------------------------- vocabulary


def _words(spec: str, value) -> dict:
    return dict.fromkeys(spec.split(), value)


#: words that present a figure as someone's claim, not a measured result
_CLAIM_WORDS = frozenset(
    "claim claims claimed claiming promised promises projected projects proposed quoted".split()
)
_NEG = frozenset("not no never none nothing neither nor without cannot nobody".split())
#: a verdict word -> its class
_VERDICT = {
    **_words("fault faults faulted faulty fail fails failed failing failure failures", "fault"),
    **_words("warn warns warning warnings", "warn"),
    **_words("suspect suspects", "suspect"),
    **_words(
        "ok pass passes passed passing healthy correctly properly normal normally accurate "
        "accurately fine",
        "ok",
    ),
    **_words("meet meets met compliant complies acceptable", "meets"),
    "info": "info",
    **_words("significant significantly", "significant"),
    "insignificant": "insignificant",
    **_words("explained explains", "explained"),
    **_words("persists persist persisted persisting stands upheld", "persists"),
    "worse": "worse",
    "flat": "flat",
    "trusted": "trusted",
    "untrusted": "untrusted",
    # assurance words (#328): never inferred, so a cited fact must use them itself
    **_words("verified verification", "verified"),
    **_words("confirmed confirms", "confirmed"),
    **_words("clean cleanly", "clean"),
    **_words("real genuine", "real"),
    **_words("confident confidence confidently", "confidence"),
    **_words("certain certainly certainty definitely sure surely", "certain"),
    **_words("proven proved proves", "proven"),
    **_words("guaranteed guarantee guarantees", "guaranteed"),
    **_words("reliable reliably", "reliable"),
    "tolerance": "tolerance",
    # a frame that denies what it frames (#332): "it is false that ...", "... is wrong"
    **_words(
        "false falsely untrue incorrect incorrectly wrong wrongly mistaken inaccurate erroneous",
        "false",
    ),
    "true": "true",
}
_ASSURANCE = frozenset(
    "verified confirmed clean real confidence certain proven guaranteed reliable tolerance false "
    "true".split()
)
#: a cause or an effect claimed (#332): "accounts for the gap", "will clear this warn", "about the
#: same size as the gap" read as the verdict "explained", which only a cited fact can give
_CAUSAL = (
    ("accounts", "for"),
    ("account", "for"),
    ("accounted", "for"),
    ("due", "to"),
    ("caused", "by"),
    ("because", "of"),
    ("attributable", "to"),
    ("responsible", "for"),
    ("results", "from"),
    ("same", "size"),
    ("same", "magnitude"),
    ("comparable", "to"),
    ("consistent", "with"),
    ("will", "clear"),
    ("will", "fix"),
    ("will", "resolve"),
    ("will", "remove"),
    ("will", "eliminate"),
    ("would", "clear"),
    ("would", "fix"),
    ("would", "resolve"),
    ("would", "remove"),
    ("would", "eliminate"),
)
#: a rank stated as a word (#332): "the worst finding", "the top issue"; what it is said of
#: must be what a cited fact says it of ("worst: HW (heating) coil"), or a cited fact's rank 1
_SUPERLATIVE = frozenset("worst best top biggest largest severest".split())
#: a rank word that names the other end of a worst-first list: a cited fact's rank 1 is no
#: support for it (#336)
_BEST = frozenset({"best", "most trusted"})
_SEVERE = frozenset("severe serious significant important critical urgent".split())
#: words of a zero's sentence that say nothing of what is counted ("No faults were found")
_GENERIC = frozenset(
    "find return report list show give record see observ detect run result there total overall "
    "also only rais produc generat".split()
)
#: words naming a share itself, not what it is a share of ("the CHW-leak share is 0%")
_SHARE_WORDS = frozenset("shar fraction proportion percentag percent portion rat".split())
#: a time noun after "same" / "other": "on other days than", "on the same days as" (#332)
_WHEN_NOUNS = frozenset(
    "day days date dates hour hours period periods time times week weeks".split()
)
#: a negated verdict reads as this one ("does not meet" = fails, "not trusted" = untrusted)
_FLIP = {
    "ok": "fault",
    "meets": "fault",
    "fault": "ok",
    "warn": "ok",
    # a trust class negated is either of the other two (#339): "not trusted" is suspect or
    # untrusted, so a cited fact giving either supports it
    "suspect": "not suspect",
    "significant": "insignificant",
    "insignificant": "significant",
    "trusted": "not trusted",
    "untrusted": "not untrusted",
    "explained": "persists",
    "persists": "explained",
}
#: a structured verdict (a fact's ``value``, ``verdict`` or ``severity``) -> its class
_VALUE_VERDICT = {
    **_words("fault fail failed", "fault"),
    **_words("warn warning", "warn"),
    **_words("ok pass passed", "ok"),
    "info": "info",
    "suspect": "suspect",
    "trusted": "trusted",
    "untrusted": "untrusted",
    "explained": "explained",
    "persists": "persists",
    "worse": "worse",
    "flat": "flat",
}
#: a direction word -> its class
_DIRECTION = {
    **_words(
        "rose rise rises risen rising increase increased increases increasing grew grow grows "
        "growing climbed jumped gained spiked",
        "up",
    ),
    **_words(
        "fell fall falls fallen falling decrease decreased decreases decreasing dropped drop "
        "drops dropping reduce reduced reduces reducing reduction save saves saved saving "
        "savings avoid avoids avoided avoiding",
        "down",
    ),
    **_words("waste wastes wasted wasting wasteful", "waste"),
}
#: a structured direction (a fact's ``direction``) -> its class
_VALUE_DIRECTION = {
    **_words("up increase increased rise rose", "up"),
    **_words("down decrease decreased fall fell saving reduced", "down"),
}
#: words with an opposite: a sentence must not say "low" where its facts say only "high" (#328)
_POLAR = {
    **_words("high higher highest", "high"),
    **_words("low lower lowest", "low"),
    "above": "above",
    "below": "below",
    # a reading against its reference (#338): "disagrees" is not "agrees"
    **_words("agree agrees agreed agreeing", "agrees"),
    **_words("disagree disagrees disagreed disagreeing", "disagrees"),
}
_OPPOSITE = {
    "agrees": "disagrees",
    "disagrees": "agrees",
    "high": "low",
    "low": "high",
    "above": "below",
    "below": "above",
    "same": "other",
    "other": "same",
}
#: what follows "all" when it counts ("all of", "all the", "all its")
_ALL_BEFORE = frozenset("of the its their these those".split())
#: quantifiers: "every sample leaked" is a claim about all of them (#328)
_QUANT = {**_words("all every", "all"), **_words("most majority", "most")}
#: "fall on", "fall outside": where something falls, not a decrease
_PLACE = frozenset("on in within outside inside into between under across".split())
#: words that make what follows hypothetical: a verdict or direction there is not a claim
_HYPO = frozenset("if unless until whether once should could would might may can".split())
#: a verdict word followed by one of these names a setting, not a verdict ("fault threshold")
_SETTING = frozenset(
    "threshold thresholds limit limits level levels setting settings tier detection diagnostics "
    "diagnosis condition conditions code codes".split()
)
#: "passes flow when closed" is a valve passing water, not a verdict
_PASSES_WHAT = frozenset("flow through water air heat".split())
#: a unit word after a number -> its canonical name; None: never a unit
_UNITS = {
    **_words("% percent pct", "%"),
    "pp": "pp",
    **_words("f degf fahrenheit", "degF"),
    **_words("c degc celsius", "degC"),
    **_words("k kelvin", "K"),
    **_words("deg degree degrees", "deg"),
    "wh": "Wh",
    "kwh": "kWh",
    "mwh": "MWh",
    "gwh": "GWh",
    **_words("therm therms thm", "therm"),
    "kbtu": "kBtu",
    "mbtu": "MBtu",
    "mmbtu": "MMBtu",
    "btu": "Btu",
    "gj": "GJ",
    "mj": "MJ",
    "kj": "kJ",
    "w": "W",
    "kw": "kW",
    "mw": "MW",
    **_words("ton tons", "ton"),
    "hp": "hp",
    "btuh": "Btuh",
    **_words("kbtuh mbh", "kBtuh"),
    "cfm": "cfm",
    "gpm": "gpm",
    "lps": "L/s",
    "inwc": "inWC",
    "pa": "Pa",
    "kpa": "kPa",
    **_words("psi psig", "psi"),
    "ppm": "ppm",
    "lux": "lux",
    "rpm": "rpm",
    "hz": "Hz",
    **_words("s sec secs second seconds", "s"),
    **_words("minute minutes mins", "min"),
    **_words("h hr hrs hour hours", "h"),
    **_words("day days", "day"),
    **_words("week weeks wk", "week"),
    **_words("month months", "month"),
    **_words("year years yr yrs", "year"),
    **_words("x times fold", "x"),
    **_words("usd dollar dollars", "$"),
    **_words("eur euro euros", "€"),
    "gbp": "£",
}
#: one-letter units that count only glued to the number ("3F", "24h") or as a capital F/C/K
_SHORT_SPACED = frozenset({"F", "C", "K"})
_DEGREE = frozenset({"deg", "degree", "degrees"})
_CURRENCY = {"$": "$", "€": "€", "£": "£"}
#: counted things: a bare count names what it counts with one of these (in the next 3 words)
_NOUNS = {
    **{
        w: w
        for w in (
            "sample point rule equipment finding fault warn info ok span start issue meter "
            "facility building zone sensor check result sentence row dataset gap pair cycle "
            "alarm reading record fact trigger fit baseline entry event energy cost time "
            "value parameter role chart page"
        ).split()
    },
    "warning": "warn",
    "unit": "equipment",  # "2 units" are "2 equipment" (#328)
    "failure": "fault",  # "no failures" is no fault (#336)
}
#: the units that count something, so a zero of them says there is none of it
_COUNT_UNITS = frozenset({*_NOUNS.values(), "h", "day", "week", "month", "year", "min", "s"})
_NOUN_STOP = frozenset(
    (
        "and or but in on at with from to vs versus for by per than of over under during "
        "while which that is are was were be been has have had as if so because since"
    ).split()
)
#: words just before a figure that make it a threshold (and "more than", "at least", "up to")
_CUE_BEFORE = frozenset(
    (
        "above below under exceeds exceed exceeded exceeding beyond limit limits threshold "
        "thresholds setpoint setpoints target targets floor ceiling minimum maximum min max "
        "cutoff allows allow allowed allowing allowance tier"
    ).split()
)
_CUE_THAN = frozenset("more less greater fewer higher lower".split())
_OR_BOUND = frozenset("more less greater fewer higher lower above below over under".split())
_CUE_AFTER = frozenset(
    "min minimum max maximum limit threshold setpoint target floor ceiling cutoff tier "
    "allowance".split()
)
_SKIP_BEFORE = frozenset("the a an its their configured high low set of is was at".split())
#: a cue noun after a comparison ("below target 54% of hours") or after its own figure ("the
#: 68°F high limit 43% of samples") names what the next figure is compared with: that figure is
#: the result, not the threshold
_CUE_NOUNS = frozenset(
    "limit limits threshold thresholds setpoint setpoints target targets floor ceiling tier "
    "lockout allowance".split()
)
_COMPARISONS = frozenset(
    "above below under over beyond exceeds exceed exceeded exceeding off".split()
)
#: words between a cue noun and its figure ("the target of AHU-1 is 55°F": the target is 55°F)
_CUE_LINK = frozenset(
    "is was are were of at to set be been equals stays sits on for the a an its their "
    "configured about around near".split()
)
#: what a figure is when a sentence says it is the threshold's ("the threshold on X is 3.4F");
#: not a setpoint, whose trended reading is a result ("the setpoint reads 55 °F")
_SUBJECT_CUES = frozenset(
    "limit limits threshold thresholds target targets floor ceiling tier lockout allowance".split()
)
#: words just before a figure that make it a label: "Ranked cause 1", "step 2"
_LABELS = frozenset(
    (
        "rank ranked cause step item priority option number part section appendix table "
        "figure fig chapter page phase stage tier round version issue issues stop stops visit "
        "day guideline"
    ).split()
)
_ORDINAL = frozenset({"st", "nd", "rd", "th"})
_ORDINAL_WORDS = {
    w: i
    for i, w in enumerate(
        "_ first second third fourth fifth sixth seventh eighth ninth tenth".split()
    )
    if w != "_"
}
#: what a rank counts: "Issue 3", "the 1st issue" (#328)
_RANKED = frozenset("issue issues priority priorities rank ranked".split())
#: tokens shaped like equipment ids that are units, chemistry or standards
_NOT_IDS = frozenset(
    "co2 h2o pm2.5 pm10 no2 o3 so2 nh3 m2 m3 ft2 ft3 r410a r22 r134a g36 g14 r2".split()
)
_WORD_NUM: dict[str, Decimal] = {
    **{
        w: Decimal(i)
        for i, w in enumerate(
            (
                "zero one two three four five six seven eight nine ten eleven twelve thirteen "
                "fourteen fifteen sixteen seventeen eighteen nineteen"
            ).split()
        )
    },
    **{
        w: Decimal(10 * i)
        for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
        if w != "_"
    },
    "hundred": Decimal(100),
    "thousand": Decimal(1000),
    "million": Decimal(1000000),
    "dozen": Decimal(12),
    "half": Decimal("0.5"),
    "quarter": Decimal("0.25"),
    "twice": Decimal(2),
    "double": Decimal(2),
    "triple": Decimal(3),
}
#: "one" is a pronoun after these ("the one", "each one", "which one"), a count elsewhere
_ONE_PRONOUN = frozenset(
    "the this that each any which every no some another a an last next other such its their".split()
)

_TENS = frozenset(Decimal(t) for t in range(20, 100, 10))

#: words with no claim of their own: what a negation, a zero or a sentence is about is the rest
_FUNCTION = frozenset(
    (
        "a an the this that these those it its their there they he she we you i our your his "
        "her them is are was were be been being am do does did done have has had having will "
        "would shall should can could may might must of in on at to from by with for as into "
        "onto over under than then so and or but nor if when while until unless because since "
        "about after before during between through per via vs versus against also only just "
        "very more less most least much many some any each every all both either neither same "
        "such other another which who whom whose what where how why here up down out off "
        "again further once own too yet still even above below beyond within inside across "
        "around near s"
    ).split()
)
#: what a negation skips before the word it negates ("did not even leak", "is not a pass")
_NEG_SKIP = frozenset(
    (
        "be is are was were been being am do does did have has had any a an the single yet "
        "really always quite ever even so its their this that these those there it at all "
        "currently actually seen found"
    ).split()
)
_ARTICLES = frozenset("a an the any single".split())
#: spellings read as the same word
_SYNONYM = {
    "chw": "cool",
    "hw": "heat",
    "chilled": "cool",
    "shut": "clos",
    "closed": "clos",
    # the heating and the cooling side, each a closed set (#336): "hot water" is read as "HW"
    "hhw": "heat",
    "reheat": "heat",
    "preheat": "heat",
    "steam": "heat",
    "boiler": "heat",
    "dx": "cool",
    "refrigerant": "cool",
}
_IRREGULAR = {
    "met": "meet",
    "ran": "run",
    "fell": "fall",
    "rose": "rise",
    "found": "find",
    "gave": "give",
}


def _word_number(word: str):
    """A number written in words (``forty-six``, ``fifty``, ``half``, ``one``) as a Decimal, or
    None. Whether a lone "one" is a count or a pronoun is decided where it stands."""
    parts = word.split("-")
    if len(parts) == 1:
        return _WORD_NUM.get(word)
    tens = _WORD_NUM.get(parts[0])
    ones = _WORD_NUM.get(parts[-1])
    if len(parts) == 2 and tens in _TENS and ones is not None and 1 <= ones <= 9:
        return tens + ones
    return None


def _negates(word: str) -> bool:
    return word in _NEG or word.endswith(("n't", "n’t"))


def _stem(word: str) -> str:
    """``word`` with a plural or verb ending taken off, so "leaks", "leaked" and "leak" match."""
    w = _SYNONYM.get(word, word)
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    for suf in ("ing", "ed", "es", "s"):
        if not w.endswith(suf) or len(w) - len(suf) < 3:
            continue
        if suf == "s" and w.endswith(("ss", "us", "is")):
            break
        if suf == "es" and not w.endswith(("sses", "xes", "zes", "ches", "shes")):
            continue
        w = w[: -len(suf)]
        break
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return _SYNONYM.get(w, w)


#: the counted things, as stems; those that are verdicts too ("1 fault")
_NOUN_STEMS = frozenset(_stem(w + s) for w in _NOUNS for s in ("", "s"))
_VERDICT_NOUNS = frozenset(_stem(w + s) for w in ("fault", "warn", "info", "ok") for s in ("", "s"))


# --------------------------------------------------------------------------- parsing


@dataclass(frozen=True)
class _Qty:
    value: Decimal
    sign: str  # "" | "+" | "-" | "±"
    unit: str  # canonical unit or counted thing, "" when none
    base: str  # for a percentage: what it is "of", "" when none
    role: str  # "result" | "threshold"
    year: bool  # a bare 1900-2100 integer: may be a year
    at: int = field(default=-1, compare=False)  # its token, in the text it was read from
    claim: bool = field(default=False, compare=False)  # its clause presents it as a claim's
    # what it is of (#332): the words of its clause before it (since the figure before) and
    # after it (to the next figure); a fact's figure keeps the words that tell it from the
    # figures listed with it (``label``: "heat" of "HW-leak 86%") and theirs (``sib``: "cool")
    pre: frozenset = field(default=frozenset(), compare=False)
    post: frozenset = field(default=frozenset(), compare=False)
    label: frozenset = field(default=frozenset(), compare=False)
    sib: frozenset = field(default=frozenset(), compare=False)
    total: bool = field(default=False, compare=False)  # "of the 11": what another figure is of
    grp: int = field(default=-1, compare=False)  # the list of figures it is read with
    # its labels from the closed families (#336): ``(family, label)`` pairs, the nearest word of
    # each family ("HW-leak 86%": ("side", "heat"); "2 suspect": ("trust", "suspect")); and (a
    # fact's) the families that tell apart the figures of its unit listed with it
    fam: frozenset = field(default=frozenset(), compare=False)
    famon: frozenset = field(default=frozenset(), compare=False)
    # a bias taken off (a sentence's "the +3.0 °F bias taken off") or a correction (a fact's
    # "corrected ... by -3.0 °F"): the one is the other with its sign changed (#336)
    flip: bool = field(default=False, compare=False)
    # (a sentence's) the number of a set of items ("both", "the 2 AHUs"): the set's check reads
    # it (#338), against the items its cited facts are about
    of_set: bool = field(default=False, compare=False)
    # "one ok result": the thing a verdict count names after it ("result"), so it may be read
    # as 1 result the fact labels ok (#338)
    of_noun: str = field(default="", compare=False)
    share: bool = field(default=False, compare=False)  # "half of the 11": a share of a count

    def show(self) -> str:
        sign = "-" if self.sign == "-" else ("±" if self.sign == "±" else "")
        unit = f" {self.unit}" if self.unit else ""
        return f"{sign}{self.value:f}{unit}"


@dataclass(frozen=True)
class _Neg:
    """One thing a text says is not so: ``words`` it is about (stems), the ``head`` it negates,
    the ``subject`` (a stem or a name) a fact's negation is about, how it was said (``kind``:
    "verbal" for not/never, "np" for no/without, "zero" for none/0) and its words as written."""

    words: frozenset
    head: str
    kind: str
    raw: str
    verdict: bool = False  # it negates a verdict word: the verdict check covers it
    subject: str = ""
    named: bool = False  # ``subject`` is a name from the text, not a word
    after: frozenset = frozenset()  # (a sentence's) the words from the negation on
    clause: frozenset = frozenset()  # (a sentence's) every word of its clause
    # a zero's parts (#332): the words of what it counts (``mods``: "flagged" of "none of the 8
    # flagged hours"), what it says of them (``pred``, ``lead`` its first verb) and every word
    # of its list item (``ctx``, a fact's: "0 rule(s) skipped for missing inputs")
    mods: frozenset = frozenset()
    pred: frozenset = frozenset()
    lead: str = ""
    ctx: frozenset = frozenset()
    there: bool = False  # (a sentence's) "there were no flagged hours in the window" (#338)


@dataclass(frozen=True)
class _Count:
    """What a text says of a counted thing as a whole, none or a part (#336): ``how`` many of
    the ``noun`` things ("all": all of them, ``k`` their number when given; "def": "the 8
    flagged hours", the same; "none"; "part": ``k`` of ``total``; "count": ``k`` of them), the
    words naming which (``mods``, "flagged") and what it says of them (``pred``, ``lead`` its
    first verb), and the closed-family labels of each (``modfam``, ``predfam``)."""

    how: str
    k: object  # Decimal, or None: "all the hours" gives no number
    total: object  # Decimal or None: the number it is a part of
    noun: str
    mods: frozenset
    pred: frozenset
    lead: str
    modfam: frozenset
    predfam: frozenset
    raw: str
    subject: bool  # it opens its clause (a sentence's): what the clause says is said of it
    # the verdicts it lists after its verb ("returned a fault, a warn and an ok", #338)
    listed: frozenset = frozenset()
    # what it says is its clause's words before it ("The rule flagged all 37 samples", #338)
    back: bool = False
    at: int = field(default=-1, compare=False)  # (a sentence's) its first token (#339)


@dataclass(frozen=True)
class _Set:
    """A sentence's claim about every one of a set of items (#338, #339): ``said`` as written,
    ``k`` the number it gives (None: none given), the item ``noun`` and its ``kind`` (equip,
    point, meter, facility; "" for "both" or "each" with no noun: the kind the cited facts share),
    ``how`` it was said; its tokens ``start`` to ``end`` (inclusive) and ``scope`` (exclusive):
    what its clause says of it; ``neg`` for a negated set ("neither", "no AHU", "none of the
    AHUs"): each item must have the negation stated of it; ``names`` the items it names ("AHU-1
    and AHU-2", "neither A nor B"), ``slots`` the joined parts that name none ("the heating valve
    and the OA damper"); ``lead`` when it is a lead-in (a colon, a dash or a table cell after
    it); ``pred`` (a negated set's) the start of what is said of it, -1 when the checker cannot
    find it (the set then fails closed)."""

    said: str
    k: object
    noun: str
    kind: str
    how: str
    start: int = -1
    end: int = -1
    scope: int = -1
    neg: bool = False
    names: tuple = ()
    slots: int = 0
    lead: bool = False
    pred: int = -1
    pron: bool = False  # "each" or "both" with no noun of its own ("Each fails it")
    poss: bool = False  # its noun is a possessive ("each AHU's sensor"): its sensor
    opens: bool = False  # it opens its clause: what the clause says is said of it


@dataclass
class _Parsed:
    qty: list = field(default_factory=list)
    dates: set = field(default_factory=set)  # (y, m, d, (h, m, s) or None)
    times: set = field(default_factory=set)  # (h, m, s)
    ids: set = field(default_factory=set)  # names, lower case
    verdicts: set = field(default_factory=set)  # verdict classes, negation applied
    dirs: set = field(default_factory=set)  # direction classes
    negs: list = field(default_factory=list)  # _Neg: what it says is not so
    affirmed: set = field(default_factory=set)  # stems it states outside any negation
    words: set = field(default_factory=set)  # every content stem
    polar: set = field(default_factory=set)  # high / low / above / below
    quant: set = field(default_factory=set)  # "all" / "most"
    ranks: set = field(default_factory=set)  # issue ranks it names
    said_ranks: set = field(default_factory=set)  # those said as a rank ("the second issue")
    rules: set = field(default_factory=set)  # rule names it names
    marks: dict = field(default_factory=dict)  # name placeholder -> the name it stands for
    claim: bool = False  # it says its figures are a claim's ("the claim states 15%")
    wholes: set = field(default_factory=set)  # "all 11", "8 of the 8": counts said whole (#332)
    # (a sentence's) the words said of a whole count; (a fact's) a count given again, with the
    # words of each later mention: "14 points ...; 14 point(s) are below 90%" (all 14 are)
    whole_words: dict = field(default_factory=dict)
    sups: list = field(default_factory=list)  # (word, what it is said of): "worst: HW coil"
    stated: set = field(default_factory=set)  # verdicts stated, not only naming a finding
    vraw: dict = field(default_factory=dict)  # verdict class -> the words that said it
    segs: dict = field(default_factory=dict)  # a figure's token -> its words' span (#332)
    counts: list = field(default_factory=list)  # _Count: what it says of a counted thing (#336)
    causes: list = field(default_factory=list)  # (said, effect stems, cause stems) (#336)
    explains: bool = False  # it says "explained" in a word of its own, not as a cause
    # (a sentence's) what it says of every one of a set of items (#338, #339): :class:`_Set`,
    # "both AHUs" (k 2), "each AHU", "the AHUs ... all" (k None), "neither A nor B"
    sets: list = field(default_factory=list)
    # (a sentence's) the text the tokens were read from, names masked (#339): a negated set's
    # per-item sentence is cut from it
    masked: str = ""
    toks: list = field(default_factory=list)
    # (a sentence's) "all trusted", "they are all ok": all of them have this label (#338)
    all_labels: set = field(default_factory=set)
    late_at: set = field(default_factory=set)  # the "all" / "both" a counted thing took
    # (a sentence's) names joined as one subject ("AHU-1 and AHU-2 both fail ..."): ``(said,
    # names)``, each said the same of (#338)
    named: list = field(default_factory=list)


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"|“([^”]*)”')
_DATE = re.compile(r"(?<![\d\-])(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?(?!\d)")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_PATTERN_ID = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9_]*(?:[\-.][A-Za-z0-9_]+)*")
_TOKEN = re.compile(
    r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"|(?P<word>[A-Za-z]+(?:['’][A-Za-z]+)?(?:-[A-Za-z]+)*)"
    r"|(?P<sym>\S)"
)


def _free(m) -> str:
    """ "fault-free", "fault free", "free of faults": "no fault" when it names a counted thing
    (#336); "suspect-free" and "free of suspect points" a trust class's points (#338)."""
    if m.group(2):
        word = m.group(2)
        if _counted(word) and (m.group(0)[len(word)] == "-" or not word.lower().endswith("s")):
            return f"no {word}"
        if word.lower() in _TRUST_CLASSES:
            return f"no {word} points"
        return m.group(0)
    words = m.group(1).split()
    for i, w in enumerate(words):
        if _counted(w) or w.lower() in _TRUST_CLASSES:
            rest = " ".join(words[i + 1 :])
            if not _counted(w) and not (words[i + 1 :] and _counted(words[i + 1])):
                w += " points"  # "free of suspect": of suspect points
            return " ".join(["no", *words[:i], w] + ([rest] if rest else [])) + " "
    return m.group(0)


_TRUST_CLASSES = frozenset({"trusted", "untrusted", "suspect"})


def _fc_word(m) -> str:
    """A G36 fault condition ("FC10") as one word of letters, so it labels its figure (#336)."""
    return "fc" + "".join("abcdefghij"[int(d)] for d in m.group(1)) + "z"


#: a fault condition as :func:`_fc_word` writes it
_FC_WORD = re.compile(r"fc[a-j]{1,2}z")


#: spellings read as one form before parsing
_PRE = (
    (re.compile(r"\(s\)"), "s"),
    (re.compile(r"\bin\.?\s?H2O\b|\bin\.?\s?w\.?c\.?(?!\w)", re.I), " inwc"),
    (re.compile(r"\bpercentage[\s-]points?\b|\bpercent(?:age)?[\s-]pts?\b", re.I), "pp"),
    (re.compile(r"\bper\s+cent\b", re.I), "percent"),
    (re.compile(r"\+/-|\+-|\bplus or minus\b", re.I), "±"),
    (re.compile(r"\s*(?:->|→)\s*"), " to "),
    (re.compile("[−‒]"), "-"),
    (re.compile("º"), "°"),
)
#: spellings read as one form once names are masked (#336): "hot water" is the heating side
#: ("HW"); "fault-free" and "free of faults" are a zero of what they name; a G36 fault condition
#: ("FC10") is a label, not a figure
_PRE_WORDS = (
    (re.compile(r"\bhot[\s-]water\b", re.I), "HW"),
    (
        re.compile(
            r"\b(?:free|clear)\s+(?:of|from)\s+(?:any\s+)?((?:[A-Za-z]+\b\s*){1,3})|"
            r"\b([A-Za-z]+)(?:-|\s+)free\b(?!\s+(?:of|from)\b)",
            re.I,
        ),
        _free,
    ),
    (re.compile(r"(?<![A-Za-z0-9_])FC(\d{1,2})(?![A-Za-z0-9_])", re.I), _fc_word),
)


def _labelled_zero(m) -> str:
    """ "Faults on X: 0", "Number of suspect points: 0", "Rules passed: 5 of 5": the figure
    counts the label's thing (#338), read as "0 faults on X"; a trust class or a verdict alone
    is a label too (#339): "Suspect: 0" is "0 suspect points", "Passed: 5" is "5 ok", "Passed: 5
    rules" is "5 rules passed". A label that names neither is left as it is."""
    label = m.group("label").strip()
    num = m.group("num")
    tail = (m.groupdict().get("tail") or "").strip()
    words = re.findall(r"[A-Za-z]+", label)
    if len(words) > 8 or not words:
        return m.group(0)
    if any(_counted(w) for w in words):
        return f"{num} {label}" + (f" {tail}" if tail else "")
    if tail and _counted(tail.split()[0]):
        return f"{num} {tail} {label}"  # "Passed: 5 rules": 5 rules passed
    low = [w.lower() for w in words]
    if any(w in _TRUST_CLASSES for w in low) and not tail:
        return f"{num} {label} points"  # "Suspect: 0": no suspect points
    verdict = next(
        (i for i, w in enumerate(low) if _VALUE_VERDICT.get(_VERDICT_WORD.get(w, ""))), -1
    )
    if verdict >= 0 and not tail:
        rest = " ".join(words[:verdict] + words[verdict + 1 :])
        noun = _VERDICT_WORD[low[verdict]]
        return f"{num} {noun}" + (f" {rest}" if rest else "")  # "Failed: 0": 0 fault
    return m.group(0)


#: a verdict word as the noun a verdict count is written with ("passed" -> "ok", #339)
_VERDICT_WORD = {
    **_words("fault faults faulted faulty fail fails failed failing failure failures", "fault"),
    **_words("warn warns warned warning warnings", "warn"),
    **_words("ok pass passes passed passing healthy", "ok"),
    "info": "info",
}


def _cell_verdict(m) -> str:
    """A table row whose first cell is a count and whose next one a verdict ("| 5 of 5 | passed
    |"): a count of results with that verdict (#339), read as "5 of 5 results passed"."""
    words = re.findall(r"[A-Za-z]+", m.group("label"))
    if not words or len(words) > 4 or not any(w.lower() in _VERDICT_WORD for w in words):
        return m.group(0)
    return f"{m.group('num')} results {m.group('label').strip()}"


def _not_found(m) -> str:
    """ "Faults were not found", "Suspect points weren't detected": no such things (#338)."""
    if not _counted(m.group("head")):
        return m.group(0)
    np = re.sub(r"^(?:the|these|those|any)\s+", "", m.group("np"), flags=re.I)
    verb = m.group("verb") or "found"  # "faults are absent": none was found
    return f"{m.group('lead')}no {np}{m.group('head')} {m.group('be')} {verb}"


def _neither_nor(m) -> str:
    """ "neither suspect nor untrusted points": each label with the noun they share (#338)."""
    a, b, noun = m.group(1), m.group(2), m.group(3)
    if not _counted(noun) or _counted(a):
        return m.group(0)
    return f"neither {a} {noun} nor {b} {noun}"


def _parallel_at(m) -> str:
    """ "trusted at 0.97 and the supply-air temperature setpoint at 1.00": the second figure is
    what the first one's verb says, not a setpoint's value (#338)."""
    if not _verbish(m.group("verb").lower()):
        return m.group(0)
    return f"{m.group('head')}{m.group('np')} {m.group('verb')} at "


_NUMBER_WORDS = "|".join(
    w for w in _WORD_NUM if w not in ("half", "quarter", "twice", "double", "triple", "zero")
)
#: what a sentence's words are read as once names are masked (#338): "the majority of" and "the
#: bulk of" are "most of"; "over 9", "upwards of 9" and "in excess of 9" are "more than 9" where
#: "over" leads its clause or follows a verb (not "15.8% over 366 days"); "a single" (not "not a
#: single") is "one"; "neither suspect nor untrusted points" names the points twice; "Faults were
#: not found" and "There aren't any faults" are "no faults"; "Faults on X: 0" counts faults
_SENT_WORDS = (
    (
        re.compile(
            r"\b(?:the|a)\s+(?:vast\s+|great\s+|large\s+|clear\s+)?(?:majority|bulk)\s+of\b", re.I
        ),
        "most of",
    ),
    (re.compile(r"\b(?:the\s+)?(?:whole|entirety)\s+of\b", re.I), "all of"),
    (
        re.compile(rf"\b(?:upwards|in\s+excess)\s+of\s+(?=\d|(?:{_NUMBER_WORDS})\b)", re.I),
        "more than ",
    ),
    (  # "well over 3", "just over 3" (#339)
        re.compile(
            r"\b(?:well|just|slightly|comfortably|far|way|a\s+little|a\s+bit)\s+over\s+"
            rf"(?=\d|(?:{_NUMBER_WORDS})\b)",
            re.I,
        ),
        "more than ",
    ),
    (
        re.compile(
            r"(^|[;:(]\s*|\b(?:is|are|was|were|has|have|had|of|found|flagged|returned|"
            rf"reported|showed|shows)\s+)over\s+(?=\d|(?:{_NUMBER_WORDS})\b)",
            re.I,
        ),
        r"\1more than ",
    ),
    (re.compile(r"(?<![Nn]ot )\b(?:a|an)\s+(?:single|lone|sole)\b", re.I), "one"),
    (
        re.compile(
            r"(?P<head>\b(?P<verb>[A-Za-z]+)\s+at\s+\d[\d.,]*\s*,?\s+and\s+)"
            r"(?P<np>(?:the\s+|its\s+)?[^,;:\d]{1,60}?)\s+at\s+(?=\d)",
            re.I,
        ),
        _parallel_at,
    ),
    (re.compile(r"\bneither\s+([A-Za-z]+)\s+nor\s+([A-Za-z]+)\s+([A-Za-z]+)", re.I), _neither_nor),
    (
        re.compile(
            r"(?P<lead>^|[,;:]\s*|\b(?:and|but|so)\s+)(?P<np>(?:[A-Za-z-]+\s+){0,3}?)"
            r"(?P<head>[A-Za-z]+)\s+(?P<be>were|was|are|is|have\s+been|has\s+been)"
            r"(?:(?:\s+not|n['’]t)\s+(?P<verb>found|detected|seen|reported|returned|observed|"
            r"flagged|raised|listed|identified|recorded|present)|\s+(?P<absent>absent))\b",
            re.I,
        ),
        _not_found,
    ),
    (re.compile(r"\b(is|are|was|were|has|have|had)(?:\s+not|n['’]t)\s+any\b", re.I), r"\1 no"),
    (
        re.compile(
            r"^(?:(?:the\s+)?(?:number|count|total|tally)\s+of\s+)?(?P<label>[^:;,|—–]{1,80}?)"
            rf"\s*(?::|—|–|\s-)\s*(?P<num>(?:\d+(?:\.\d+)?|zero|{_NUMBER_WORDS})"
            rf"(?:\s+(?:out\s+)?of\s+(?:\d+|{_NUMBER_WORDS}))?)"
            r"(?P<tail>\s+[A-Za-z]+(?:[\s-][A-Za-z]+)?)?\s*\.?\s*\Z",
            re.I,
        ),
        _labelled_zero,
    ),
    (  # a table row "| Faults | 0 |"
        re.compile(
            r"^\|?\s*(?:(?:the\s+)?(?:number|count|total|tally)\s+of\s+)?(?P<label>[^|:;,]{1,80}?)"
            r"\s*\|\s*(?P<num>\d+(?:\.\d+)?(?:\s+(?:out\s+)?of\s+\d+)?)\s*\|?\s*\Z",
            re.I,
        ),
        _labelled_zero,
    ),
    (  # a table row "| 5 of 5 | passed |" (#339)
        re.compile(
            r"^\|?\s*(?P<num>\d+(?:\s+(?:out\s+)?of\s+\d+)?)\s*\|\s*(?P<label>[A-Za-z][^|:;,]{0,60}?)"
            r"\s*\|?\s*\Z",
            re.I,
        ),
        _cell_verdict,
    ),
    # "In the reference window there are 8 flagged hours": "there are 8 flagged hours in ..."
    (
        re.compile(
            r"^(?P<pre>(?:in|within|during|inside|across|on|at)\b[^,;:]*?),?\s+"
            r"(?P<there>there\s+(?:is|are|was|were)\b.*?)\s*(?P<end>[.!?]?)\s*\Z",
            re.I,
        ),
        r"\g<there> \g<pre>\g<end>",
    ),
)
#: what the masked text writes in place of a name (``QQNAME`` and letters: which one), a term,
#: a date and a time
_NAME, _TERM = "QQNAME", "QQTERM"
#: what ends a clause: a negation, a zero or a verdict's polarity reaches no further
_BOUNDARY = frozenset(",;:()[]{}!?.|/—–")


def _date(m) -> tuple:
    y, mo, d, hh, mm, ss = m.groups()
    return int(y), int(mo), int(d), ((int(hh), int(mm), int(ss or 0)) if hh else None)


class _Vocab:
    """What a sentence's names are read against (#328): ``names`` (lower case, longest first)
    a matched span of which is a name; ``aliases`` mapping a facility's registry name to its id;
    ``terms`` (rule, tool and field names: masked, never an equipment); ``rules`` (the rule
    names among them)."""

    def __init__(self, names=(), aliases=None, terms=(), rules=()):
        self.aliases = {str(k).lower(): str(v).lower() for k, v in (aliases or {}).items()}
        every = {str(n).lower() for n in names} | set(self.aliases)
        self.names = sorted(every, key=lambda n: (-len(n), n))
        self.terms = frozenset(str(t).lower() for t in terms or ())
        self.rules = frozenset(str(r).lower() for r in rules or ())
        # a rule written as words: "leaking-valve", or "leaking valve fault" (the finding's name)
        spelled = sorted((r for r in self.rules if "_" in r), key=lambda r: (-len(r), r))
        self.rule_rx = None
        if spelled:
            alt = "|".join(r"[\s-]".join(map(re.escape, r.split("_"))) for r in spelled)
            self.rule_rx = re.compile(
                rf"(?<![A-Za-z0-9_])(?:{alt})(?=-(?![A-Za-z])|[-\s](?:fault|finding|rule|check|"
                rf"warn|warning|issue|result)s?\b)|(?<![A-Za-z0-9_])(?:"
                + "|".join("-".join(map(re.escape, r.split("_"))) for r in spelled)
                + r")(?![A-Za-z0-9_\-])",
                re.I,
            )


_NO_VOCAB = _Vocab()


def _parse(text: str, names, *, fact: bool = False) -> _Parsed:
    """The claims of one sentence (or of one sentence of a fact's text, ``fact``): see the module
    doc. ``names``: a :class:`_Vocab`, or the subject names (lower case, longest first)."""
    vocab = names if isinstance(names, _Vocab) else _Vocab(names)
    p = _Parsed()
    s = _unlisted(_FACT_ID.sub(" ", _CITES.sub(" ", text)).strip())
    for rx, rep in _PRE:
        s = rx.sub(rep, s)

    def mark(name: str, kind: str = _NAME) -> str:
        i, tag = len(p.marks), ""
        while True:
            tag += chr(97 + i % 26)
            i //= 26
            if not i:
                break
        p.marks[kind + tag] = name
        return f" {kind}{tag} "

    def quoted(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        n = " ".join(inner.replace('\\"', '"').split()).lower()
        if n in vocab.terms:
            if n in vocab.rules:
                p.rules.add(n)
            return mark(n, _TERM)
        p.ids.add(vocab.aliases.get(n, n))
        return mark(n)

    def date(m):
        p.dates.add(_date(m))
        return mark(m.group(0), "QQDATE")

    def time(m):
        p.times.add((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
        return mark(m.group(0), "QQTIME")

    def ident(m):
        tok = m.group(0)
        low = tok.lower()
        if low in vocab.terms:
            if low in vocab.rules:
                p.rules.add(low)
            return mark(tok, _TERM)
        if (
            low in _NOT_IDS
            or _FC.fullmatch(low)
            or not ("_" in tok or any(c.isdigit() for c in tok))
        ):
            return tok
        p.ids.add(vocab.aliases.get(low, low))
        return mark(low)

    s = _QUOTED.sub(quoted, s)
    s = _DATE.sub(date, s)
    s = _TIME.sub(time, s)
    if vocab.rule_rx is not None:

        def spelled(m):
            p.rules.add(re.sub(r"[\s-]+", "_", m.group(0).lower()))
            return mark(m.group(0), _TERM)

        s = vocab.rule_rx.sub(spelled, s)
    low = s.lower()
    for n in vocab.names:
        if n in low:
            rx = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", re.I)
            if rx.search(s) and (fact or _plain(n)):
                p.ids.add(vocab.aliases.get(n, n))
                tag = mark(n)
                s = rx.sub(tag, s)  # letters and spaces only: no group references
                low = s.lower()
    s = _PATTERN_ID.sub(ident, s)
    for rx, sub in _PRE_WORDS:
        s = rx.sub(sub, s)
    if not fact:
        for rx, sub in _SENT_WORDS:
            s = rx.sub(sub, s)
    toks = [(m.lastgroup, m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(s)]
    if not fact:
        p.masked, p.toks = s, toks
    for i, (kind, raw, _a, _b) in enumerate(toks):
        share = False
        if kind == "num":
            value = _decimal(raw)
        elif kind == "word":
            w = raw.lower()
            value = _word_number(w)
            if w == "one" and not _one_counts(toks, i):
                value = None  # "the one", "the suspect one", "to pick one": a pronoun
            share = w in ("half", "quarter") and (
                i + 1 < len(toks) and toks[i + 1][1].lower() in ("of", *_DEFINITE)
            )  # "half of the 11 points": a share of them, read with their count (#338)
            raw = ""
        else:
            continue
        if value is not None:
            q = _quantity(toks, i, value, raw)
            if q is not None:
                p.qty.append(replace(q, share=True) if kind == "word" and share else q)
    if fact:
        _fill(p.qty, toks)
    else:
        _lead_in(p.qty, toks)
        _ellipsis(p.qty, toks)
    _claim_words(p, toks, fact=fact)
    return p


_PLAIN_CACHE: dict = {}


def _one_counts(toks: list, i: int) -> bool:
    """Whether "one" at ``i`` is a count ("one point", "one of the 24", "one is untrusted",
    "about one day") rather than a pronoun ("the one", "the suspect one", "not one tuned for
    this site", "to pick one")."""
    before = [t[1].lower() for t in toks[max(0, i - 2) : i] if t[0] == "word"]
    if before and (before[-1] in _ONE_PRONOUN or (len(before) == 2 and before[0] in _ARTICLES)):
        return False
    if before and before[-1] in ("not", "single"):
        return False  # "not one of the 8", "not a single one": none (#336)
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if nxt is None or nxt[0] != "word":
        return False
    w = nxt[1].lower()
    if w in ("of", "is", "was", "has", "had", "point", "more") or _scan_noun(toks, i + 1):
        return True
    return not w.endswith("ed") and w not in _NOUN_STOP and w not in _FUNCTION


def _plain(name: str) -> bool:
    """Whether a name from the data reads as a plain name (#328): no figure with a unit, verdict,
    direction, negation, quantifier or notice in it. Only a plain name shields its own words
    where a sentence writes it unquoted; any other name must be quoted to be a name."""
    hit = _PLAIN_CACHE.get(name)
    if hit is None:
        if re.search(r"[\[\]\":;]|\bnotice\b", name, re.I):
            hit = False
        else:
            p = _parse(name, ())
            hit = not (
                p.verdicts or p.dirs or p.negs or p.quant or p.claim or any(q.unit for q in p.qty)
            )
        if len(_PLAIN_CACHE) > 4096:
            _PLAIN_CACHE.clear()
        _PLAIN_CACHE[name] = hit
    return hit


def _lead_in(qty: list, toks: list) -> None:
    """A sentence that opens "Of (the) 92 points, ...": its bare counts after that are of those
    points ("Of the 92 points, 1 is suspect"), as a fact's list is (#336)."""
    words = [t[1].lower() for t in toks]
    j = 1 + (len(words) > 1 and words[1] in _DEFINITE)
    if not (words[:1] == ["of"] and j < len(toks) and toks[j][0] == "num"):
        return
    head = next((q for q in qty if q.at == j), None)
    comma = next((x for x in range(j, len(toks)) if toks[x][1] in ",:"), -1)
    if head is None or head.unit not in _NOUNS.values() or comma < 0:
        return
    for k, q in enumerate(qty):
        if q.at <= comma or q.year or q.role != "result" or q.sign:
            continue
        nxt = toks[q.at + 1][1].lower() if q.at + 1 < len(toks) else ""
        if not q.unit or (q.unit in _VERDICT_UNITS and _verbish(nxt)):
            # "Of the 3 rules, 1 returned a fault": 1 rule, not 1 fault (#339)
            qty[k] = replace(q, unit=head.unit, of_noun="")


def _ellipsis(qty: list, toks: list) -> None:
    """A bare count after "and" (or a comma) that a verb follows counts what the count before it
    counts (#338): "1 point is below the trust floor and 1 is suspect" (1 point is suspect),
    "one is untrusted and one is suspect"."""
    noun = ""
    for k, q in enumerate(qty):
        if q.unit in _NOUNS.values() and q.at >= 0:
            noun = q.unit
            continue
        i = q.at
        if not noun or q.unit or q.year or q.sign or q.role != "result" or i < 1:
            continue
        before = toks[i - 1][1].lower()
        after = toks[i + 1][1].lower() if i + 1 < len(toks) else ""
        if before in ("and", ",", "but", "while", "whereas") and after in (
            "is",
            "are",
            "was",
            "were",
        ):
            qty[k] = replace(q, unit=noun)
    # "1 suspect and 1 untrusted point": a labelled count before one that names its thing
    for k in range(len(qty) - 2, -1, -1):
        q, nxt = qty[k], qty[k + 1]
        i = q.at
        if q.unit or q.year or q.sign or i < 0 or nxt.unit not in _NOUNS.values() or nxt.at <= i:
            continue
        between = [t[1].lower() for t in toks[i + 1 : nxt.at]]
        if between and _FAMILY.get(between[0]) and set(between[1:]) <= {"and", ",", "or"}:
            qty[k] = replace(q, unit=nxt.unit)


def _decimal(raw: str):
    try:
        return Decimal(raw.rstrip(",").replace(",", ""))
    except InvalidOperation:
        return None


def _fill(qty: list, toks: list) -> None:
    """In a fact that lists figures, give a bare one what its neighbour names (#328): ``0.83 vs
    0.35 in. w.c.`` (the unit), ``86% / 0% of samples`` (what a share is of), ``9 trusted, 2
    suspect`` of ``11 points`` (the counted thing). A fact only: a sentence's own figures keep
    what the sentence says."""
    for k, q in enumerate(qty):
        nxt = qty[k + 1] if k + 1 < len(qty) else None
        if nxt is None or q.at < 0 or nxt.at <= q.at:
            continue
        between = toks[q.at + 1 : nxt.at]
        if any(t[0] == "sym" and t[1] in ",;:()" for t in between):
            continue
        words = [t[1].lower() for t in between if t[0] == "word"]
        if not q.unit and nxt.unit and not q.year and set(words) <= _LINKS:
            qty[k] = _Qty(q.value, q.sign, nxt.unit, q.base, q.role, False, q.at)
        elif q.unit == "%" == nxt.unit and not q.base and nxt.base and len(words) <= 2:
            qty[k] = _Qty(q.value, q.sign, "%", nxt.base, q.role, q.year, q.at)
    # "32 of 37 judged samples, 86.5%": the share is of those samples (#338)
    for k, q in enumerate(qty):
        if q.unit != "%" or q.base or q.at < 0:
            continue
        for a, b in zip(qty[:k], qty[1:k]):
            if (
                b.unit in _NOUNS.values()
                and 0 <= a.at < b.at < q.at
                and b.value
                and toks[a.at + 1][1].lower() == "of"
                and round(100 * a.value / b.value, max(0, -q.value.as_tuple().exponent)) == q.value
            ):
                qty[k] = replace(q, base=b.unit)
    noun, more = "", []
    for q in qty:
        if q.unit in _NOUNS.values():
            noun = q.unit
        elif not q.unit and noun and not q.year and q.role == "result":
            more.append(_Qty(q.value, q.sign, noun, q.base, q.role, False, q.at))
    qty.extend(more)


#: what may stand between two figures that share a unit ("0.83 vs expected 0.35 in. w.c.")
_LINKS = frozenset("vs versus expected and or to from".split())


def _clauses(toks: list, boundary=_BOUNDARY) -> list:
    """The clause of each token (-1: a boundary): punctuation and dashes end a clause."""
    out, c = [], 0
    for i, (kind, raw, _a, b) in enumerate(toks):
        if kind == "sym" and (
            raw in boundary
            or (raw == "-" and not (i + 1 < len(toks) and toks[i + 1][2] == b))
            or (raw == "-" and i + 1 < len(toks) and toks[i + 1][1] == "-")
        ):
            c += 1
            out.append(-1)
            continue
        out.append(c)
    return out


#: what ends a fact's list item: a zero's words reach past a parenthesis or a slash ("0
#: rule(s)) skipped for missing inputs"), not past a comma, colon or semicolon (#332)
_ITEM_BOUNDARY = frozenset(",;:!?.|—–")


#: G36 fault conditions ("FC1"), named like equipment
_FC = re.compile(r"fc\d{1,2}")


def _content(raw: str) -> tuple:
    """The stems a word contributes (a hyphenated word: each part's), or () for a word with no
    claim of its own."""
    if raw.startswith("QQ"):
        return ()  # a name, a term, a date or a time
    w = raw.lower()
    if w in _FUNCTION or w in _NEG or _negates(w) or _word_number(w) is not None:
        return ()
    parts = [x for x in re.split(r"[-'’]", w) if x and x not in _FUNCTION and len(x) > 1]
    return tuple(_stem(x) for x in parts)


def _raw(toks: list, a: int, b: int, marks: dict) -> str:
    """Tokens ``a`` to ``b`` (inclusive) as written: a name or term placeholder as its name."""
    out = []
    for x in range(max(0, a), min(b, len(toks) - 1) + 1):
        tok = toks[x][1]
        out.append(marks.get(tok, tok) if tok.startswith("QQ") else tok)
    s = re.sub(r"\s+(['’])\s+s\b", r"\1s", " ".join(out))
    return re.sub(r"\s+([%°,;:)])|([(])\s+", lambda m: m.group(1) or m.group(2), s)


def _figure_words(
    p: _Parsed, toks: list, cont: list, words: list, clause: list, fact: bool = False
) -> None:
    """Each figure's words (#332): those of its clause since the figure before it (``pre``) and
    up to the figure after it (``post``), without counted things and units; and whether it is
    the total another figure is "of" (``total``: the 11 of "2 of the 11 points")."""
    n = len(toks)
    ats = sorted({q.at for q in p.qty if 0 <= q.at < n})

    def stems(a, b, near=0):
        """the words of tokens a..b-1; a fact's figure: the ``near`` nearest (-: before it)"""
        xs = [
            x
            for x in range(a, b)
            if toks[x][0] == "word" and cont[x] and not (_counted(words[x]) or words[x] in _UNITS)
        ]
        if fact and near:
            xs = xs[near:] if near < 0 else xs[:near]
        return frozenset(s for x in xs for s in cont[x] if s not in _SHARE_WORDS)

    groups = _clauses(toks, _LIST_BOUNDARY)
    units: dict = {}
    for q in p.qty:
        if q.unit or q.at not in units:
            units[q.at] = q.unit
    seg = {}
    for i, at in enumerate(ats):
        c = clause[at]
        lo = ats[i - 1] + 1 if i else 0
        hi = ats[i + 1] if i + 1 < len(ats) else n
        a = at
        while a > lo and clause[a - 1] == c and words[a - 1] not in _CONJ:
            a -= 1
        b = at + 1
        while b < hi and clause[b] == c and not (words[b] in _CONJ and hi < n and clause[hi] == c):
            b += 1
        p.segs[at] = (a, b)
        x = at - 1
        while x >= 0 and words[x] in ("the", "its", "their", "these", "those", "all"):
            x -= 1
        if x >= 2 and words[x] == "of" and words[x - 1] == "out":
            x -= 1  # "9 out of 11 points" (#336)
        total = (
            x >= 1
            and words[x] in ("of", "out")
            and (
                toks[x - 1][0] == "num"
                or words[x - 1] in _OF_TOTAL
                or bool(_word_number(words[x - 1]))
            )
        ) or (x >= 0 and words[x] == "of" and (x == 0 or toks[x - 1][0] == "sym"))  # "Of the 11"
        # its closed-family labels (#336): the nearest of each family in its own words, then
        # in its list, past a parenthesis or a slash, back to the figure before it and on to
        # the one after (not past a comma or "and": those words are another figure's); "22 of
        # 415 points are suspect": on past its total
        g = groups[at]
        fams: dict = {}
        # past a figure of another unit ("FC10 ... 100% of applicable hours (1628 h)"), not one
        # of its own (those have labels of their own)
        same = [x for x in ats if units.get(x) == units.get(at)]
        lo = max((x + 1 for x in same if x < at), default=0)
        hi = min((x for x in same if x > at), default=n)
        after = hi
        y = at + 1 + (at + 1 < n and words[at + 1] == "out")
        if y < n and words[y] == "of":
            y += 1
            while y < n and words[y] in _DEFINITE:
                y += 1
            if y == hi:
                after = min((x for x in ats if x > hi and units.get(x) == units.get(at)), default=n)
        order = [*range(at - 1, a - 1, -1), *range(at + 1, b)]
        # not into the next figure's own words: "26.7% of hours (CHW 85%, HHW 27%)"
        own = after
        while (
            own > at + 1
            and own < n
            and clause[own - 1] == clause[own]
            and words[own - 1] not in _CONJ
        ):
            own -= 1
        for step, end in ((-1, lo - 1), (1, own)):
            for x in range(at + step, end, step):
                if groups[x] != g or words[x] in _CONJ or toks[x][1] == ",":
                    break
                order.append(x)
        for x in order:
            hit = _family(words, x)
            if hit is not None and hit[0] != "verdict":
                fams.setdefault(hit[0], hit)

        seg[at] = (stems(a, at, -2), stems(at + 1, b, 2), total, g, fams)
    out = []
    for q in p.qty:
        if q.at not in seg:
            out.append(q)
            continue
        pre, post, total, g, fams = seg[q.at]
        if q.unit not in _NOUNS.values():  # a trust class labels a count of points
            fams = {f: lab for f, lab in fams.items() if f != "trust"}
        if q.unit in _VERDICT_UNITS:  # "1 ok": what it counts is its label
            fams = {**fams, "verdict": ("verdict", q.unit)}
        out.append(replace(q, pre=pre, post=post, total=total, grp=g, fam=frozenset(fams.values())))
    p.qty[:] = out


#: the closed label families (#336): a word -> ``(family, label)``; "heat" / "cool" (the side,
#: :data:`_SYNONYM` maps its spellings) and a fault condition (``fc...z``) are found by
#: :func:`_family`
_FAMILY = {
    **{w: ("trust", w) for w in ("trusted", "untrusted", "suspect")},
    **_words("fault faults faulted faulty fail fails failed failing", ("verdict", "fault")),
    **_words("warn warns warning warnings", ("verdict", "warn")),
    "info": ("verdict", "info"),
    **_words("ok pass passes passed passing healthy", ("verdict", "ok")),
}
_VERDICT_UNITS = frozenset({"fault", "warn", "info", "ok"})
_FAMILY_STEMS = frozenset(_stem(w) for w in _FAMILY)


def _family(words: list, i: int):
    """The closed-family label ``(family, label)`` word ``i`` gives, or None (#336): a trust
    class, a verdict, a fault condition ("FC10") or a side ("HW-leak", "the cooling valve",
    "reheat"; not "fan heat")."""
    w = words[i]
    if not w:
        return None
    hit = _FAMILY.get(w)
    if hit is not None:
        return hit
    if _FC_WORD.fullmatch(w):
        return ("fc", w)
    parts = w.split("-")
    for x, part in enumerate(parts):
        side = _stem(part)
        if side in ("heat", "cool"):
            if (parts[x - 1] if x else (words[i - 1] if i else "")) == "fan":
                continue  # "2F fan heat": the fan's, no side
            return ("side", side)
    return None


#: what ends a list of figures read together ("9 trusted, 2 suspect, 0 untrusted"), and the
#: words that hand a clause from one figure to the next ("86% and the cooling valve 0%")
_LIST_BOUNDARY = frozenset(";:!?.|—–")
_CONJ = frozenset("and or but while whereas vs versus against".split())


#: words that take a part of a total: "none of the 11", "all of the 8", "one of the 24"
_OF_TOTAL = frozenset("none all each every one some any most many few several both".split())


def _number_at(toks: list, words: list, j: int):
    """The figure at token ``j`` (digits or a number in words), or None."""
    if j >= len(toks):
        return None
    if toks[j][0] == "num":
        return _decimal(toks[j][1])
    return _word_number(words[j]) if toks[j][0] == "word" else None


def _verbish(word: str) -> bool:
    return word in _STATING or (word.endswith("ed") and len(word) > 4)


def _zero_parts(toks, cont, words, scope: list, end: int, k: int, bound: list):
    """``(noun index, mods, pred, lead)`` of a zero whose counted words are ``scope``: the words
    before its counted noun, those after it to the end of its clause, and its first verb (or
    word) there."""
    noun_at = next((j for j in scope if _counted(words[j])), -1)
    mods: frozenset
    if noun_at < 0:
        mods, start = frozenset(), (scope[0] if scope else end)
    else:
        mods = frozenset(s for j in scope if j < noun_at for s in cont[j])
        start = noun_at + 1
    pred_at = []
    j = start
    while j < len(toks) and bound[j] == bound[k] and toks[j][0] != "num":
        if toks[j][0] == "word" and cont[j]:
            pred_at.append(j)
        j += 1
    pred = frozenset(s for j in pred_at for s in cont[j])
    lead_at = next((j for j in pred_at if _verbish(words[j])), pred_at[0] if pred_at else -1)
    lead = cont[lead_at][-1] if lead_at >= 0 else ""
    return noun_at, mods, pred, lead


def _claim_words(p: _Parsed, toks: list, *, fact: bool) -> None:
    """The verdicts (with their polarity), directions, negations, zeros, quantifiers, ranks and
    opposites of a text's words (see the module doc)."""
    clause = _clauses(toks)
    item = _clauses(toks, _ITEM_BOUNDARY)
    words = [t[1].lower() if t[0] == "word" else "" for t in toks]
    cont = [_content(t[1]) if t[0] == "word" else () for t in toks]
    n = len(toks)

    def in_clause(i, j):  # tokens i..j-1 share a clause
        c = clause[i]
        return c >= 0 and all(clause[x] == c for x in range(min(i, j), max(i, j)))

    # "Of the 8 flagged hours, none falls ...": the lead-in names what its clauses count (#336)
    lead_in: set = set()
    if not fact and n > 2 and words[0] == "of":
        lead_in = {s for x in range(n) if clause[x] == clause[0] for s in cont[x]}

    def clause_words(i) -> set:
        c = clause[i]
        return {s for x in range(n) if clause[x] == c for s in cont[x]} | lead_in

    def hypothetical(k) -> bool:
        if k and words[k - 1] == "to":
            return True
        x = k - 1
        while x >= 0 and clause[x] == clause[k]:
            if words[x] in _HYPO:
                return True
            x -= 1
        return False

    def said_of(k) -> frozenset:  # what a whole count says of them, without what it counts
        words = frozenset(clause_words(k)) - _GENERIC - {"all", "every", "each"}
        return words - (_NOUN_STEMS - _VERDICT_NOUNS)

    def negated_before(k) -> int:
        back = [x for x in range(max(0, k - 3), k) if in_clause(x, k + 1)]
        return next((x for x in reversed(back) if _negates(words[x])), -1)

    for s in cont:
        p.words.update(s)
    # a figure is a claim's only where its own clause says so ("the claim states 15%, and the
    # retrofit saved 15%": the second is a measured statement)
    claimed = {
        clause[x]
        for x in range(n)
        if re.split(r"['’]", words[x])[0] in _CLAIM_WORDS and clause[x] >= 0  # "the claim's"
    }
    # "The claim names 2017 [A1], 12% [A2] and 2016 [A3]": a list of figures after it goes on
    # with its clause (#338)
    for cl in range(max(clause, default=0) + 1):
        if cl - 1 in claimed and cl not in claimed:
            xs = [x for x in range(n) if clause[x] == cl]
            colon = bool(xs) and xs[0] > 0 and toks[xs[0] - 1][1] == ":"  # "The claim: it ..."
            if colon or (
                xs
                and any(toks[x][0] == "num" for x in xs)
                and not any(cont[x] and words[x] not in _UNITS for x in xs)
            ):
                claimed.add(cl)
    # "The 12% figure: it is the claim's own number": a label of the figure before it, too
    for cl in sorted(claimed):
        xs = [x for x in range(n) if clause[x] == cl - 1]
        if (
            xs
            and xs[-1] + 1 < n
            and toks[xs[-1] + 1][1] == ":"
            and any(toks[x][0] == "num" for x in xs)
            and not any(
                cont[x] and words[x] not in _UNITS and words[x] not in _FIGURE_WORDS for x in xs
            )
        ):
            claimed.add(cl - 1)
    p.qty[:] = [
        replace(q, claim=True) if 0 <= q.at < n and clause[q.at] in claimed else q for q in p.qty
    ]
    _figure_words(p, toks, cont, words, clause, fact)
    if fact:  # "corrected ... by -3.0 °F": a correction (#336)
        corr = set()
        for q in p.qty:
            x = q.at - 1
            while x >= 0 and toks[x][1] in "+-":
                x -= 1
            if (
                x >= 0
                and words[x] == "by"
                and any("correct" in cont[y] for y in range(max(0, x - 12), x))
            ):
                corr.add(q.at)
        p.qty[:] = [replace(q, flip=True) if q.at in corr else q for q in p.qty]
    else:  # "with the +3.0 °F bias taken off": that correction (#336)
        took = any(
            (
                words[x] in ("taken", "take", "takes", "taking", "took", "backed", "back")
                and x + 1 < n
                and words[x + 1] in ("off", "out")
            )
            or words[x] in ("removed", "remove", "removing", "subtracted", "subtract")
            for x in range(n)
        )
        if took:
            p.qty[:] = [
                replace(q, flip=True)
                if q.at >= 0
                and q.sign in ("+", "-")
                and any(
                    words[x] in ("bias", "offset")
                    for x in range(max(0, q.at - 4), min(n, q.at + 5))
                    if clause[x] == clause[q.at]
                )
                else q
                for q in p.qty
            ]
    negated: set = set()  # token indices inside a negation
    zeroed: set = set()  # clauses with a zero in them: a verdict after it is what it counts
    last_noun = ""
    q_at: dict = {}
    for q in p.qty:  # a figure as the text writes it (not the counted thing _fill gave it)
        if q.at >= 0:
            q_at.setdefault(q.at, q)
    # "HW-leak 0% / CHW-leak 0% of the samples": a share listed before one that says what it is
    # of is of that too, for its zero (a fact's figures have it already, from _fill)
    share_of: dict = {}
    for a, b in zip(p.qty, p.qty[1:]):
        if a.unit == "%" == b.unit and not a.base and b.base and 0 <= a.at < b.at:
            between = toks[a.at + 1 : b.at]
            if not any(t[0] == "sym" and t[1] in ",;:()" for t in between) and (
                sum(1 for t in between if t[0] == "word") <= 2
            ):
                share_of[a.at] = b.base
    last_word = max((x for x in range(n) if toks[x][0] in ("word", "num")), default=-1)
    for k in range(n):
        kind = toks[k][0]
        w = words[k]
        if kind == "num" or (kind == "word" and k in q_at):
            q = q_at.get(k)
            if q is not None and q.unit in _NOUNS.values():
                last_noun = q.unit
            if q is not None and not q.base and k in share_of:
                q = replace(q, base=share_of[k])
            reading = q is not None and not q.unit and (q.sign or "." in toks[k][1])
            if q is not None and q.value == 0 and q.unit == "%" and not q.base:
                # "CHW-leak 0%": what it is a share of left out; its label is not affirmed
                x = k - 1
                while x >= 0 and clause[x] == clause[k] and k - x <= 3:
                    negated.add(x)
                    x -= 1
                # "0% heating and 0% cooling leak": or the words after it, to the next figure
                # (#338)
                x = k + 1 + (k + 1 < n and toks[k + 1][1] == "%")
                while (
                    x < n
                    and clause[x] == clause[k]
                    and x - k <= 4
                    and toks[x][0] == "word"
                    and words[x] not in _CONJ
                    and words[x] != "of"
                ):
                    negated.add(x)
                    x += 1
            elif (
                q is not None
                and q.value == 0
                and not reading  # "bias +0.0": a reading, not a count of none (#332)
                and (q.unit in _COUNT_UNITS or not q.unit or q.base)
            ):
                seg = p.segs.get(k) if not fact else None
                near = clause_words
                if seg:  # a sentence's zero is read with its own figure's words (#332)

                    def near(_i, a=seg[0], b=seg[1]):
                        return {s for x in range(a, b) for s in cont[x]}

                neg = _zero(toks, k, q, cont, item, words, last_noun, fact, near, p.marks)
                if neg is not None:
                    p.negs.append(neg)
                    negated.update(_neg_span(k, neg, clause))
                    if neg.kind == "zero":
                        zeroed.add(clause[k])
            # "8 of the 8 flagged hours": the whole of them (#332)
            if q is not None and k + 1 < n and words[k + 1] == "of":
                j = k + 2
                while j < n and words[j] in ("the", "its", "their", "these", "those"):
                    j += 1
                if _number_at(toks, words, j) == q.value and q.value:
                    p.wholes.add(q.value)
                    p.whole_words.setdefault(q.value, said_of(k))
            if kind == "num" and k and toks[k - 1][1] == "#" and toks[k][1].isdigit():
                p.ranks.add(int(toks[k][1]))  # "is #1"
                p.said_ranks.add(int(toks[k][1]))
            continue
        if kind != "word":
            continue
        nxt = words[k + 1] if k + 1 < n else ""
        # a verdict, with its polarity (a negation just before it in its clause)
        cls = _VERDICT.get(w)
        if cls is not None and nxt in _SETTING:
            cls = None  # "the fault threshold"
        if cls == "ok" and w.startswith("pass") and nxt in _PASSES_WHAT:
            cls = None  # "passes flow when closed"
        if cls == "confidence" and (
            nxt in ("level", "interval") or (k and toks[k - 1][1] == "%") or (nxt and nxt == "%")
        ):
            cls = None  # "at 90% confidence": a statistical level
        if cls == "certain" and k and words[k - 1] == "make":
            cls = None  # "make sure": advice
        if cls is not None and k and words[k - 1] in ("least", "most", "less", "more"):
            cls = None  # "the least trusted point": a rank or a comparison, not a verdict (#336)
        if cls is None and _still_holds(toks, words, k):
            cls = "persists"  # "remains valid", "still holds": the finding persists (#336)
        if cls is not None and k and _number_at(toks, words, k - 1) is not None:
            # "1 fault, 0 info": what a sentence's figure counts, matched with it (#332)
            cls = cls if fact else None
        if cls is not None and clause[k] in zeroed and not fact:
            cls = None  # "0 rules returned a fault", "no faults": the zero says it (#332)
        if cls is not None and not hypothetical(k):
            x = negated_before(k)
            if x >= 0:
                cls = _FLIP.get(cls, f"not {cls}")
            p.verdicts.add(cls)
            p.explains = p.explains or cls == "explained"
            p.vraw.setdefault(cls, f"{toks[x][1]} {toks[k][1]}" if x >= 0 else toks[k][1])
            # "the leaking_valve fault", "leaking_valve (fault)": the finding's name (#332)
            prev = toks[k - 1][1] if k else ""
            if prev == "(" and k > 1:
                prev = toks[k - 2][1]
            if not prev.startswith("QQ"):
                p.stated.add(cls)
        # a cause or an effect (#332): a claim even where hedged ("would clear")
        if (w, nxt) in _CAUSAL_PAIRS:
            x = negated_before(k)
            c = "persists" if x >= 0 else "explained"
            p.explains = p.explains or (w, nxt) not in _CAUSE_AFTER  # "accounts for" (#336)
            p.verdicts.add(c)
            p.stated.add(c)
            p.vraw.setdefault(c, f"{toks[x][1] + ' ' if x >= 0 else ''}{toks[k][1]} {nxt}")
        d = _DIRECTION.get(w)
        if d is not None and w.startswith(("fall", "fell")) and nxt in _PLACE:
            d = None  # "the spans fall on 2025-05-14", "fall outside the window"
        if d is not None and w in ("fall", "falls", "falling") and nxt in _COMPARE:
            d = None  # "no point falls below the trust floor": where it is, not a decrease (#339)
        if d == "down" and w.startswith(("sav", "avoid", "reduc")):
            # "avoided energy -21,825": a saving below zero is an increase (#339), in a fact
            # and in a sentence alike
            fig = None
            if k and words[k - 1] in ("negative", "minus"):
                d = "up"  # "a negative avoided energy", "negative savings": an increase (#339)
            for x in range(k + 1, min(n, k + 5)):
                if clause[x] != clause[k]:
                    break
                fig = q_at.get(x)
                if fig is not None:
                    d = "up" if fig.sign == "-" else d
                    break
            if fig is None and d == "down" and w in ("saving", "savings"):
                # "this saving is for information only": the result named, a decrease unless
                # a fact gives it below zero (:class:`_FactClaims`); a sentence's is a fact's
                # that names its saving, whatever its sign
                d = "down?"
        if d is None and w in ("more", "less", "fewer") and not fact:
            # "used more energy than its baseline": an increase on its baseline (#339)
            ahead = words[k + 1 : k + 6]
            if (
                "than" in ahead
                and ahead.index("than") <= 3
                and any(x.startswith("baseline") for x in words[k + 2 : k + 8])
            ):
                d = "up" if w == "more" else "down"
        if d in ("up", "down") and negated_before(k) >= 0:
            d = f"not {d}"  # "did not save energy": an increase or nothing saved (#339)
        if d is not None and not hypothetical(k):
            p.dirs.add(d)
        if w in _CLAIM_WORDS:
            p.claim = True
        pol = _POLAR.get(w)
        if w in ("above", "below") and (
            k + 1 >= n or toks[k + 1][1] in ",;:.)]!?" or nxt in ("and", "or", "but")
        ):
            pol = None  # "the figures above", "see below": a place in the text (#336)
        if w == "same" and nxt in _WHEN_NOUNS:
            pol = "same"  # "on the same days as" against "on other days than" (#332)
        elif w in ("other", "different") and nxt in _WHEN_NOUNS:
            pol = "other"
        if pol is not None:
            p.polar.add(pol)
        qc = _QUANT.get(w)
        if w in ("all", "every", "each") and not (k and words[k - 1] in ("at", "after", "in")):
            j = k + 1  # "all 11 points", "each of the 8", "every one of the 37" (#332)
            while j < n and words[j] in ("of", "the", "its", "their", "these", "those", "one"):
                j += 1
            v = _number_at(toks, words, j)
            if v and (w == "all" or nxt in ("of", "one")):
                p.wholes.add(v)
                p.whole_words.setdefault(v, said_of(k))
        if qc == "all" and not (k and words[k - 1] in ("at", "after", "in", "of", "above")):
            # "all of the samples", "all 4 issues", "every check": not "all good"
            after = words[k + 2] if k + 2 < n else ""
            if nxt == "of" and after in ("it", "them", "this", "that"):
                pass  # "all of it": a share of one whole, not a count of things
            elif k + 1 < n and toks[k + 1][0] == "num":
                pass  # "all 14 are untrusted": the figure is checked, as a whole one
            elif _whole_of(toks, words, k):
                pass  # "all of the 17 points", "every one of the 17": checked as a whole (#336)
            elif w == "every" or nxt in _ALL_BEFORE:
                p.quant.add("all")
            elif _FAMILY.get(nxt) and not fact:
                pass  # "all ok", "all trusted": a label, read with the counts (#338)
            elif _counted(nxt) or nxt.endswith("s"):
                p.quant.add("all")
        elif qc == "most" and (nxt == "of" or _noun(nxt) or w == "majority"):
            if not (k and words[k - 1] in ("the", "at")):
                p.quant.add("most")
        rank, stated = _rank(toks, words, k)
        p.ranks.update(rank)
        if stated:
            p.said_ranks.update(rank)
        sup = _superlative(toks, words, cont, k)
        if sup is not None:
            p.sups.append(sup)
        if _negates(w) and not (
            nxt in ("only", "just")
            or (nxt in ("more", "less", "fewer") and k + 2 < n and words[k + 2] == "than")
            or hypothetical(k)  # "whether they are not stuck", "should not be replaced"
        ):
            if w in ("none", "nothing", "nobody"):  # a fact's reaches past a parenthesis
                neg = _none_of(toks, k, cont, item, words, last_noun, fact, clause_words, p.marks)
            else:
                neg = _negation(
                    toks, k, cont, clause, words, last_noun, fact, clause_words, p.marks
                )
            if neg is not None:
                p.negs.append(neg)
                negated.update(_neg_span(k, neg, clause))
                if neg.kind == "zero":
                    zeroed.add(clause[k])
            elif k == last_word and not fact:
                # "<the fact's words> -- not": a closing "not" denies all before it (#332)
                p.negs.append(_Neg(frozenset({"--"}), "--", "frame", toks[k][1]))
    for k in range(n):
        if k not in negated:
            p.affirmed.update(cont[k])
    _counts(p, toks, words, cont, clause, item, fact)
    if not fact:
        _sets(p, toks, words, cont, clause)
        _alls(p, toks, words, cont, clause)
    _causes(p, toks, words, cont, clause, fact)
    if fact:  # a count given again, and the words of its later mentions
        at: dict = {}
        for q in sorted(q_at.values(), key=lambda q: q.at):
            if q.value and q.role == "result":
                at.setdefault(q.value, []).append(q)
        for v, qs in at.items():
            if len(qs) > 1:
                p.whole_words[v] = frozenset().union(*(q.pre | q.post for q in qs[1:]))


_CAUSAL_PAIRS = frozenset(_CAUSAL)
#: what a label before a colon may call its figure ("The 12% figure: ...", #338)
_FIGURE_WORDS = frozenset("figure number value share percentage".split())
#: a cause named after its effect (#336): "declined because of the untrusted input", "is due to
#: the offset"; a fact states one with these or a bare "because"
_CAUSE_AFTER = frozenset(
    {
        ("due", "to"),
        ("caused", "by"),
        ("because", "of"),
        ("attributable", "to"),
        ("results", "from"),
        ("explained", "by"),
    }
)


def _causes(p: _Parsed, toks, words, cont, clause, fact: bool) -> None:
    """The causes a text states, as ``(said, effect stems, cause stems)`` (#336): a fact's
    "declined because an input scored below the trust floor ...", a sentence's "declined
    because of its untrusted input" (or, opening its clause, "Because of the untrusted input,
    the check declined")."""
    n = len(toks)
    for k in range(n):
        w, nxt = words[k], words[k + 1] if k + 1 < n else ""
        if (w, nxt) not in _CAUSE_AFTER and not (fact and w == "because"):
            continue
        m = k + (1 if w == "because" and nxt != "of" else 2)
        if fact:  # its sentence: what comes before the marker is the effect
            cause, effect = range(m, n), range(0, k)
        else:
            end = m
            while end < n and clause[end] == clause[k]:
                end += 1
            cause = range(m, end)
            opens = not any(cont[x] for x in range(k) if clause[x] == clause[k])
            effect = range(end, n) if opens else range(0, k)
        p.causes.append(
            (
                f"{toks[k][1]} {nxt}",
                frozenset(s for x in effect for s in cont[x]),
                frozenset(s for x in cause for s in cont[x]),
            )
        )


#: what starts another clause inside a counted thing's predicate (#336)
_SUBORDINATE = _RELATIVE | frozenset(
    "when while whereas but although though because so since if unless as".split()
)
#: the comparisons a predicate of a counted thing may be ("are below 90%", #336)
_COMPARE = frozenset("above below under over outside beyond".split())
#: what may stand before a counted thing said as a whole (#336): "the 8 flagged hours"
_DEFINITE = frozenset("the these those its their".split())
#: what ends the walk back over a counted thing's own words ("the 8 flagged hours")
_NP_STOP = _DEFINITE | frozenset(
    "of out all every each both none no zero not any single a an one some most many few "
    "several".split()
)


def _quantifier(toks, words, y: int):
    """``(how, k, start)`` of a quantifier ending at token ``y`` (#336): "all", "each", "every
    one" (all of them), "both" (all 2), "none", "no", "zero", "not one", "not a single (one)",
    "not any" (none), a number (that many), else None."""
    if y >= 1 and toks[y][1] == "%" and toks[y - 1][0] == "num":
        return "pct", _decimal(toks[y - 1][1]), y - 1  # "86.5% of the samples" (#338)
    if y < 0 or toks[y][0] == "sym":
        return None
    w = words[y]
    back = [words[x] if x >= 0 else "" for x in (y - 1, y - 2, y - 3)]
    if w in ("all", "each", "every"):
        return "all", None, y
    if w == "one" and back[0] in ("each", "every"):
        return "all", None, y - 1
    if w == "both":
        return "all", Decimal(2), y
    if w in ("none", "no", "zero", "neither", "nor"):
        return "none", Decimal(0), y
    if w == "one" and back[0] == "not":
        return "none", Decimal(0), y - 1
    if w == "one" and back[:3] == ["single", "a", "not"]:
        return "none", Decimal(0), y - 3
    if w == "single" and back[:2] == ["a", "not"]:
        return "none", Decimal(0), y - 2
    if w == "any" and back[0] == "not":
        return "none", Decimal(0), y - 1
    if w in ("some", "several", "many") or (w == "few" and back[0] == "a"):
        return "some", None, y - (w == "few")
    if w == "most":
        return "most", None, y
    if w in ("half", "quarter"):  # "at least half of the 11", "fewer than half of the 106"
        f = Decimal("0.5") if w == "half" else Decimal("0.25")
        if back[0] == "a":  # "fewer than a quarter of the 106" (#339)
            y -= 1
            back = [*back[1:], words[y - 3] if y >= 3 else ""]
        if back[:2] == ["least", "at"]:
            return "frac>=", f, y - 2
        if back[:2] == ["most", "at"]:
            return "frac<=", f, y - 2
        if back[0] == "than" and back[1] in ("more", "greater"):
            return "frac>", f, y - 2
        if back[0] == "than" and back[1] in ("fewer", "less"):
            return "frac<", f, y - 2
        if back[0] in ("over", "above"):
            return "frac>", f, y - 1
        if back[0] in ("under", "below"):
            return "frac<", f, y - 1
        return "frac", f, y
    v = _number_at(toks, words, y)
    if v is not None:
        return "num", v, y
    return None


def _counts(p: _Parsed, toks, words, cont, clause, item, fact: bool) -> None:
    """What the text says of each counted thing as a whole, none or a part (#336), as
    :class:`_Count`: "all 11 points", "the 8 flagged hours ... all fall" (a quantifier after
    the noun), "The 11 points are trusted" (none given: the definite "the 11" is all of them),
    "8 out of 8", "both", "none of the 8", "no rule", "not a single fault", "20 of the 106". A
    fact's are those it says with a quantifier: none, a part of a total, or all of them."""
    n = len(toks)
    groups = _clauses(toks, _LIST_BOUNDARY)
    seen: dict = {}  # (a fact's) the first count of each counted thing: what a later one is of
    # "Of the 11 points, all are trusted": a quantifier standing alone is of the lead-in's
    lead_noun, lead_n = "", None
    if not fact and words[:1] == ["of"]:
        y = 1 + (n > 1 and words[1] in _DEFINITE)
        v = _number_at(toks, words, y) if y < n else None
        if v is not None and y + 1 < n:
            for z in range(y + 1, min(n, y + 5)):  # "Of the 37 judged both-valves-shut samples"
                if _counted(words[z]) or not cont[z]:
                    lead_noun = _counted(words[z])
                    break
            lead_n = v
    for j in range(n):
        alone = (
            not fact
            and toks[j][0] == "word"
            and words[j] in ("nothing", "none", "all", "each", "both")
            and not (j + 1 < n and (words[j + 1] in ("of", "the") or _counted(words[j + 1])))
            and not (j + 1 < n and toks[j + 1][0] == "num")
        )
        after_lead = bool(lead_noun) and j > 0 and toks[j - 1][1] in (",", ":")
        if alone and words[j] in ("all", "each", "both") and not after_lead:
            alone = False  # "516 gaps in all", "costs 5 each"
        # "3 points are suspect and none is trusted", "21 are trusted and the rest are
        # untrusted": of what the count before it counts (#339)
        before = [q for q in p.qty if 0 <= q.at < j and q.unit in _NOUNS.values()]
        after_and = (
            bool(before)
            and j > 0
            and (
                words[j - 1] in ("and", "but", "while")
                or (toks[j - 1][1] == "," and words[j] in ("none", "nothing"))
            )
        )
        is_rest = (
            not fact
            and bool(before)
            and words[j] in ("rest", "others", "remainder")
            and j >= 2
            and words[j - 1] == "the"
            and words[j - 2] in ("and", "but", "while", ",")
        )
        if alone:
            # "Of the 11 points, all are trusted": of the lead-in's; "Nothing failed": of
            # anything (#336)
            noun = lead_noun if after_lead else "thing"
            if not after_lead and after_and and words[j] in ("none", "nothing"):
                noun = before[-1].unit
        elif is_rest:
            noun = before[-1].unit
        elif toks[j][0] != "word" or not cont[j]:
            continue
        else:
            noun = _counted(words[j])
            if (
                noun in _VERDICT_UNITS
                and not words[j].endswith("s")
                and j
                and (set(cont[j - 1]) & _GENERIC or (words[j - 1] == "all" and not fact))
            ):
                noun = ""  # "all returned ok", "returned a fault", "all ok": a label (#338)
        if not noun:
            continue
        i = j - 1  # its own words: "the 8 flagged hours", "both suspect points"
        while (
            i >= 0
            and i > j - 5
            and clause[i] == clause[j]
            and toks[i][0] == "word"
            and words[i] not in _NP_STOP
            and _word_number(words[i]) is None
            and (cont[i] or toks[i][1].startswith("QQ"))
            and not _counted(words[i])
        ):
            i -= 1
        mods_at = range(i + 1, j)
        how, k, total, start = "", None, None, i
        v = _number_at(toks, words, i) if i >= 0 else None
        if is_rest:
            how, k, total, start = "rest", before[-1].value, None, j - 1
        elif alone:
            how, start, v = ("none" if words[j] in ("nothing", "none") else "all"), j, None
            k = Decimal(0) if how == "none" else (Decimal(2) if words[j] == "both" else lead_n)
            total = lead_n if noun == lead_noun else None
        elif v is not None and not (words[i] == "one" and i and words[i - 1] == "not"):
            x, det = i - 1, False
            while x >= 0 and words[x] in _DEFINITE:
                det, x = True, x - 1
            if x >= 0 and words[x] == "of":
                y = x - 1 - (x >= 1 and words[x - 1] == "out")
                q = _quantifier(toks, words, y)
                if q is not None:
                    how, k, start = q
                    how, k, total = ("part", k, v) if how == "num" else (how, k, v)
                    if how in ("some", "most") and fact:
                        how = ""
                    if how == "all":
                        k = v
                    if how == "part" and k == v:
                        how = "all"
                    if how == "pct" and k in (0, 100) and fact:  # "100% of the 1628 hours"
                        how, k = ("none", Decimal(0)) if k == 0 else ("all", v)
                        if k:
                            p.wholes.add(k)  # all 1628 of them
            elif x >= 0 and words[x] == "all":
                how, k, total, start = "all", v, v, x
            elif det:
                how, k, total, start = "def", v, v, x + 1
            else:
                how, k = "count", v
        else:
            x, det = i, False
            while x >= 0 and words[x] in _DEFINITE:
                det, x = True, x - 1
            if not fact and x >= 2 and words[x] == "s" and toks[x - 1][1] in ("'", "’"):
                det, x = True, x - 3  # "fac-b's equipment": its, all of it (#338)
            q = _quantifier(toks, words, x - 1 if det and x >= 0 and words[x] == "of" else x)
            if det and x >= 0 and words[x] == "of" and q is not None:
                how, k, start = q
                how = "part" if how == "num" else how
            elif q is not None and q[0] in ("some", "most") and not det:
                how, k, start = q
            elif q is not None and q[0] != "num" and (not det or q[0] == "all"):
                how, k, start = q
            elif det and (words[j].endswith("s") or words[j] == "equipment"):
                how, start = "def", x + 1
            elif not fact and q is None and words[j].endswith("s") and _there_be(words, i + 1):
                how, start = "some", i + 1  # "There are flagged hours in the window" (#338)
        if not how or (fact and (how == "def" or (k is None and how != "none"))):
            continue
        if fact and how == "count":  # "2 point(s) are below 90%" of "24 points ...": a part
            head = seen.get(noun)
            how, total = "part", head if head is not None and k is not None and head >= k else None
        if k is not None:
            seen.setdefault(noun, k)
        # what it says of them: from its first verb (else its first word, or a comparison) to
        # its list item's end (past a parenthesis) or the figure it ends with ("are below 90%");
        # a quantifier after the noun ("are all trusted", "all fall", ", all of them,") says
        # all of them
        rest: list = []
        fig, x = "", j
        if x + 1 < n and toks[x + 1][1] == "(":  # "hours (OAT < 60°F)": a qualifier (#338)
            close = next((y for y in range(x + 2, n) if toks[y][1] == ")"), -1)
            x = close if close > 0 else x
        while x + 1 < n:
            x += 1
            if (  # ", all of them,": what it says of them goes on after it
                toks[x][1] == ","
                and x + 4 < n
                and words[x + 1] in ("all", "both", "each")
                and words[x + 2] == "of"
                and words[x + 3] in ("them", "these", "those")
                and toks[x + 4][1] == ","
            ):
                x += 4
                continue
            if item[x] != item[j] or (rest and words[x] in _SUBORDINATE):
                break  # "neither unit fails where its peer passes": not what it says of them
            if toks[x][0] == "num" or _number_at(toks, words, x) is not None:
                fig = next((f"#{q.show()}" for q in p.qty if q.at == x), "")
                break
            rest.append(x)
        lead_at = next((x for x in rest if cont[x] and _verbish(words[x])), -1)
        if lead_at < 0:
            lead_at = next((x for x in rest if cont[x] or words[x] in _COMPARE), -1)
        back_lead = False
        if lead_at < 0:
            # "The rule flagged 8 hours (32 of 37 judged samples, 86.5%)", "mechanical cooling
            # ran 42% of the 1672 free-cooling hours": what the clause before it says (#338)
            back = []
            x = start - 1
            while x >= 0 and toks[x][1] not in (";", ":", ".", "!", "?", "|"):
                if toks[x][0] == "word" and cont[x] and not _counted(words[x]):
                    back.append(x)
                x -= 1
            lead_at = next((x for x in back if _verbish(words[x])), -1)
            back_lead = lead_at >= 0
        late = [
            x
            for x in [*rest, *range(rest[-1] + 1 if rest else j + 1, min(n, j + 12))]
            if groups[x] == groups[j]
            and words[x] in (("all", "both") if how == "count" else ("all", "both", "each"))
            and (
                (lead_at < 0 or x < lead_at)
                or (x + 2 < n and words[x + 1] == "of" and words[x + 2] in ("them", "these"))
            )
            and not (x + 1 < n and (_counted(words[x + 1]) or words[x + 1] in _DEFINITE))
            and words[x - 1] not in ("in", "at", "after", "above")  # "516 gaps in all"
        ]
        if late and not fact:
            p.late_at.add(late[0])
        if late and how in ("def", "count"):
            how = "all"
            k = Decimal(2) if words[late[0]] == "both" and k is None else k
        if lead_at >= 0 and lead_at < start:
            pred_at = sorted(back)  # what the clause before it says
        else:
            pred_at = [x for x in rest if lead_at >= 0 and x >= lead_at]
        both = words[start] == "both" or (late and words[late[0]] == "both")
        if how == "all" and k is not None and not fact and not both:  # a whole count (#332)
            p.wholes.add(k)
            said = {s for x in (*mods_at, *pred_at) for s in cont[x]} - _GENERIC
            said -= {"all", "every", "each", "both"} | (_NOUN_STEMS - _VERDICT_NOUNS)
            p.whole_words.setdefault(k, frozenset(said))
        own = [*mods_at, j] if noun in _VERDICT_UNITS or _FAMILY.get(words[j]) else [*mods_at]
        x, subject = start - 1, True  # no verb before it in its clause
        if _there_be(words, start):
            x = -1  # "There were 8 flagged hours in the window": what the clause says of them
        while x >= 0 and clause[x] == clause[j] and subject:
            subject = not (toks[x][0] == "word" and (words[x] in _STATING or _verbish(words[x])))
            x -= 1
        p.counts.append(
            _Count(
                how,
                k,
                total,
                noun,
                frozenset(s for x in mods_at for s in cont[x]),
                frozenset(
                    [s for x in pred_at for s in cont[x] or ([words[x]] * (words[x] in _COMPARE))]
                    + ([fig] if fig and pred_at else [])
                )
                - {"all", "both", "each"},
                (cont[lead_at] or (words[lead_at],))[-1] if lead_at >= 0 else "",
                frozenset(f for x in own if (f := _family(words, x)) is not None),
                frozenset(f for x in pred_at if (f := _family(words, x)) is not None),
                _raw(toks, max(start, 0), lead_at if lead_at >= start else j, p.marks),
                subject,
                frozenset(
                    lab
                    for x in range(lead_at + 1, n)
                    if lead_at >= start and groups[x] == groups[j]
                    for fam, lab in [_family(words, x) or ("", "")]
                    if fam == "verdict"
                ),
                back_lead,
            )
        )


#: the items a sentence may say "both", "each" or "all" of (#338, #339): word -> its kind, what
#: tells two of them apart in the cited facts (an equipment, a point, a meter, a facility)
_ITEM_NOUNS = {
    **{
        w: "equip"
        for w in (
            "ahu rtu vav fcu mau doas unit equipment handler chiller boiler pump tower box logger"
        ).split()
    },
    # a reference comparison is of one point (#339): "each reference comparison passed"
    **{w: "point" for w in "point sensor valve damper comparison".split()},
    "meter": "meter",
    **{w: "facility" for w in "facility building site".split()},
}
#: a word after an item noun that makes it a modifier, not a set ("the facilities list", #339)
_TOOL_NOUNS = frozenset(
    (
        "list listing lists tool tools table tables view page output summary call data report "
        "reports index catalog registry name names id ids count counts"
    ).split()
)
#: what may stand just before names joined by "and" and not make them a set (#339): "between A
#: and B", "A vs B", "more than A and B"
_NOT_A_SET = frozenset("between than vs versus from like unlike except besides".split())
#: what starts a clause of its own after a set's verb: what is said of the set ends there (#339)
_SET_END = frozenset("but while whereas although though except unless because since".split())
#: auxiliaries a negated set's verb may be (#339): its "not" goes after one, else "does not"
_AUX = frozenset(
    (
        "is are was were has have had does do did can could will would shall should may might must"
    ).split()
)


def _item_noun(word: str) -> str:
    """``word`` as an item of :data:`_ITEM_NOUNS` in the singular ("AHUs" -> "ahu", "boxes" ->
    "box", "facilities" -> "facility", "AHU's" -> "ahu"), or ""."""
    w = re.sub(r"['’]s$", "", word.lower())
    for cand in (
        w,
        w[:-1] if w.endswith("s") else "",
        w[:-2] if w.endswith("es") else "",
        w[:-3] + "y" if w.endswith("ies") else "",
    ):
        if cand and cand in _ITEM_NOUNS:
            return cand
    return ""


def _set_scope(toks, words, end: int, lead: bool) -> int:
    """The end (exclusive) of what a sentence says of a set whose words end at token ``end``
    (#339): the end of its sentence, a semicolon, a table cell, or a clause of its own after its
    verb ("..., but AHU-2 passes"); a lead-in's (a colon, a dash or a table cell after the set)
    runs on past it, to the end of the line."""
    n = len(toks)
    verb = False
    x = end + 1
    while x < n:
        kind, tok, w = toks[x][0], toks[x][1], words[x]
        if kind == "sym" and tok in (";", ".", "!", "?"):
            break
        if kind == "sym" and tok == "|" and not lead:
            break
        if verb and (
            w in _SET_END
            or tok == ":"
            or (tok == "," and x + 1 < n and words[x + 1] in ("and", "but", "so", "while"))
        ):
            break
        if kind == "word" and (_verbish(w) or w in _AUX or _VERDICT.get(w) or _DIRECTION.get(w)):
            verb = True  # "warn", "fail", "differ" too: the first word of what is said of it
        elif kind == "word" and not tok.startswith("QQ") and w not in _FUNCTION:
            verb = True
        x += 1
    return x


def _lead_after(toks, x: int) -> bool:
    """Whether token ``x`` makes what stands before it a lead-in (#339): a colon, a table cell's
    bar or a dash ("AHU-1 and AHU-2: fault", "| AHU-1, AHU-2 | fault |")."""
    if x >= len(toks) or toks[x][0] != "sym":
        return False
    tok = toks[x][1]
    if tok in (":", "|", "—", "–"):
        return True
    return tok == "-" and (x + 1 >= len(toks) or toks[x + 1][2] > toks[x][3])


def _pred_at(toks, words, a: int, b: int) -> int:
    """The first token in ``a`` to ``b`` (exclusive) that is a verb: where what a negated set
    says of each item starts (#339), or -1."""
    for y in range(a, b):
        if toks[y][0] == "word" and (_verbish(words[y]) or words[y] in _AUX):
            return y
        if toks[y][0] == "sym" and toks[y][1] not in ("'", "’", '"'):
            return -1
    return -1


def _conjunct(toks, words, j: int):
    """``(start, end, name)`` of one part of a joined subject at token ``j`` (#339): a name
    (and an item noun after it: '"heat_valve" point'), or "the" and a singular item noun ("the
    heating valve", "the OA damper": ``name`` ""); else None."""
    n = len(toks)
    if toks[j][1].startswith(_NAME):
        end = j + (
            j + 1 < n and toks[j + 1][0] == "word" and _item_noun(words[j + 1]) == words[j + 1]
        )
        return j, end, toks[j][1]
    if words[j] not in ("the", "its", "their"):
        return None
    for y in range(j + 1, min(n, j + 5)):
        if (
            toks[y][0] != "word"
            or words[y] in _NP_STOP
            or _counted(words[y])
            and not _item_noun(words[y])
        ):
            return None
        noun = _item_noun(words[y])
        if noun and noun == words[y]:  # singular: "the heating valve"
            if y + 1 < n and toks[y + 1][0] == "word" and _item_noun(words[y + 1]):
                continue
            return j, y, ""
    return None


def _sets(p: _Parsed, toks, words, cont, clause) -> None:
    """What a sentence says of every one of a set of items (#338, #339), as :class:`_Set`:
    "both AHUs", "each AHU", "each AHU's ...", "each of the 2 AHUs", "every unit", "all three
    AHUs", "the pair of AHUs", "the AHUs both / all / each ...", "The AHUs fail" and "fac-b's 7
    AHUs" (a definite plural opening its clause: all of them); negated: "neither AHU",
    "neither of the AHUs", "no AHU", "none of the AHUs", "not one of the AHUs"; names (or "the
    <item>") joined by "and", a comma or "neither ... nor", in any position (a colon, a dash or
    a table cell after them makes them a lead-in); "A has no leak, and B doesn't either"; and
    "both" or "each" with no noun ("Each fails it", "On both, ...")."""
    n = len(toks)

    def opens(a: int) -> bool:  # no content word before token ``a`` in its clause
        return a <= 0 or not any(cont[z] for z in range(0, a) if clause[z] == clause[a])

    def leads(a: int) -> bool:  # nothing but a conjunction before token ``a`` in its clause
        return a <= 0 or not any(
            toks[z][0] in ("word", "num") and words[z] not in ("and", "but", "so", "then", "while")
            for z in range(0, a)
            if clause[z] == clause[a]
        )

    named_at: set = set()  # the item nouns a named set reads ("the three meters A, B and C")
    consumed: set = set()  # the quantifiers a set with a noun took
    for j in range(n):
        noun = _item_noun(words[j]) if toks[j][0] == "word" else ""
        if not noun:
            continue
        if (
            j + 1 < n
            and toks[j + 1][0] == "word"
            and (_item_noun(words[j + 1]) or words[j + 1] in _TOOL_NOUNS)
        ):
            continue  # "the VAV boxes": the next word is the noun; "the facilities list"
        y = j + 1  # 'every tower "cw_flow" point': the point is the noun, the tower its own
        while y < min(n, j + 4) and toks[y][0] == "word" and not _verbish(words[y]):
            if _item_noun(words[y]) and clause[y] == clause[j]:
                break
            if not toks[y][1].startswith("QQ") and words[y] in _NP_STOP | _FUNCTION:
                y = n
                break
            y += 1
        if y < min(n, j + 4) and toks[y][0] == "word" and _item_noun(words[y]):
            continue
        base = re.sub(r"['’]s$", "", words[j])
        plural = base != noun and noun != "equipment"
        poss = base != words[j] or (
            plural and j + 1 < n and toks[j + 1][1] in ("'", "’") and toks[j + 1][2] == toks[j][3]
        )  # "each AHU's sensor", "both AHUs' sensors"
        i = j - 1  # its own words: "both supply air sensors", 'each "supply_air_temp" point'
        while (
            i >= 0
            and j - i <= 4
            and clause[i] == clause[j]
            and toks[i][0] == "word"
            and words[i] not in _NP_STOP
            and words[i] not in ("neither", "nor", "pair")
            and _number_at(toks, words, i) is None
        ):
            i -= 1
        k, how, neg = None, "", False
        v = _number_at(toks, words, i) if i >= 0 else None
        if v is not None:
            i -= 1
        x = i
        while x >= 0 and words[x] in ("the", "its", "their", "these", "those", "of", "one"):
            x -= 1
        q = words[x] if x >= 0 else ""
        has_of = any(words[y] == "of" for y in range(x + 1, i + 1))
        if "one" in words[x + 1 : i + 1] and q not in ("each", "every", "not"):
            continue  # "one of the two AHUs": a part, not each of them
        owner = x >= 2 and q == "s" and toks[x - 1][1] in ("'", "’")  # "fac-b's 7 AHUs" (#339)
        start = x
        if q == "both":
            how, k = "both", Decimal(2)
        elif q == "pair" and has_of:  # "the pair of AHUs" (#339)
            how, k = "both", Decimal(2)
            start = x - (x >= 1 and words[x - 1] in ("the", "a"))
        elif q in ("each", "every", "all") and (q != "every" or not plural or has_of):
            how, k = q, v
        elif q == "neither" or (q == "none" and has_of) or (q == "no" and not has_of):
            how, neg = q, True  # "neither AHU", "none of the AHUs", "no AHU" (#339)
            k = Decimal(2) if q == "neither" and v is None else v
        elif q == "not" and x + 1 < n and words[x + 1] == "one" and has_of:
            how, k, neg = "none", v, True  # "not one of the AHUs"
        elif q == "single" and x >= 2 and words[x - 1] == "a" and words[x - 2] == "not":
            how, neg, start = "none", True, x - 2  # "not a single AHU"
        elif q in _OF_TOTAL or q in ("not", "any"):
            continue  # "one of the AHUs", "any AHU": a part, not each of them
        elif (owner or (i >= 0 and words[i] in _DEFINITE)) and (plural or v is not None):
            # "the AHUs ... both", "The 2 AHUs fail", "fac-b's 7 AHUs": opening its clause
            first = x - 2 if owner else i
            if not opens(first):
                continue  # "The 14 points on the 2 chillers": not what the clause is about
            late = next(
                (
                    words[z]
                    for z in range(j + 1, min(n, j + 4))
                    if clause[z] == clause[j] and words[z] in ("both", "all", "each")
                ),
                "",
            )
            how = late or "def"
            k = Decimal(2) if late == "both" else v
            names = j + 1 < n and toks[j + 1][1].startswith(_NAME)
            if names:
                named_at.add(j)  # "The three meters A, B and C meet ...": the names say which
            elif how == "def" and not any(
                toks[z][0] == "word" and cont[z] for z in range(j + 1, n) if clause[z] == clause[j]
            ):
                continue  # "the AHUs." alone: no statement of them
            start = first
        else:
            continue
        if v is not None and k is None:
            k = v
        start = max(start, 0)
        if v is not None:  # "the 2 AHUs": the set's number, read with the set
            at = i + 1
            p.qty[:] = [replace(q, of_set=True) if q.at == at else q for q in p.qty]
        for z in range(start, min(n, j + 4)):  # its "all" is the set's, not a label's
            if words[z] in ("all", "both", "each", "every") and clause[z] == clause[j]:
                p.late_at.add(z)
                consumed.add(z)
        lead = _lead_after(toks, j + 1)  # "| Both AHUs | fault |", "Both AHUs: fault"
        scope = _set_scope(toks, words, j, lead)
        pred = -1
        if neg and not leads(start):
            if how != "neither":
                continue  # "has no valve leak", "carries no sensor condition": a negation
        elif neg:
            pred = _pred_at(toks, words, j + 1, scope)
        p.sets.append(
            _Set(
                _raw(toks, start, j, p.marks),
                k,
                noun,
                _ITEM_NOUNS[noun],
                how,
                start,
                j,
                scope,
                neg,
                lead=lead,
                pred=pred,
                poss=poss,
                opens=opens(start),
            )
        )
    _joined(p, toks, words, cont, clause, opens, named_at)
    # "both" or "each" with no noun of its own: of the items the cited facts are about (#339)
    for j in range(n):
        if words[j] not in ("both", "each") or j in consumed:
            continue
        nxt = words[j + 1] if j + 1 < n else ""
        prev = words[j - 1] if j else ""
        if (
            nxt == "of"
            or _counted(nxt)
            or _item_noun(nxt)
            or (j + 1 < n and toks[j + 1][0] == "num")
        ):
            continue
        if prev in _PRONOUNS or prev in ("are", "were", "in", "at", "after"):
            continue  # "they both", "they are all": read with the counts; "costs 5 each"
        if any(st.end in (j - 1, j - 2) for st in p.sets) or (
            j and toks[j - 1][1].startswith("QQ")
        ):
            continue  # "A and B both ...", "leaking_valve and supply_air_control both pass"
        y = j + 1
        while y < n and words[y] in ("also", "still", "then", "now", "too", "again"):
            y += 1
        verb = (
            y < n
            and toks[y][0] == "word"
            and not toks[y][1].startswith("QQ")
            and (
                _verbish(words[y])
                or words[y] in _AUX
                or _negates(words[y])
                or _VERDICT.get(words[y]) is not None
                or _DIRECTION.get(words[y]) is not None
                or words[y] in ("read", "reads", "move", "moves", "show", "shows")
            )
        )  # "Each fails it", "Both never move": not "Both coils were open"
        alone = opens(j) and (verb or (j + 1 < n and toks[j + 1][1] == ","))
        on = prev in ("on", "for", "of") and opens(j - 1) and j + 1 < n and toks[j + 1][1] == ","
        if not (alone or on):
            continue
        start = j - 1 if on else j
        p.sets.append(
            _Set(
                toks[j][1] if not on else f"{toks[j - 1][1]} {toks[j][1]}",
                Decimal(2) if words[j] == "both" else None,
                "",
                "",
                words[j],
                start,
                j,
                _set_scope(toks, words, j, False),
                pron=True,
            )
        )


def _joined(p: _Parsed, toks, words, cont, clause, opens, named_at) -> None:
    """Names (or "the <item>") joined as one set (#338, #339): "AHU-1 and AHU-2 (both) fail",
    "Both A and B", "neither A nor B", "the heating valve and the OA damper both", a lead-in
    ("A, B: fault", "| A, B | fail |"), names joined in any other position ("found on A and B"),
    and "A has no leak, and B doesn't either" / "so does B" / "nor does B"."""
    n = len(toks)
    j = 0
    while j < n:
        first = _conjunct(toks, words, j)
        if first is None:
            j += 1
            continue
        parts, x, nor, conj = [first], first[1] + 1, False, False
        while x < n:
            y = x + (toks[x][1] == ",")
            if y < n and words[y] in ("and", "nor", "or"):
                nor = nor or words[y] in ("nor", "or")  # "neither A or B" too
                conj = True
                y += 1
            if y == x or y >= n:
                break
            nxt = _conjunct(toks, words, y)
            if nxt is None:
                break
            parts.append(nxt)
            x = nxt[1] + 1
        j = max(x, j + 1)
        names = tuple(p.marks[tok].lower() for _a, _b, tok in parts if tok and tok in p.marks)
        slots = sum(1 for *_, tok in parts if not tok)
        if len(parts) < 2 or len(names) != len(set(names)):
            continue
        a, b = parts[0][0], parts[-1][1]
        before = words[a - 1] if a else ""
        if before in _NOT_A_SET:
            continue
        lead_word = int(before in ("both", "neither"))
        if before == "of" and a >= 2 and words[a - 2] == "neither":
            before, lead_word = "neither", 2  # "Neither of AHU-1 and AHU-2 fails" (#339)
            nor = True
        start = a - lead_word
        at_open = opens(start) or any(z in named_at for z in range(max(0, a - 6), a))
        lead = _lead_after(toks, b + 1)
        if not conj and not lead:
            continue  # 'For "AHU-2" "supply_air_temp", the comparison ...': a comma, no list
        if slots and not (at_open and not lead):
            continue  # "the heating valve and the cooling valve" mid-sentence: not read
        if nor and before != "neither" and "and" not in words[a : b + 1]:
            continue  # "AHU-1 or AHU-2": one of them, not each
        neg = nor and before == "neither"
        if neg and slots:
            continue
        verb = b + 1 < n and toks[b + 1][0] == "word"
        if at_open and not (verb or lead) and not neg:
            continue  # "AHU-1 and AHU-2." alone
        scope = _set_scope(toks, words, b, lead)
        pred = _pred_at(toks, words, b + 1, scope) if neg and at_open else -1
        k = next(
            (st.k for st in p.sets if st.end in named_at and st.end < a and st.k is not None),
            None,
        )
        p.sets.append(
            _Set(
                _raw(toks, start, b, p.marks),
                k,
                "",
                "point"
                if any(
                    _item_noun(words[c]) in ("point", "sensor", "valve", "damper")
                    for _x, c, _t in parts
                )
                else "",
                "neither" if neg else "named",
                start,
                b,
                scope,
                neg,
                names,
                slots,
                lead,
                pred,
            )
        )
    # "A has no valve leak, and B doesn't either", "..., and so does B", "nor does B" (#339)
    subj = next((z for z in range(n) if toks[z][1].startswith(_NAME) and opens(z)), -1)
    if subj < 0 or toks[subj][1] not in p.marks:
        return
    for z in range(subj + 1, n):
        if not toks[z][1].startswith(_NAME) or toks[z][1] not in p.marks:
            continue
        b1, b2 = (words[z - 1] if z else ""), (words[z - 2] if z > 1 else "")
        after = words[z + 1 : z + 5]
        echo = (
            (b1 in _AUX and b2 in ("so", "neither", "nor"))
            or (
                after
                and (after[0] in _AUX or _negates(after[0]))
                and any(w in ("either", "too") for w in after)
            )
            or after[:2] == ["as", "well"]
        )
        if not echo:
            continue
        names = (p.marks[toks[subj][1]].lower(), p.marks[toks[z][1]].lower())
        if names[0] == names[1]:
            continue
        p.sets.append(
            _Set(
                f"{_raw(toks, subj, subj, p.marks)} ... {_raw(toks, z, z, p.marks)}",
                Decimal(2),
                "",
                "",
                "named",
                subj,
                subj,
                n,
                names=names,
            )
        )
        return


_PRONOUNS = frozenset("they them these those".split())


def _alls(p: _Parsed, toks, words, cont, clause) -> None:
    """ "all" said with no counted thing a sentence names (#338): "They are all trusted", "all
    of them are suspect", a table cell "all trusted". With a label of a closed family, it is
    said of the label ("all trusted", read against the cited facts' counts); without one, it
    is the quantifier "all" (a cited fact must say all)."""
    n = len(toks)
    for k in range(n):
        w = words[k]
        if w not in ("all", "both", "each") or k in p.late_at:
            continue
        prev = words[k - 1] if k else ""
        prev2 = words[k - 2] if k > 1 else ""
        nxt = words[k + 1] if k + 1 < n else ""
        pronoun = prev in _PRONOUNS or (prev in ("are", "were") and prev2 in _PRONOUNS)
        pronoun = pronoun or (  # "All are trusted", "Each is suspect": a pronoun itself
            not any(cont[z] for z in range(k) if clause[z] == clause[k])
            and nxt in ("is", "are", "was", "were")
        )
        of_them = nxt == "of" and k + 2 < n and words[k + 2] in _PRONOUNS
        label = _FAMILY.get(nxt) if w == "all" else None
        if label and k + 2 < n and (_counted(words[k + 2]) or toks[k + 2][0] == "num"):
            continue  # "all trusted points": a counted thing
        if not (pronoun or of_them or label):
            continue
        if label is None:  # the first label of its clause: "they are all trusted"
            label = next(
                (
                    hit
                    for x in range(k + 1, n)
                    if clause[x] == clause[k] and (hit := _FAMILY.get(words[x])) is not None
                ),
                None,
            )
        if label is not None and label[0] in ("trust", "verdict"):
            p.all_labels.add(label)
        elif w != "each" or pronoun:
            p.quant.add("all")


def _there_be(words, i: int) -> bool:
    """Whether the words before token ``i`` are "there is / are / was / were" (#338)."""
    return i >= 2 and words[i - 1] in ("is", "are", "was", "were") and words[i - 2] == "there"


def _still_holds(toks, words, k) -> bool:
    """Whether the word at ``k`` says a finding persists (#336): "remains valid", "is still
    valid", "still holds", "holds after the re-judge" (not "air holds near the allowance")."""
    w = words[k]
    prev = words[k - 1] if k else ""
    if w == "valid":
        return prev in ("remains", "remain", "remained", "still", "stays", "stay")
    if prev == "still" and w in ("applies", "apply", "stands", "stand"):
        return True
    if w not in ("holds", "hold", "held"):
        return False
    nxt = words[k + 1] if k + 1 < len(toks) else ""
    end = k + 1 >= len(toks) or toks[k + 1][0] == "sym"
    return prev == "still" or end or nxt in ("after", "once", "when", "even", "true", "up")


def _whole_of(toks, words, k) -> bool:
    """Whether "all" or "every" at ``k`` counts a figure that follows ("all of the 17", "every
    one of the 8"): that whole count is checked as one."""
    j = k + 1
    while j < len(toks) and words[j] in ("of", "the", "its", "their", "these", "those", "one"):
        j += 1
    return j > k + 1 and _number_at(toks, words, j) is not None


def _superlative(toks, words, cont, k):
    """``(word, what it is said of)`` for a rank word at ``k`` ("the worst finding", "worst: HW
    (heating) coil", "the most severe issue"), or None (#332)."""
    w = words[k]
    if w == "most" and k + 1 < len(toks) and words[k + 1] in _SEVERE:
        w, j = "most " + words[k + 1], k + 2
    elif w in ("least", "most") and k + 1 < len(toks) and words[k + 1] == "trusted":
        w, j = f"{w} trusted", k + 2  # "the least trusted point" (#336)
    elif w in _SUPERLATIVE:
        if k and words[k - 1] == "at":
            return None  # "at best", "at worst"
        if k + 1 < len(toks) and words[k + 1] == "first":
            return None  # "(worst first)": an order
        j = k + 1
    else:
        return None
    obj: set = set()
    seen = 0
    while j < len(toks) and seen < 3:  # past a colon or a parenthesis: "worst: HW (heating)"
        if toks[j][0] == "num" or (toks[j][0] == "sym" and toks[j][1] in ",;.!?|"):
            break
        if toks[j][0] == "word" and cont[j]:
            obj.update(cont[j])
            seen += 1
        j += 1
    said = " ".join(
        t[1] for x, t in enumerate(toks[k:j], k) if x == k or (t[0] == "word" and cont[x])
    )
    return w, frozenset(obj), said


def _neg_span(k: int, neg: _Neg, clause: list) -> range:
    """The tokens a negation (or zero) at ``k`` covers: to the end of its clause."""
    end = k
    while end < len(clause) and clause[end] == clause[k]:
        end += 1
    start = k
    if neg.kind == "share":  # "HW-leak 0%": the words before the zero are what it is about
        while start > 0 and clause[start - 1] == clause[k] and k - start < 3:
            start -= 1
    return range(start, end)


def _negation(toks, k, cont, clause, words, last_noun, fact, clause_words, marks):
    """The :class:`_Neg` a negation word at ``k`` makes, or None when it negates nothing."""
    n = len(toks)
    w = words[k]
    if w == "none" or w == "nothing" or w == "nobody":
        return _none_of(toks, k, cont, clause, words, last_noun, fact, clause_words, marks)
    np = w in ("no", "without", "neither", "nor")
    j = k + 1
    while j < n and clause[j] == clause[k] and toks[j][0] == "word" and words[j] in _NEG_SKIP:
        if words[j] in _ARTICLES:
            np = True
        j += 1
    scope = []
    while j < n and clause[j] == clause[k] and len(scope) < 4:
        if toks[j][0] != "word":
            if toks[j][0] == "num" or toks[j][1] in "%°":
                j += 1
                continue
            break
        if not cont[j]:
            if toks[j][1].startswith("QQ") or (np and scope and words[j] in ("or", "and")):
                j += 1  # "not a measured or computed result": the result
                continue
            if scope:
                break
            j += 1
            continue
        scope.append(j)
        if not np:
            break
        j += 1
    if not scope:
        return None
    if w == "no" and _counted(words[scope[-1]]):
        # "no faults were found", "no untrusted points": a count of none (#332), read as a zero
        noun_at, mods, pred, lead = _zero_parts(toks, cont, words, scope, j, k, clause)
        noun = _counted(words[noun_at])
        cw = frozenset(clause_words(k))
        return _Neg(
            frozenset(s for x in scope for s in cont[x]) | {_stem(noun)},
            _stem(noun),
            "zero",
            _raw(toks, k, noun_at, marks),
            clause=cw,
            after=cw,
            mods=mods,
            pred=pred,
            lead=lead,
            ctx=cw,
            there=_there_be(words, k),
        )
    head_tok = scope[-1] if np else scope[0]
    head = cont[head_tok][-1]
    verdict = (
        _VERDICT.get(words[head_tok]) is not None or _DIRECTION.get(words[head_tok]) is not None
    )
    stems = frozenset(s for x in scope for s in cont[x])
    raw = _raw(toks, k, scope[-1], marks)
    subject, named = "", False
    if fact and not np:  # "drift not evaluated", "pairs were not judged": what it is about
        x = k - 1
        while x >= 0 and clause[x] == clause[k] and toks[x][0] == "word" and words[x] in _NEG_SKIP:
            x -= 1
        if x >= 0 and clause[x] == clause[k] and toks[x][0] == "word":
            if cont[x]:
                subject = cont[x][-1]
            elif toks[x][1].startswith(_NAME):
                subject, named = marks.get(toks[x][1], ""), True
    words_ = (
        stems
        if np or not fact
        else frozenset({head} | ({subject} if subject and not named else set()))
    )
    after = frozenset(s for x in range(k, n) if clause[x] == clause[k] for s in cont[x])
    return _Neg(
        words_,
        head,
        "np" if np else "verbal",
        raw,
        verdict,
        subject,
        named,
        after,
        frozenset(clause_words(k)),
    )


def _none_of(toks, k, cont, clause, words, last_noun, fact, clause_words, marks):
    """The zero a "none (of the 8 flagged hours ...)" at ``k`` states: what it counts (a counted
    thing named there, else the one counted before it) and the words of what it is about."""
    n = len(toks)
    j = k + 1
    if j < n and words[j] == "of":
        j += 1
    scope, noun = [], ""
    while j < n and clause[j] == clause[k] and len(scope) < 4:
        if toks[j][0] == "word" and cont[j]:
            scope.append(j)
            noun = noun or _counted(words[j])
        elif fact and scope and toks[j][0] == "word" and words[j] in ("of", "in", "on"):
            break  # "none of the 8 flagged hours of <the finding>": the hours
        j += 1
    noun = noun or last_noun
    stems = {x for t in scope for x in cont[t]}
    if noun:
        stems.add(_stem(noun))
    if not stems:
        return None
    head = _stem(noun) if noun else cont[scope[-1]][-1]
    noun_at, mods, pred, lead = _zero_parts(toks, cont, words, scope, j, k, clause)
    end = noun_at if noun_at >= 0 else (scope[-1] if scope else k)
    ctx = frozenset(s for x in range(n) if clause[x] == clause[k] for s in cont[x])
    ctx |= frozenset(clause_words(k))  # a sentence's lead-in ("Of the 8 flagged hours, none")
    cw = ctx | stems  # ``clause`` is the list item: it reaches past a parenthesis
    return _Neg(
        frozenset(stems),
        head,
        "zero",
        _raw(toks, k, end, marks),
        clause=cw,
        after=cw,
        mods=mods,
        pred=pred,
        lead=lead,
        ctx=ctx | stems,
    )


#: a time unit as the word a text writes for it
_UNIT_WORD = {"h": "hour", "min": "minut", "s": "second"}


def _zero(toks, k, q, cont, item, words, last_noun, fact, clause_words, marks):
    """The zero a figure 0 at ``k`` states: ``0 untrusted`` (of the points counted before it),
    ``0 below the trust floor``, ``0 hours``; or, a share, ``CHW-leak 0% of the samples``: none
    of the samples leaked on the chilled-water side. ``item``: the token's list item (a fact's
    reaches past a parenthesis)."""
    n = len(toks)
    if q.unit == "%":
        # "CHW-leak 0%", "the CHW-leak share is 0%": the words before the zero, not "share"
        before = [
            t
            for t in range(max(0, k - 4), k)
            if item[t] == item[k] and cont[t] and not set(cont[t]) <= _SHARE_WORDS
        ]
        if not before:
            return None
        base = _stem(q.base) if q.base else ""
        stems = set(cont[before[-1]]) | ({base} if base else set())
        kind, head, subject = "share", cont[before[-1]][-1], base
        cw = frozenset(clause_words(k) | stems)
        return _Neg(
            frozenset(stems),
            head,
            kind,
            _raw(toks, before[-1], k + 1, marks),
            subject=subject,
            clause=cw,
            after=cw,
        )
    j = k + 1
    # "0 of the 11 points": what it counts follows the total
    x = j
    if x < n and words[x] == "of":
        x += 1
        while x < n and words[x] in ("the", "its", "their", "these", "those"):
            x += 1
        if x < n and toks[x][0] == "num":
            j = x + 1
    scope = []
    while j < n and item[j] == item[k] and len(scope) < 4:
        if toks[j][0] == "num":
            break
        if toks[j][0] == "word" and cont[j]:
            scope.append(j)
        j += 1
    stems = {x for t in scope for x in cont[t]}
    noun = q.unit if q.unit in _NOUNS.values() else ""
    if not noun and not q.unit and not any(_noun(words[t]) for t in scope):
        noun = last_noun
    if not noun and not q.unit:
        noun = next((_counted(words[t]) for t in scope if _counted(words[t])), "")
    unit_word = _stem(_UNIT_WORD.get(q.unit, q.unit)) if q.unit and not noun else ""
    thing = _stem(noun) if noun else unit_word
    if thing:
        stems.add(thing)
    if not stems:
        return None
    noun_at, mods, pred, lead = _zero_parts(toks, cont, words, scope, j, k, item)
    end = noun_at if noun_at >= 0 else (scope[0] if scope else k)
    head = thing or cont[scope[0]][-1]
    # its list item, and (a fact's) what leads in to the list: "Drift on X: ..., 0 fault"
    ctx = frozenset(s for x in range(n) if item[x] == item[k] or x < k for s in cont[x])
    cw = frozenset(clause_words(k) | stems)
    return _Neg(
        frozenset(stems),
        head,
        "zero",
        _raw(toks, k, end, marks),
        clause=cw,
        after=cw,
        mods=mods,
        pred=pred,
        lead=lead,
        ctx=ctx | stems,
        there=not fact and _there_be(words, k),
    )


def _rank(toks: list, words: list, k: int) -> tuple:
    """``(ranks, stated)``: the issue ranks named at token ``k``: "Issue 3", "Issues 3 and 4",
    "the 1st issue", "the second issue" (#328); ``stated`` for a rank said as one ("the second
    issue", "issue #1", "ranked first", "rank 2"), not an issue's label ("Issue 3", #332)."""
    n = len(toks)
    w = words[k]
    out = set()
    if w in ("ranked", "ranks", "rank") and k + 1 < n and words[k + 1] in _ORDINAL_WORDS:
        return {_ORDINAL_WORDS[words[k + 1]]}, True
    if w in _RANKED and w not in ("ranked",):
        j = k + 1
        stated = w == "rank"
        if j < n and toks[j][1] == "#":
            j += 1
            stated = True
        while j < n and toks[j][0] == "num" and toks[j][1].isdigit():
            out.add(int(toks[j][1]))
            j += 1
            if j < n and (toks[j][1] == "," or words[j] in ("and", "or", "to")):
                j += 1
        return out, stated
    if w in _ORDINAL_WORDS and k + 1 < n and words[k + 1] in _RANKED:
        return {_ORDINAL_WORDS[w]}, True
    if (
        toks[k][0] == "word"
        and w in _ORDINAL
        and k
        and toks[k - 1][0] == "num"
        and toks[k - 1][3] == toks[k][2]
        and k + 1 < n
        and words[k + 1] in _RANKED
        and toks[k - 1][1].isdigit()
    ):
        return {int(toks[k - 1][1])}, True
    return out, False


def _counted(word: str) -> str:
    """``word`` as a counted thing: a noun of :data:`_NOUNS`, or a time unit's word ("hours")."""
    n = _noun(word)
    if n:
        return n
    unit = _UNITS.get(word.lower())
    return (
        _UNIT_WORD.get(unit, unit) if unit in ("h", "day", "week", "month", "year", "min") else ""
    )


def _noun(word: str) -> str:
    """``word`` as a counted thing in the singular, or ""."""
    w = word.lower()
    for cand in (w, w[:-1] if w.endswith("s") else "", w[:-2] if w.endswith("es") else ""):
        if cand and cand in _NOUNS:
            return _NOUNS[cand]
    return ""


def _quantity(toks: list, i: int, value: Decimal, raw: str):
    """The figure at token ``i`` with its sign, unit, base and role; None for a label or a
    number that is part of a name."""
    _kind, _tok, a, b = toks[i]
    prev = toks[i - 1] if i else None
    if raw and prev is not None and prev[3] == a and prev[0] == "word":
        return None  # glued to the letters before it: part of a name
    sign, j = "", i - 1
    if prev is not None and prev[0] == "sym" and prev[3] == a and prev[1] in "+-±–":
        before = toks[i - 2] if i >= 2 else None
        glued = before is not None and before[3] == prev[2] and before[0] in ("num", "word")
        if not glued and not (prev[1] == "–" and before is not None):  # "1-20": a range
            sign = {"+": "+", "±": "±"}.get(prev[1], "-")
            j = i - 2
    elif prev is not None and prev[0] == "sym" and prev[1] == "±":
        sign, j = "±", i - 2
    elif prev is not None and prev[0] == "word" and prev[1].lower() in ("minus", "negative"):
        sign, j = "-", i - 2
    elif prev is not None and prev[0] == "word" and prev[1].lower() == "plus":
        sign, j = "+", i - 2
    unit = ""
    if j >= 0 and toks[j][0] == "sym" and toks[j][1] in _CURRENCY and toks[j][3] == a:
        unit = _CURRENCY[toks[j][1]]  # "$12,000", "-$5"
        j -= 1
        if not sign and j >= 0 and toks[j][0] == "sym" and toks[j][1] in "+-–":
            sign = "+" if toks[j][1] == "+" else "-"
            j -= 1
    k = i + 1
    if not unit and k < len(toks):
        t = toks[k]
        w = t[1].lower()
        glued = t[2] == b
        if t[0] == "sym" and t[1] == "%":
            unit, k = "%", k + 1
        elif t[0] == "sym" and t[1] == "°":
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            if nxt is not None and nxt[0] == "word" and nxt[1].lower() in ("f", "c", "k"):
                unit, k = _UNITS[nxt[1].lower()], k + 2
            else:
                unit, k = "deg", k + 1
        elif t[0] == "word":
            if glued and w in _ORDINAL:
                return None  # "1st", "2nd": an ordinal
            nxt = toks[k + 1][1].lower() if k + 1 < len(toks) else ""
            if w in _DEGREE and nxt in ("f", "c", "k", "fahrenheit", "celsius"):
                unit, k = _UNITS[nxt], k + 2
            elif w in _UNITS and (len(w) > 1 or glued or t[1] in _SHORT_SPACED):
                unit, k = _UNITS[w], k + 1
            elif glued and len(t[1]) == 1 and t[1].isupper():
                return None  # "4P", "3P": a model's name, not a count (#332)
            elif glued:
                unit, k = w, k + 1  # an unknown glued suffix ("4.5e1") is a unit of its own
    if (
        unit
        and k + 1 < len(toks)
        and toks[k][1] == "/"
        and toks[k + 1][0] == "word"
        and toks[k + 1][1].lower() in _UNITS
    ):
        unit, k = f"{unit}/{toks[k + 1][1].lower()}", k + 2  # "kWh/day"
    if prev is not None and prev[0] == "num" and prev[3] == a:
        return None  # ".14" of "5.16.14": a section or version number
    if not unit and not sign and prev is not None:
        if prev[0] == "sym" and prev[1] in "#§":
            return None
        if prev[0] == "word" and prev[1].lower() in _LABELS:
            return None
        if _label_list(toks, i):
            return None
    if not unit:
        unit = _scan_noun(toks, k)
        if (
            unit in _NOUNS.values()
            and raw.isdigit()
            and len(raw) == 4
            and 1900 <= int(raw) <= 2100
            and not sign
            and prev is not None
            and prev[1].lower() in _YEAR_BEFORE
            and k < len(toks)
            and toks[k][1].lower() in _NOUNS
        ):
            unit = ""  # "the 2016 baseline": a year naming one thing, not 2016 of them (#336)
    base = ""
    if unit == "%" and k < len(toks) and toks[k][1].lower() == "of":
        base = _scan_noun(toks, k + 1, of=True)
    threshold = _cue_before(toks, j) or _cue_after(toks, k) or _cue_subject(toks, j)
    role = "threshold" if threshold else "result"
    if j >= 1 and toks[j][1] == "=" and toks[j - 1][1].startswith(_NAME):
        role = "setting"  # "fault_pct = 20.0": a parameter's value, a threshold or a result
    year = raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100 and not unit and not sign
    of_noun = ""
    if unit in _VERDICT_UNITS:
        x = i + 1
        while x < len(toks) and toks[x][1].lower() in _UNIT_NOUN_WORDS.get(unit, ()):
            x += 1
        nxt = _noun(toks[x][1]) if x < len(toks) and toks[x][0] == "word" else ""
        of_noun = nxt if nxt in ("result", "finding", "check", "issue") else ""
    return _Qty(value.normalize(), sign, unit, base, role, year, i, of_noun=of_noun)


#: the words of a verdict count before what it counts ("1 ok result")
_UNIT_NOUN_WORDS = {u: {u, u + "s"} for u in ("fault", "warn", "info", "ok")}


#: what stands before a year that names one thing ("the 2016 baseline", "its 2017 period")
_YEAR_BEFORE = frozenset("the a an its their this that each every".split())


def _label_list(toks: list, i: int) -> bool:
    """Whether the number at ``i`` continues a list of labels ("issues 1, 3 and 4")."""
    j = i - 1
    seen = False
    while j >= 0 and (toks[j][0] == "num" or toks[j][1].lower() in (",", "and", "or", "&")):
        seen = seen or toks[j][0] == "num"
        j -= 1
    return seen and j >= 0 and toks[j][0] == "word" and toks[j][1].lower() in _LABELS


def _cue_before(toks: list, j: int) -> bool:
    """A comparison just before a figure ("above", "<", "less than", "at least", "target")."""
    while j >= 0 and toks[j][0] == "word" and toks[j][1].lower() in _SKIP_BEFORE:
        j -= 1
    if j < 0:
        return False
    kind, tok, _a, _b = toks[j]
    w = tok.lower()
    before = toks[j - 1][1].lower() if j else ""
    if kind == "sym":
        return tok in "<>≤≥≦≧" or (tok == "=" and before in ("<", ">"))
    if w in _CUE_NOUNS and (before in _COMPARISONS or _has_figure(toks, j)):
        return False  # "below target 54%", "the 68°F high limit 43%": the figure is a result
    return (
        w in _CUE_BEFORE
        or (w == "than" and before in _CUE_THAN)
        or (w in ("least", "most") and before == "at")
        or (w == "to" and before == "up")
    )


def _cue_subject(toks: list, j: int) -> bool:
    """A figure stated as what a threshold, target or setpoint is (#328): "the target of AHU-1
    is 55°F", "the high-limit lockout at 65°F", "its setpoint is flat at ~55F". A cue noun
    earlier in the clause, with no figure and no comparison between, makes it a threshold."""
    seen = 0
    while j >= 0 and seen < 8:
        kind, tok, _a, _b = toks[j]
        if kind == "num" or (kind == "sym" and tok in ",;:()[]!?.|/"):
            return False
        if kind == "word":
            w = tok.lower()
            last = w.split("-")[-1]
            if w in _SUBJECT_CUES or last in _SUBJECT_CUES:
                before = toks[j - 1][1].lower() if j else ""
                return before not in _COMPARISONS and not _has_figure(toks, j)
            if not (w in _CUE_LINK or tok.startswith("QQ")):
                return False  # "the high-limit rule flagged 50 hours": not what the limit is
            seen += 1
        j -= 1
    return False


def _has_figure(toks: list, j: int) -> bool:
    """Whether the cue noun at token ``j`` comes right after a figure of its own ("68°F high")."""
    j -= 1
    while j >= 0 and toks[j][0] == "word" and toks[j][1].lower() in ("high", "low"):
        j -= 1
    while j >= 0 and (
        (toks[j][0] == "sym" and toks[j][1] in "%°")
        or (toks[j][0] == "word" and toks[j][1].lower() in _UNITS)
    ):
        j -= 1
    return j >= 0 and toks[j][0] == "num"


def _cue_after(toks: list, k: int) -> bool:
    """A limit word just after a figure and its unit ("20% min", "65°F high limit", "the 30%
    daily CV(RMSE) tier")."""
    while k < len(toks) and toks[k][0] == "word" and toks[k][1].lower() in ("high", "low"):
        k += 1
    if k < len(toks) and toks[k][0] == "word" and toks[k][1].lower() in _CUE_AFTER:
        return True
    # "90% fan speed or more": a bound
    words = [t[1].lower() for t in toks[k : k + 5] if t[0] == "word"]
    for a, b in zip(words, words[1:]):
        if a == "or" and b in _OR_BOUND:
            return True
    # "the 30% daily CV(RMSE) tier": a tier named a few words on, with no figure between
    seen = 0
    while k < len(toks) and seen < 6:
        kind, tok, _a, _b = toks[k]
        if kind == "num" or (kind == "sym" and tok in ",;:!?.|/"):
            return False
        if kind == "word":
            if tok.lower() in ("tier", "threshold", "allowance"):
                return True
            if tok.lower() in _NOUN_STOP or tok.lower() in _COMPARISONS:
                return False  # "+0.5F beyond the allowance" is a result against it
            seen += 1
        k += 1
    return False


def _scan_noun(toks: list, k: int, of: bool = False) -> str:
    """The first counted thing (or unit word) in the 3 words from token ``k``, stopping at a
    figure, punctuation or a stop word; ``of``: what a percentage is of, in 5 words and past a
    count ("46% of the 64 free-cooling hours" is of hours). A part of a total counts what its
    total counts: "20 of the 106 flagged hours", "9 out of 11 points" (#336)."""
    seen = 0
    if not of:
        x = k + (k < len(toks) and toks[k][1].lower() == "out")
        if x < len(toks) and toks[x][1].lower() == "of":
            x += 1
            while x < len(toks) and toks[x][1].lower() in _DEFINITE:
                x += 1
            if x < len(toks) and toks[x][0] == "num":
                return _scan_noun(toks, x + 1)
    if (
        not of
        and k + 1 < len(toks)
        and toks[k][1].lower() == "of"
        and toks[k + 1][1].lower() in ("them", "these", "those", "which")
    ):
        k += 2  # "2 of them returned a result"
    while k < len(toks) and seen < (5 if of else 3):
        kind, tok, _a, _b = toks[k]
        w = tok.lower()
        number = kind == "num" or (kind == "word" and _word_number(w) is not None)
        if of and number:
            seen += 1
            k += 1
            continue
        if kind != "word" or w in _NOUN_STOP or number:
            return ""
        unit = _UNITS.get(w) if len(w) > 1 else None
        if unit:
            return unit
        n = _noun(w)
        nxt = toks[k + 1][1].lower() if k + 1 < len(toks) else ""
        if n and nxt not in _CUE_NOUNS:  # "the 20.0 fault threshold" counts no faults
            return n
        seen += 1
        k += 1
    return ""


# --------------------------------------------------------------------------- facts

#: structured fields of a fact that name its subject (an equipment, a facility, a meter)
_SUBJECT_KEYS = ("equip", "facility_id", "meter", "subject")
#: structured fields that are names: never read for dates
_NAME_KEYS = frozenset((*_SUBJECT_KEYS, "id", "rule", "role", "finding_id", "finding_ids", "kind"))
#: the point roles a sentence may name, and what a fact about each says (#328): the phrases a
#: sentence uses, then the words (role ids with "_" read as spaces) a fact's text, fields or
#: subject uses
_ROLE_FAMILIES = (
    ("supply air", r"\bsupply[\s-]air\b|\bdischarge[\s-]air\b|\bSAT\b", r"\bsupply air\b|\bsat\b"),
    ("return air", r"\breturn[\s-]air\b|\bRAT\b", r"\breturn air\b|\brat\b"),
    ("mixed air", r"\bmixed[\s-]air\b|\bMAT\b", r"\bmixed air\b|\bmat\b"),
    (
        "outdoor air",
        r"\boutdoor[\s-]air\b|\boutside[\s-]air\b|\bOAT\b",
        r"\boutdoor air\b|\boutside air\b|\boat\b|\boa\b|\boaf\b",
    ),
    (
        "heating valve",
        r"\bheating[\s-]valve|\bheat[\s-]valve|\bhot[\s-]water[\s-]valve|\bHW[\s-]valve",
        r"\bheat valve\b|\bheating valve\b|\bhw\b|\bhot water\b",
    ),
    (
        "cooling valve",
        r"\bcooling[\s-]valve|\bcool[\s-]valve|\bchilled[\s-]water[\s-]valve|\bCHW[\s-]valve",
        r"\bcool valve\b|\bcooling valve\b|\bchw\b|\bchilled water\b",
    ),
    ("setpoint", r"\bset[\s-]?points?\b", r"\bsetpoints?\b|\bsp\b|\bset point\b"),
)
_ROLE_RX = tuple((n, re.compile(s, re.I), re.compile(f)) for n, s, f in _ROLE_FAMILIES)


def _labelled(qty: list) -> list:
    """A fact's figures, each with the words that tell it from the figures of the same unit
    listed with it (#332): ``86%`` of "HW-leak 86% / CHW-leak 0%" is the "heat" one, and "cool"
    is its sibling's."""
    seen: dict = {}
    for q in qty:
        if q.at >= 0 and q.role != "setting":  # "fan_heat_f = 2.0": named, not labelled
            seen[(q.at, q.unit)] = (q.pre or q.post, q.grp)
    out = []
    for q in qty:
        if (q.at, q.unit) not in seen:
            out.append(q)
            continue
        own = seen[(q.at, q.unit)][0]
        others = set()
        for (at, unit), (words, grp) in seen.items():
            if unit == q.unit and at != q.at and grp == q.grp:
                others |= words
        out.append(replace(q, label=frozenset(own - others), sib=frozenset(others - own)))
    return out


def _told_apart(qty: list) -> list:
    """A fact's figures, each with the families whose labels tell apart the figures of its unit
    in this sentence (#336): "HW-leak 86% / CHW-leak 0%" (the side), "9 trusted, 2 suspect"
    (the trust class), "FC10 ... 100%; FC11 ... 56%" (the fault condition)."""
    seen: dict = {}
    for q in qty:
        for fam, label in q.fam:
            if q.unit:  # a bare number has no unit to list others of
                seen.setdefault((q.unit, fam), set()).add(label)
    out = []
    for q in qty:
        on = frozenset(f for (u, f), labels in seen.items() if u == q.unit and len(labels) > 1)
        out.append(replace(q, famon=on) if on else q)
    return out


def _parts(qty: list) -> list:
    """A fact's counts labelled with a trust class or a verdict (#336), as ``(unit, family,
    label, value, total)``: "9 trusted" of "11 matching points judged: 9 trusted, 2 suspect" is
    ``("point", "trust", "trusted", 9, 11)``; the total is the figure of its unit before it with
    no label of that family (None when there is none)."""
    out = []
    for q in qty:
        if not q.unit or q.at < 0:
            continue
        for fam, label in q.fam:
            if fam not in ("trust", "verdict"):
                continue
            head = [
                h.value
                for h in qty
                if 0 <= h.at < q.at
                and h.unit == q.unit
                and fam not in dict(h.fam)
                and h.value >= q.value
            ]
            out.append((q.unit, fam, label, q.value, head[-1] if head else None))
    return out


#: a paging notice: "'findings' shows rows 1-25 of 223"
_PAGED = re.compile(r"'([A-Za-z_ ]+)' shows rows? (\d+)(?:-(\d+))? of (\d+)")


def _paged(text: str) -> list:
    """The figures of a paging notice, with the thing its list counts (#332): "rows 1-25 of
    223" of 'findings' are 25 and 223 findings."""
    m = _PAGED.search(text)
    noun = _noun(m.group(1).split()[-1]) if m else ""
    if m is None or not noun:
        return []
    return [
        _Qty(Decimal(v), "", noun, "", "result", False) for v in m.groups()[1:] if v is not None
    ]


class _FactClaims:
    """What one fact exposes to the matcher: the claims of its text plus its structured fields
    (``value`` and ``unit``; ``value`` / ``verdict`` / ``severity`` as a verdict; ``direction``;
    ``subject``; every string field as a name it may carry; the dates of its non-name fields;
    ``rank``). A fact with ``basis: "claimed"`` (a claim's own figure) supports a figure only in
    a sentence that presents it as a claim."""

    def __init__(self, fact, names):
        text = str(getattr(fact, "text", "") or "")
        data = getattr(fact, "data", None)
        data = data if isinstance(data, dict) else {}
        p = _Parsed()
        self.parts = []  # (unit, family, label, value, total): "9 trusted" of "11 points" (#336)
        for piece in _split_sentences(text):
            q = _parse(piece, names, fact=True)
            qty = _told_apart(_labelled(q.qty))
            self.parts += _parts(qty)
            p.qty += qty + _paged(piece)
            p.negs += q.negs
            p.counts += q.counts
            p.causes += q.causes
            p.sups += q.sups
            p.whole_words.update(q.whole_words)
            for attr in (
                "dates",
                "times",
                "ids",
                "verdicts",
                "dirs",
                "affirmed",
                "words",
                "polar",
                "quant",
                "ranks",
                "rules",
                "wholes",
            ):
                getattr(p, attr).update(getattr(q, attr))
        if "down?" in p.dirs:  # "this saving ...": as the fact's own figure gives it (#339)
            p.dirs.discard("down?")
            if "up" not in p.dirs:
                p.dirs.add("down")
        value = data.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            unit = data.get("unit")
            u = _unit_name(unit)
            try:
                d = Decimal(str(value))
                p.qty.append(_Qty(abs(d).normalize(), "-" if d < 0 else "", u, "", "result", False))
            except InvalidOperation:
                pass
        v = data.get("verdict")
        if isinstance(v, str) and v.lower() in _VALUE_DIRECTION:  # "reduced": a decrease
            p.dirs.add(_VALUE_DIRECTION[v.lower()])
        # a claim's own figure (``basis: "claimed"``) supports only a sentence that says it is a
        # claim, never a measured statement
        self.claimed = data.get("basis") == "claimed" or data.get("kind") == "claimed_figure"
        own = set()  # the verdicts its structured fields give it (not its text's lists)
        for key in ("value", "verdict", "severity"):
            v = data.get(key)
            if isinstance(v, str) and v.lower() in _VALUE_VERDICT:
                p.verdicts.add(_VALUE_VERDICT[v.lower()])
                if key != "severity":
                    own.add(_VALUE_VERDICT[v.lower()])
        self.own = own
        # a finding re-judged (#332): its severity before the re-judge is not its verdict, so it
        # supports a sentence only where that names the finding ("the leaking_valve fault")
        self.superseded = set()
        sev = str(data.get("severity") or "").lower()
        if data.get("kind") == "reference_effect" and _VALUE_VERDICT.get(sev) not in own:
            self.superseded = {_VALUE_VERDICT[sev]} if sev in _VALUE_VERDICT else set()
        v = data.get("direction")
        if isinstance(v, str) and v.lower() in _VALUE_DIRECTION:
            p.dirs.add(_VALUE_DIRECTION[v.lower()])
        for holder in (data, data.get("data") if isinstance(data.get("data"), dict) else {}):
            r = holder.get("rank")
            if isinstance(r, int) and not isinstance(r, bool):
                p.ranks.add(r)
        if any(isinstance(q.value, Decimal) and q.unit == "%" and q.value == 100 for q in p.qty):
            p.quant.add("all")
        if any(q.unit == "%" and 50 < q.value < 100 and q.role == "result" for q in p.qty):
            p.quant.add("most")
        if re.search(r"\b(?:all|every|always|entire|whole)\b", text, re.I):
            p.quant.add("all")
        if re.search(r"\b(?:most|majority)\b", text, re.I):
            p.quant.add("most")
        strings = [text.lower(), str(getattr(fact, "equip", "") or "").lower()]
        _strings(data, strings, p)
        self.claims = p
        self.hay = "\n".join(strings)
        roles = re.sub(r"[_\-]+", " ", self.hay)
        self.roles = {n for n, _s, f in _ROLE_RX if f.search(roles)}
        subj = data.get("subject") if isinstance(data.get("subject"), dict) else {}
        self.point = bool(data.get("role") or subj.get("role"))  # a fact about one point
        self.subjects = {
            str(v).lower()
            for v in (
                getattr(fact, "equip", ""),
                data.get("role"),
                data.get("facility_id"),
                *(
                    (data["subject"].get(k) for k in ("equip", "role", "facility_id"))
                    if isinstance(data.get("subject"), dict)
                    else ()
                ),
            )
            if v
        }
        # what it is about, by kind of item (#338): its equipment, point, meter, facility
        eq = str(getattr(fact, "equip", "") or subj.get("equip") or "")
        role = str(data.get("role") or subj.get("role") or "")
        self.item = {
            "equip": eq,
            "point": f"{eq} {role}".strip() if eq and role else "",
            "meter": str(data.get("meter") or subj.get("meter") or eq),
            "facility": str(data.get("facility_id") or subj.get("facility_id") or "")
            if not eq
            else "",
            "role": role,  # a point's name alone ('"heat_valve" and "oa_damper"', #339)
        }
        self.has_rule = bool(data.get("rule") or data.get("finding_id") or p.rules) or (
            isinstance(data.get("subject"), dict) and bool(data["subject"].get("finding"))
        )

    def names(self, name: str) -> bool:
        if name in self.claims.ids:
            return True
        return re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", self.hay) is not None


def _strings(data, out: list, p: _Parsed, key: str = "", depth: int = 0) -> None:
    """The string values of ``data`` into ``out`` (lower case), and the dates of its non-name
    fields into ``p``."""
    if depth > 8:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if not (depth == 0 and k == "text"):
                _strings(v, out, p, str(k), depth + 1)
    elif isinstance(data, (list, tuple)):
        for v in data:
            _strings(v, out, p, key, depth + 1)
    elif isinstance(data, str):
        out.append(" ".join(data.split()).lower())
        if key not in _NAME_KEYS:
            p.dates.update(_date(m) for m in _DATE.finditer(data))


def _unit_name(unit) -> str:
    """A fact's ``unit`` field as the matcher names units (``°F`` -> ``degF``, ``%``, ``kWh``)."""
    if not isinstance(unit, str) or not unit.strip():
        return ""
    key = unit.strip().lower().replace("°", "").replace(" ", "")
    return _UNITS.get(key) or unit.strip()


def _known_names(context) -> list:
    """The subject names of every fact in ``context``: lower case, longest first."""
    names = set()
    for f in getattr(context, "facts", None) or []:
        data = getattr(f, "data", None)
        vals = [getattr(f, "equip", "")]
        if isinstance(data, dict):
            vals += [data.get(k) for k in _SUBJECT_KEYS]
            subj = data.get("subject")
            if isinstance(subj, dict):  # {"facility_id", "equip", "role", ...}
                vals += [subj.get(k) for k in ("facility_id", "equip", "meter")]
        for v in vals:
            if isinstance(v, str):
                n = " ".join(v.split()).lower()
                if len(n) >= 3 and any(c.isalpha() for c in n) and not n.startswith("<"):
                    names.add(n)
    return sorted(names, key=lambda n: (-len(n), n))


# --------------------------------------------------------------------------- judging


def _bound(q: _Qty, f: _Qty) -> bool:
    """Whether a sentence's figure ``q`` is said of what the fact's figure ``f`` is (#332): the
    words of its clause before it (else after it) must not name only what another figure listed
    with ``f`` is of ("the cooling valve leaked on 86%" against "HW-leak 86% / CHW-leak 0%")."""
    if q.total or not f.sib:
        return True
    for seg in (q.pre, q.post):
        own, other = seg & f.label, seg & f.sib
        if own or other:
            return bool(own)
    return True


def _same_label(q: _Qty, f: _Qty) -> bool:
    """Whether a sentence's figure ``q`` has the closed-family labels of the fact's figure
    ``f`` (#336): a label of the same family must be the same one ("the hot-water valve leaked
    on 60%" against "HW-leak 0% / CHW-leak 60%"); where the fact tells its figures apart by a
    family, the sentence must say which ("the valve leaked on 60%", "1 point is suspect" against
    "1 untrusted; 1 below the trust floor"). A total ("of the 11") is no label's."""
    if q.total:
        return True
    mine, theirs = dict(q.fam), dict(f.fam)
    for fam in set(mine) | set(theirs):
        a, b = mine.get(fam), theirs.get(fam)
        if a is not None and b is not None and a != b:
            return False
        if (a is None) != (b is None) and fam in f.famon:
            return False
    return True


def _match(q: _Qty, facts: list, claim: bool = False, bound: bool = True) -> bool:
    for fc in facts:
        if fc.claimed and not claim:
            continue  # a claim's own figure never supports a measured statement
        for f in fc.claims.qty:
            if f.value != q.value or (f.role != q.role and "setting" not in (f.role, q.role)):
                continue
            if ((q.sign == "-") != (f.sign == "-")) != (q.flip and f.flip):
                continue
            if q.sign == "±" and f.sign != "±":
                continue
            if (q.unit and f.unit != q.unit) or (q.base and f.base != q.base):
                continue
            if bound and not (_bound(q, f) and _same_label(q, f)):
                continue
            return True
        if q.unit in _VERDICT_UNITS and q.of_noun:  # "one ok result" (#338): 1 result, ok
            for f in fc.claims.qty:
                if (
                    f.unit == q.of_noun
                    and f.value == q.value
                    and f.role == q.role
                    and dict(f.fam).get("verdict", q.unit) == q.unit
                    and q.unit in fc.claims.verdicts
                ):
                    return True
        if q.year and any(d[0] == int(q.value) for d in fc.claims.dates):
            return True
    return False


#: how a why names a closed family, and its labels (#336)
_FAMILY_NAME = {
    "side": "side (heating or cooling)",
    "trust": "trust class",
    "verdict": "verdict",
    "fc": "fault condition",
}


def _label_name(label: tuple) -> str:
    fam, lab = label
    if fam == "side":
        return {"heat": "heating", "cool": "cooling"}.get(lab, lab)
    if fam == "fc":
        return "FC" + "".join(str("abcdefghij".index(c)) for c in lab[2:-1])
    return lab


def _told_why(q: _Qty, facts: list) -> str:
    """Why a figure a cited fact gives fails on its closed-family label (#336), or ""."""
    cands = [
        f
        for fc in facts
        if not (fc.claimed and not q.claim)
        for f in fc.claims.qty
        if f.value == q.value
        and (q.sign == "-") == (f.sign == "-")
        and not (q.unit and f.unit != q.unit)
        and (f.role == q.role or "setting" in (f.role, q.role))
    ]
    if not cands or any(_same_label(q, f) for f in cands):
        return ""
    mine = dict(q.fam)
    for f in cands:
        theirs = dict(f.fam)
        for fam in sorted(set(mine) | set(theirs)):
            a, b = mine.get(fam), theirs.get(fam)
            if a is not None and (b is None or a != b) and (b is not None or fam in f.famon):
                given = sorted(
                    {_label_name((fam, dict(g.fam)[fam])) for g in cands if fam in dict(g.fam)}
                )
                as_ = f" for {', '.join(map(repr, given))}," if given else ", but"
                said = _label_name((fam, a))
                return f"{q.show()}: a cited fact gives this figure{as_} not as {said!r}"
            if a is None and b is not None and fam in f.famon:
                return (
                    f"{q.show()}: a cited fact gives this figure for {_label_name((fam, b))!r} and "
                    f"others for another {_FAMILY_NAME[fam]}: say which"
                )
    return ""


def _set_counted(noun: str, facts: list) -> bool:
    """Whether a cited fact counts the items a set names ("2 suspect" for "both suspect
    points"): the set is a counted thing then, read as one (#338)."""
    unit = _counted(noun)
    return bool(unit) and any(f.unit == unit for fc in facts for f in fc.claims.qty)


def _number_problems(p: _Parsed, facts: list) -> list:
    why = []
    sets = {st.k for st in p.sets if st.k is not None and not _set_counted(st.noun, facts)}
    shares = [c for c in p.counts if c.how.startswith("frac")]
    said_by = [(t, fc.claims.words) for fc in facts for t in fc.claims.counts]
    parts = {x[0] for fc in facts for x in fc.parts}
    for q in p.qty:
        if q.of_set and q.value in sets:
            continue  # the set's check reads it
        if q.share:  # "at least half of the 11 points": read with a cited fact's count of them
            if any(c.noun in parts or _same(c, said_by) for c in shares):
                continue
            why.append(
                f"'{'half' if q.value == Decimal('0.5') else 'quarter'}': no cited fact gives a "
                "count of them to read this share by (say it as the fact's count)"
            )
            continue
        if _match(q, facts, q.claim):
            continue
        if q.unit == "rule" and q.role == "result":
            # "1 rule returned a fault" against "3 rules ... on 1 equipment ...: 1 fault": on
            # one equipment each rule gives one result (#339); on more, a result is not a rule
            counts = [
                fc
                for fc in facts
                if any(
                    f.unit in _VERDICT_UNITS and f.value == q.value and f.unit in p.verdicts
                    for f in fc.claims.qty
                )
            ]
            if any(
                any(f.unit == "equipment" and f.value == 1 for f in fc.claims.qty) for fc in counts
            ):
                continue
            if counts:
                verdict = sorted(
                    f.unit
                    for fc in counts
                    for f in fc.claims.qty
                    if f.unit in _VERDICT_UNITS and f.value == q.value and f.unit in p.verdicts
                )[0]
                why.append(
                    f"{q.show()}: the cited fact counts rule-equipment results, not rules: say "
                    f"it as its count ('{q.value:f} {verdict}')"
                )
                continue
        told = _told_why(q, facts)
        if told:
            why.append(told)
        elif _match(q, facts, q.claim, bound=False):
            other = sorted(
                {w for fc in facts for f in fc.claims.qty for w in f.sib & (q.pre | q.post)}
            )
            why.append(
                f"{q.show()}: a cited fact gives this figure for another of the things it lists "
                f"(not {', '.join(map(repr, other))})"
            )
        elif signed := [
            f
            for fc in facts
            for f in fc.claims.qty
            if f.value == q.value
            and f.unit == q.unit
            and ((f.sign == "-") != (q.sign == "-")) != (q.flip and f.flip)
        ]:
            mine = "-" if q.sign == "-" else "+"
            size = q.show().lstrip("-")
            f = signed[0]
            given = f"{'-' if f.sign == '-' else '+'}{size}"
            if f.flip and q.flip:
                why.append(
                    f"{mine}{size}: a cited fact gives {given} as the correction; a bias taken "
                    f"off is that correction with its sign changed"
                )
            elif f.flip:
                why.append(
                    f"{mine}{size}: a cited fact gives this figure with the other sign ({given}), "
                    "as a correction: a correction is the bias with its sign changed; say which "
                    "it is"
                )
            else:
                why.append(
                    f"{mine}{size}: a cited fact gives this figure with the other sign ({given})"
                )
        elif any(f.value == q.value for fc in facts for f in fc.claims.qty):
            why.append(
                f"{q.show()}: a cited fact has this number, but with another sign, unit, "
                "counted thing or role (a threshold, target or setpoint is not a result)"
            )
        else:
            why.append(f"{q.show()}: no cited fact states this figure")
    for d in sorted(p.dates, key=str):
        if not any(
            fd[:3] == d[:3] and (d[3] is None or fd[3] == d[3])
            for fc in facts
            for fd in fc.claims.dates
        ):
            why.append(f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}: no cited fact carries this date")
    for t in sorted(p.times):
        if not any(t in fc.claims.times or t in {fd[3] for fd in fc.claims.dates} for fc in facts):
            why.append(f"{t[0]:02d}:{t[1]:02d}: no cited fact carries this time")
    return why


_ZEROS = frozenset({"zero", "share"})
#: words that put a counted thing on the other side of what a fact's zero is about (#336)
_ELSEWHERE = frozenset({"outsid", "beyond"})


def _says_too(n: _Neg, f: _Neg, names=frozenset()) -> bool:
    """Whether a fact's negation (or zero) ``f`` says the sentence's negation ``n``: about the
    same words (the fact's are all in the sentence's clause), negating the same thing."""
    if n.kind == "frame":
        return False
    if "share" in (n.kind, f.kind):
        # the counted thing a fact's zero inherited ("0 untrusted" of "11 points") may be left
        # implicit ("of those, 0 are untrusted"), and what a share is of ("CHW-leak 0%")
        need = f.words - {f.head} - ({f.subject} if f.subject and not n.subject else set())
        if not need <= n.clause:
            return False
        return n.head in f.words
    if n.kind in _ZEROS or f.kind in _ZEROS:
        # a zero is read with its own clause (#332): what the fact's zero is of and its first
        # verb ("0 rule(s) skipped ...") must be in the sentence's, the sentence's first verb in
        # the fact's list item ("0 rules returned a fault" is not "0 rules skipped"), and the
        # sentence must not deny what the fact's counted things are ("None of the 8 hours was
        # flagged" against "none of the 8 flagged hours falls ...")
        req = set(f.mods) | ({f.lead} if f.lead else set())
        if n.there and f.lead:
            # "there were no flagged hours in the window": what the fact's zero says, in the
            # sentence's words after it, stands for its verb (#338)
            place = n.pred - _GENERIC - _NOUN_STEMS
            if place and place <= f.pred | (f.ctx or f.clause):
                req.discard(f.lead)
        if not req and not f.ctx:
            req = set(f.words) - {f.head}
        if not req <= n.clause:
            return False
        if not (n.head == f.head or n.head in f.words or f.head in n.clause):
            return False
        ctx = f.ctx or f.clause or f.words
        if n.lead and n.lead not in _GENERIC | _NOUN_STEMS | f.words | ctx:
            return False
        if n.pred & _ELSEWHERE - (f.words | ctx):
            return False  # "0 of the 8 hours fall outside the window": not "none ... in" (#336)
        return not (n.pred & f.mods)
    own = f.words - ({f.subject} if f.subject and not f.named else set())
    if not own <= n.clause:
        return False
    if names is not None and f.named and f.subject and f.subject.lower() not in names:
        # "measured_fan_heat_f not set" is about that setting, not what the fact is about: the
        # sentence must name it, or say all of its words ("no fan heat was set" is not it, #336)
        parts = {_stem(w) for w in re.split(r"[^a-z]+", f.subject.lower()) if len(w) > 1}
        if not parts or not parts <= n.clause:
            return False
    return n.head in f.words or f.head in n.after


def _items_of(facts: list, kind: str) -> dict:
    """The items of ``kind`` the cited facts are about, each with its facts (#338)."""
    items: dict = {}
    for fc in facts:
        key = fc.item.get(kind, "")
        if key:
            items.setdefault(key, []).append(fc)
    return items


def _about(fc) -> set:
    """What a cited fact is about, by name (#339): its equipment, point, role, meter, facility."""
    return {str(v).lower() for v in fc.item.values() if v}


#: what ends the part of a clause a figure is said in (#339): "at 0.66 on AHU-1 | and 0.68 on
#: AHU-2"
_PART_END = frozenset("and or but while whereas".split())


def _attributed(p: _Parsed, q: _Qty, st) -> set:
    """The names a sentence gives a figure in its own part of the clause, not the set's (#339):
    "at 0.66 on AHU-1 and 0.68 on AHU-2" gives 0.66 to AHU-1, "0.83 vs 0.35 in. w.c. on
    AHU-1" both figures to AHU-1."""
    toks = p.toks
    if not toks or not 0 <= q.at < len(toks):
        return set()

    def edge(x):
        return toks[x][1] in (",", ";", ":", "|", ".", "(", ")") or toks[x][1].lower() in _PART_END

    a = q.at
    while a > 0 and not edge(a - 1) and not _verbish(toks[a - 1][1].lower()):
        a -= 1  # not back past its verb: "A and B are suspect at 0.66" names no figure's
    b = q.at + 1
    while b < len(toks) and not edge(b):
        b += 1
    return {
        p.marks[t[1]].lower()
        for x, t in enumerate(toks[a:b], a)
        if t[1].startswith(_NAME) and t[1] in p.marks and not (st.start <= x <= st.end)
    }


def _clause_start(p: _Parsed, start: int) -> int:
    """The first token of the clause a set at ``start`` opens, when another clause stands
    before it in its sentence ("Both AHUs warn ...; both also fail ...", "..., and both fail
    ..."), else 0 (#339)."""
    toks = p.toks
    x = start - 1
    while x >= 0:
        tok, w = toks[x][1], toks[x][1].lower()
        if tok == ";":
            return x + 1
        if w in _SET_END or (
            w in ("and", "but", "so", "while") and x >= 1 and toks[x - 1][1] == ","
        ):
            return x + 1
        if toks[x][0] == "word" and not tok.startswith("QQ") and w not in _FUNCTION:
            return 0  # a word of its own clause before it: the set is not what opens it
        x -= 1
    return 0


def _listed(p: _Parsed, st) -> set:
    """The tokens of a set's figures given as a list, one for each item ("suspect at 0.66 and
    0.68", "(0.68 to 0.70)", "15.8% and 33.6% respectively", #339)."""
    toks = p.toks
    ats = sorted({q.at for q in p.qty if st.start <= q.at < st.scope and not q.of_set})
    out: set = set()
    run: list = ats[:1]
    for a, b in zip(ats, ats[1:]):
        between = [t[1].lower() for t in toks[a + 1 : b] if t[0] != "num"]
        between = [w for w in between if w not in ("%", "°", "f", "c")]
        if between and set(between) <= {"and", "or", "to", ",", "-", "–", "/"}:
            run.append(b)
            continue
        if len(run) > 1:
            out.update(run)
        run = [b]
    if len(run) > 1:
        out.update(run)
    return out


_UNMASK = re.compile(r"QQ(NAME|TERM|DATE|TIME)[a-z]+")


def _unmask(s: str, marks: dict) -> str:
    """Masked text with its names (quoted), terms, dates and times written back (#339)."""

    def sub(m):
        v = marks.get(m.group(0))
        if v is None:
            return m.group(0)
        return f'"{v}"' if m.group(1) == "NAME" else str(v)

    return _UNMASK.sub(sub, s)


def _span_text(p: _Parsed, a: int, b: int) -> str:
    """Tokens ``a`` to ``b`` (exclusive) of a sentence as text, names written back (#339)."""
    if a >= b or a >= len(p.toks):
        return ""
    b = min(b, len(p.toks))
    return _unmask(p.masked[p.toks[a][2] : p.toks[b - 1][3]], p.marks)


def _negated_item(p: _Parsed, st, vocab) -> _Parsed:
    """What a negated set says of each of its items, as a sentence of its own (#339): "Neither
    A nor B has a valve leak" says of each "It has not a valve leak"; "No AHU's sensor
    disagrees" says "Its sensor does not disagree"."""
    pred = _span_text(p, st.pred, st.scope)
    head = "It"
    if st.poss and st.pred > st.end + 1:
        head = "Its " + _span_text(p, st.end + 1, st.pred).lstrip("'’s ").strip()
    first, _sp, rest = pred.partition(" ")
    if first.lower() in _AUX:
        text = f"{head} {first} not {rest}"
    else:
        text = f"{head} does not {pred}"
    out = _parse(text, vocab)
    out.ids -= {n for n in st.names}
    return out


def _set_problems(p: _Parsed, facts: list, vocab: _Vocab, text: str, only=None) -> list:
    """What a sentence says of every one of a set of items, against the cited facts (#338,
    #339). The set is read against the items it names, or the items the cited facts are about
    (an equipment, a point, a meter, a facility): each of them must have a cited fact that
    states the claim of it on its own -- its figures (but a figure the sentence gives another
    of its items: "at 0.66 on AHU-1 and 0.68 on AHU-2"), verdicts, directions, negations and
    point roles -- so a fact about one that says otherwise fails the sentence; a negated set
    ("neither A nor B", "no AHU", "none of the AHUs") needs the negation stated of each; "both"
    or a number needs that many items so stated, and a set with no number at least two. With no
    cited fact about one item, a cited fact's verdict counts must give every result the
    sentence's verdict. A set a cited fact counts ("both suspect points" against "2 suspect") is
    a counted thing instead, and one a cited fact says itself is its words. A set the checker
    finds but cannot read (a negated one that does not open its clause, or with no verb after
    it) fails closed."""
    why = []
    for st in p.sets if only is None else only:
        said = st.said
        if st.noun and _set_counted(st.noun, facts):
            continue
        low = said.lower()
        if (st.noun or st.names) and any(
            low in fc.hay or low.replace("each ", "every ") in fc.hay for fc in facts
        ):
            continue
        if st.neg and st.pred < 0:
            why.append(
                f"{said!r}: a negated set is read only where it opens its clause with a verb "
                "after it: say it of each item"
            )
            continue
        item_p = _negated_item(p, st, vocab) if st.neg else None
        whole, sst = p, st
        off = _clause_start(p, st.start)
        if not st.neg and (
            off > 0
            or (st.scope < len(p.toks) and any(t[0] in ("word", "num") for t in p.toks[st.scope :]))
        ):
            # what its own clause says of it, not the clause before or after ("Both AHUs
            # warn ..., but only AHU-1 fails"): its per-item reading is that clause (#339)
            cut = _parse(_span_text(p, off, st.scope), vocab)
            same = [x for x in cut.sets if (x.start, x.end) == (st.start - off, st.end - off)]
            if same:
                whole, sst = cut, same[0]
        trust = (item_p or whole).verdicts & {
            *_TRUST_CLASSES,
            *(f"not {c}" for c in _TRUST_CLASSES),
        }

        def stated(fcs, kind="", st=st, item_p=item_p, whole=whole, sst=sst, trust=trust):
            if kind == "equip" and trust and st.opens and not st.poss:
                fcs = [fc for fc in fcs if not fc.point]  # a point's trust is not its unit's
            # a cited fact about it, or the cited facts about it together, state it
            tries = [[fc] for fc in fcs] + ([fcs] if len(fcs) > 1 else [])
            if item_p is not None:
                return any(
                    not _number_problems(item_p, these)
                    and not _claim_problems(item_p, these, vocab, "")
                    for these in tries
                )
            return any(_states_alone(whole, these, facts, vocab, text, sst) for these in tries)

        if st.names:
            seen = 0
            for name in st.names:
                about = [fc for fc in facts if name in _about(fc)]
                if not about:
                    continue  # no cited fact names it: the names check says so
                seen += 1
                if not stated(about):
                    why.append(f"{said!r}: no cited fact states it of {name!r}")
            if not st.slots:
                continue
            kind = st.kind or "equip"
            rest = {
                key: fcs
                for key, fcs in _items_of(facts, kind).items()
                if not any(_about(fc) & set(st.names) for fc in fcs)
            }
            bad = [key for key, fcs in rest.items() if not stated(fcs)]
            if bad:
                why.append(f"{said!r}: no cited fact states it of {', '.join(map(repr, bad))}")
            elif len(rest) < st.slots:
                why.append(
                    f"{said!r}: the cited facts state it of {len(rest) + seen} of them; cite a "
                    "fact that states it of each"
                )
            continue
        kind = st.kind
        if not kind:  # "both" or "each" with no noun: the kind the cited facts tell apart
            kind = next(
                (
                    c
                    for c in ("point", "equip", "meter", "facility")
                    if len(_items_of(facts, c)) >= 2
                ),
                "",
            )
            if not kind:
                if st.pron and len(facts) > 1:
                    why.append(
                        f"{said!r}: the cited facts are not about 2 or more items to read it by; "
                        "say it of each item, naming it"
                    )
                continue
        items = _items_of(facts, kind)
        if not items:
            labels = p.verdicts & _VERDICT_UNITS
            counts = [x for fc in facts for x in fc.parts if x[1] == "verdict"]
            others = sorted({(x[2], x[3]) for x in counts if x[2] not in labels and x[3] > 0})
            if labels and counts and others:
                given = ", ".join(f"{v:f} {lab}" for lab, v in others)
                why.append(
                    f"{said!r}: a cited fact counts {given}: not every result is "
                    f"{', '.join(sorted(labels))}"
                )
            elif st.neg or (
                not (labels and counts)
                and any(st.start <= q.at < st.scope and not q.of_set for q in p.qty)
            ):
                # no cited fact is about one of them: what it says of each is not read (#339)
                why.append(
                    f"{said!r}: no cited fact is about one of these items: say it of each, "
                    "citing a fact about each"
                )
            continue
        ok = [key for key, fcs in items.items() if stated(fcs, kind)]
        bad = [key for key in items if key not in ok]
        k = st.k
        need = k if k is not None else 2
        if bad:
            why.append(f"{said!r}: no cited fact states it of {', '.join(map(repr, bad))}")
        elif len(ok) < need:
            of = f" ({', '.join(map(repr, ok))})" if ok else ""
            more = "both of them" if need == 2 and k is not None else f"{need:f} of them"
            why.append(
                f"{said!r}: the cited facts state it of {len(ok)} of them{of}; cite a fact that "
                f"states it of each ({more})"
                if k is not None
                else f"{said!r}: the cited facts state it of {len(ok)} of them{of}; a claim about "
                "each of a set needs a cited fact stating it of each one"
            )
    return why


def _states_alone(p: _Parsed, fcs: list, facts: list, vocab: _Vocab, text: str, st=None) -> bool:
    """Whether the cited facts ``fcs`` (about one item) state what the sentence says of each
    item on their own (#338, #339): its verdicts, directions, negations and point roles, and its
    figures: a figure in what the set's clause says of it must be theirs (#339: "Both meters
    save 2.5%" against -15.6% on one), but a figure the sentence gives another named item ("at
    0.68 on AHU-2") is that item's, and figures given as a list or "respectively" are each one
    item's; a figure outside the set's clause is the item's only where another cited fact does
    not give it. Its names are the items'."""
    others = [f for f in facts if all(f is not fc for fc in fcs)]
    mine = set().union(*(_about(fc) for fc in fcs))
    fc = fcs
    items = set().union(*(_about(f) for f in facts))  # a facility named is no item's
    words = [t[1].lower() for t in p.toks] if p.toks else []
    resp = st is not None and "respectively" in words[st.start : st.scope]
    lists = _listed(p, st) if st is not None else set()
    keep, hits, listed = [], 0, 0
    for q in p.qty:
        if q.of_set:
            continue
        if st is not None and q.at >= 0:
            named = _attributed(p, q, st) & items
            if named:
                if named & mine:
                    keep.append(q)
                continue
            if st.start <= q.at < st.scope:
                if resp or q.at in lists:
                    listed += 1
                    hits += _match(q, fc, q.claim)
                    continue
                keep.append(q)
                continue
        if _match(q, fc, q.claim) or not _match(q, others, q.claim):
            keep.append(q)
    if listed and not hits:
        return False
    alone = replace(
        p,
        qty=keep,
        ids=set(),
        quant=set(),
        wholes=set(),
        counts=[],
        sets=[],
        named=[],
        sups=[],
        all_labels=set(),
    )
    return not _number_problems(alone, fc) and not _claim_problems(alone, fc, vocab, text)


def _name_problems(p: _Parsed, facts: list, vocab: _Vocab, text: str) -> list:
    """The names, rules and point roles a sentence names that no cited fact does."""

    def named(n):
        if any(fc.names(n) for fc in facts):
            return True
        fid = vocab.aliases.get(n)  # a facility's registry name: a fact naming its id names it
        return fid is not None and any(fc.names(fid) for fc in facts)

    why = [f"{n!r}: no cited fact names it" for n in sorted(p.ids) if not named(n)]
    if p.rules and any(fc.has_rule for fc in facts):
        why += [
            f"rule {r!r}: no cited fact is about it"
            for r in sorted(p.rules)
            if not any(fc.names(r) for fc in facts)
        ]
    # a point the sentence names, when the cited facts are about a point; a setpoint also when
    # they name another point ("the setpoint is 55°F" for a measured supply air median)
    about_point = any(fc.point for fc in facts)
    about_any = about_point or any(fc.roles for fc in facts)
    if text and about_any:
        vis = _visible(text)
        for name, srx, _frx in _ROLE_RX:
            if not (about_point or name == "setpoint"):
                continue
            if srx.search(vis) and not any(name in fc.roles for fc in facts):
                why.append(f"the {name} point: no cited fact is about it")
    return why


def _outside(p: _Parsed, sets: list, vocab: _Vocab) -> _Parsed:
    """The parse of what a sentence says outside the clauses of ``sets`` (#339), names not
    read (the whole sentence's names are)."""
    gone = {x for st in sets for x in range(st.start, st.scope)}
    keep = [x for x in range(len(p.toks)) if x not in gone]
    pieces: list = []
    run: list = []
    for x in keep:
        if run and x != run[-1] + 1:
            pieces.append(_span_text(p, run[0], run[-1] + 1))
            run = []
        run.append(x)
    if run:
        pieces.append(_span_text(p, run[0], run[-1] + 1))
    rest = " ".join(pieces)
    if not re.search(r"[A-Za-z]{2}", _UNMASK.sub(" ", rest)):
        return _Parsed()
    out = _parse(rest, vocab)
    out.ids, out.rules = set(), set()
    return out


def _claim_problems(
    p: _Parsed, facts: list, vocab: _Vocab = _NO_VOCAB, text: str = "", bound=None
) -> list:
    negsets = [
        st
        for st in p.sets
        if st.neg and st.pred >= 0 and not (st.noun and _set_counted(st.noun, facts))
    ]
    if negsets:
        # a negated set ("Neither A nor B has a valve leak") is read item by item (#339): its
        # clause's negation, zero and verdicts are each item's, not the cited facts' together
        return (
            _name_problems(p, facts, vocab, text)
            + _claim_problems(_outside(p, negsets, vocab), facts, vocab, "")
            + _set_problems(p, facts, vocab, text, negsets)
            + _set_problems(p, facts, vocab, text, [st for st in p.sets if st.neg and st.pred < 0])
        )
    why = _name_problems(p, facts, vocab, text)
    verdicts = set().union(*(fc.claims.verdicts for fc in facts))
    final = set().union(*(fc.claims.verdicts - fc.superseded for fc in facts))
    stated = set(p.verdicts)
    for cls in _TRUST_CLASSES:  # "not trusted": a cited fact's "suspect" or "untrusted" (#339)
        if f"not {cls}" in stated and final & (_TRUST_CLASSES - {cls}):
            stated.discard(f"not {cls}")
    if "explained" in stated and not p.explains and p.causes:
        # a cause the sentence names after its effect, stated by a cited fact in its words (a
        # counted thing may stand for it: "that sensor") and of the same effect, is the fact's
        # ("declined because of the untrusted input" against "declined because an input scored
        # below the trust floor ... untrusted", #336)
        fcauses = [c for fc in facts for c in fc.claims.causes]
        if all(
            any(
                cause - _GENERIC - _NOUN_STEMS <= fcause and effect - _GENERIC & feffect
                for _s, feffect, fcause in fcauses
            )
            for _said, effect, cause in p.causes
        ):
            stated.discard("explained")
    for v in sorted(stated - final):
        if v in verdicts and v not in p.stated:
            continue  # "the leaking_valve fault": the finding's name, not a verdict on it now
        said = p.vraw.get(v, v)
        if v in verdicts:
            why.append(f"verdict {said!r}: the cited fact re-judged it, and says otherwise now")
            continue
        word = "word" if v in _ASSURANCE or v.startswith("not ") else "verdict"
        if said.lower().split()[-2:] and tuple(said.lower().split()[-2:]) in _CAUSAL_PAIRS:
            word = "cause or effect"
        why.append(f"{word} {said!r}: no cited fact states it")
    dirs = set().union(*(fc.claims.dirs for fc in facts))
    words = {"up": "an increase", "down": "a decrease or saving", "waste": "waste"}
    for d in sorted(p.dirs - dirs):
        if d == "down?":  # "the savings result", "this saving is ...": a saving named (#339)
            if "down" not in dirs and not any(
                x.startswith("sav") for fc in facts for x in fc.claims.words
            ):
                why.append(f"{words['down']}: no cited fact states it")
            continue
        if d.startswith("not "):  # "did not save": a cited fact's increase says it (#339)
            if {"up": "down", "down": "up"}[d[4:]] not in dirs:
                why.append(f"no {words[d[4:]]}: no cited fact states the opposite")
            continue
        why.append(f"{words[d]}: no cited fact states it")
    polar = set().union(*(fc.claims.polar for fc in facts))
    why += [
        f"{c!r}: the cited facts say {_OPPOSITE[c]!r}"
        for c in sorted(p.polar)
        if c not in polar and _OPPOSITE[c] in polar
    ]
    quant = set().union(*(fc.claims.quant for fc in facts))
    if any(
        st.how in ("all", "every")
        and not _set_counted(st.noun, facts)
        and any(fc.item.get(st.kind) for fc in facts)
        for st in p.sets
    ):
        quant.add("all")  # "every AHU": read against the items the cited facts are about (#338)
    several = " (a claim about all of several facts is not read: say it of each)" * (len(facts) > 1)
    for qc in sorted(p.quant - quant):
        what = "all or every" if qc == "all" else "most"
        why.append(f"'{what}': no cited fact says {what}{several}")
    parts = [x for fc in facts for x in fc.parts]
    for fam, label in sorted(p.all_labels):  # "they are all trusted", "all ok" (#338)
        of = [x for x in parts if x[1] == fam]
        if fam == "verdict":
            ok = bool(of) and not any(x[3] > 0 for x in of if x[2] != label)
            ok = ok and any(x[3] > 0 for x in of if x[2] == label)
        else:
            ok = any(x[2] == label and x[4] is not None and x[3] == x[4] for x in of)
        if not ok:
            why.append(f"'all {label}': no cited fact counts all of them as {label}")
    wholes = set().union(*(fc.claims.wholes for fc in facts))
    # "5 of 5 rules passed": a verdict said of a whole set, read against the verdict counts
    # (#339), and its number a cited figure
    vparts = [x for x in parts if x[1] == "verdict"]
    wholes |= {
        c.k
        for c in p.counts
        if c.how in ("all", "def")
        and c.k is not None
        and vparts
        and {lab for fam, lab in c.predfam if fam == "verdict"}
        and not _verdict_set(c, parts, facts)
        and any(f.unit == c.noun and f.value == c.k for fc in facts for f in fc.claims.qty)
    }
    # "all 3 AHUs", "each of the 2 AHUs": the set's check reads it (#338)
    wholes |= {st.k for st in p.sets if st.k is not None and not _set_counted(st.noun, facts)}
    for w in sorted(p.wholes - wholes):
        said = p.whole_words.get(w, frozenset())
        if any(w in fc.claims.whole_words and said <= fc.claims.whole_words[w] for fc in facts):
            continue  # "all 14 are below 90%" against "14 points ...; 14 are below 90%"
        why.append(f"'all {w:f}': no cited fact says all {w:f} of them (a part or none is not all)")
    ranks = set().union(*(fc.claims.ranks for fc in facts))
    if p.ranks and ranks:
        why += [
            f"issue {r}: the cited facts rank {', '.join(str(x) for x in sorted(ranks))}"
            for r in sorted(p.ranks - ranks)
        ]
    elif p.said_ranks:  # a rank said as one comes from a cited fact (#332)
        why += [f"rank {r}: no cited fact gives a rank" for r in sorted(p.said_ranks)]
    fsups = [s for fc in facts for s in fc.claims.sups]
    for word, obj, said in p.sups:
        own = obj - _NOUN_STEMS  # "the worst point": of a point, as the fact's name says
        if (1 in ranks and word not in _BEST) or any(
            w == word and own <= o and (own or not o) for w, o, _s in fsups
        ):
            continue
        why.append(f"{said!r}: no cited fact ranks it so")
    why += _count_problems(p, facts)
    if p.sets:
        why += _set_problems(p, facts, vocab, text)
    names = frozenset(
        str(v).lower() for t, v in p.marks.items() if not t.startswith(("QQDATE", "QQTIME"))
    )
    fnegs = [f for fc in facts for f in fc.claims.negs]
    own = set().union(*(fc.own for fc in facts))
    # "None of the 59 judged samples were flagged" against "0 of 59 judged both-valves-shut
    # samples": the count of the same total says the zero (#339)
    said_by = [(t, fc.claims.words) for fc in facts for t in fc.claims.counts]
    counted_zero = {
        _stem(c.noun)
        for c in p.counts
        if c.total is not None
        and (c.how == "none" or (c.how == "part" and c.k == 0))
        and any(t.total == c.total and _says(c, t.how, t.k, t.total) for t in _same(c, said_by))
    }
    for n in p.negs:  # a negation the sentence states needs a fact that states it
        if n.verdict:
            continue  # "does not meet": the verdict's polarity is checked above
        if n.kind == "zero" and n.head in counted_zero:
            continue
        if n.kind == "zero" and n.head in ("fault", "warn") and own & {"ok", "info"}:
            continue  # "no fault" of a finding the cited fact judges ok
        if n.kind == "zero":
            # a zero needs a fact's zero; "none of the baselines meets" a "does not meet" in
            # each cited fact (#332)
            said = any(_says_too(n, f) for f in fnegs if f.kind not in ("verbal", "np")) or all(
                any(_says_too(n, f) for f in fc.claims.negs) for fc in facts
            )
        else:
            said = any(
                _says_too(n, f, None if f.subject in fc.subjects else names)
                for fc in facts
                for f in fc.claims.negs
            )
        if not said:
            if n.kind == "frame":
                why.append(f"{n.raw!r}: it denies the rest of the sentence; no cited fact does")
            elif n.kind in _ZEROS:
                why.append(
                    f"{n.raw!r}: no cited fact gives this zero, of this, in its own words{several}"
                )
            else:
                why.append(f"{n.raw!r}: no cited fact says so")
    affirmed = set().union(*(fc.claims.affirmed for fc in facts))
    flagged = set()
    for i, fc in enumerate(facts):  # what a cited fact negates, the sentence must not state
        # (a sentence citing one fact for its first clause and another for its second: only
        # the words of the clauses that cite this fact, #338)
        stated = bound[i] if bound is not None and bound[i] is not None else p.affirmed
        for f in fc.claims.negs:
            if f.verdict or f.kind == "zero" or f.head in affirmed or f.head in flagged:
                continue
            need = set(f.words)
            if f.kind == "share":  # "CHW-leak 0% of the samples": the leak, not the samples
                need.discard(f.subject)
            elif f.named and f.subject not in fc.subjects and f.subject not in p.ids:
                continue  # "measured_fan_heat_f not set" is about that setting only
            if f.head in stated and need <= stated:
                flagged.add(f.head)
                why.append(f"{f.raw!r}: a cited fact says so, and the sentence states the opposite")
    return why


def _count_problems(p: _Parsed, facts: list) -> list:
    """What a sentence says of a counted thing as a whole, none or a part, against what its
    cited facts say of the same thing (#336): the count it gives (all of them, none, k) must be
    the facts' count of the things with that label ("The 11 points are trusted" against "9
    trusted, 2 suspect"; "No rule passed" against "19 ok") or with that statement ("The 8
    flagged hours fall in the window" against "None of the 8 flagged hours falls in the
    window"); and one thing is not two labels of one closed family ("Both suspect points are
    trusted")."""
    why: list = []
    parts = [x for fc in facts for x in fc.parts]
    said_by = [(t, fc.claims.words) for fc in facts for t in fc.claims.counts]
    for c in p.counts:
        if c.how in ("def", "count") and not c.subject:
            # "the rule flagged 8 hours": the figure is checked as one; and against a cited
            # fact's count with that verb ("the rule flagged the 37 samples", #338)
            if (
                c.back
                and (same := _same(c, said_by))
                and not any(_says(c, t.how, t.k, t.total) for t in same)
            ):
                why.append(f"{c.raw!r}: a cited fact gives another count of them: {same[0].raw!r}")
            continue
        said = c.raw
        labels: dict = {}
        for fam, label in c.modfam | c.predfam:
            labels.setdefault(fam, set()).add(label)
        for fam in ("trust", "verdict", "side"):
            a = {lab for f, lab in c.modfam if f == fam}
            b = {lab for f, lab in c.predfam if f == fam}
            if a and b and not a & b and c.how != "none":
                why.append(
                    f"{said!r}: they are {', '.join(sorted(a))}; they are not "
                    f"{', '.join(sorted(b))} as well (one {_FAMILY_NAME[fam]} each)"
                )
        for fam in ("trust", "verdict"):
            for label in sorted(labels.get(fam, ())):
                rel = [
                    x
                    for x in parts
                    if x[1] == fam and x[2] == label and (x[0] == c.noun or fam == "verdict")
                ]
                if c.how == "rest":  # "21 are trusted and the rest are untrusted" (#339)
                    rel = [x for x in rel if x[0] == c.noun and x[4] is not None]
                    if not rel:
                        why.append(f"{said!r}: no cited fact gives the total to read it by")
                    elif not any(x[3] == x[4] - c.k for x in rel):
                        x = rel[0]
                        why.append(
                            f"{said!r}: a cited fact counts {x[3]:f} of {x[4]:f} as {label}, "
                            f"not the {x[4] - c.k:f} after {c.k:f}"
                        )
                elif c.how == "none" and not (
                    label in {lab for f, lab in c.modfam if f == fam}
                    and label not in {lab for f, lab in c.predfam if f == fam}
                    and any(
                        w[:1] != "#" and w not in _FAMILY_STEMS
                        for w in c.pred - _GENERIC - _NOUN_STEMS
                    )
                ):
                    # (not "No suspect point is below the floor": what it says of them is
                    # more than their label, and its zero is read as one, #339)
                    bad = [x for x in rel if x[3] > 0]
                    if bad:
                        what = "" if bad[0][0] == label else f" {bad[0][0]}"
                        why.append(f"{said!r}: a cited fact counts {bad[0][3]:f} {label}{what}")
                elif (c.how.startswith("frac") or c.how == "pct") and len(labels[fam]) == 1:
                    rel = [x for x in rel if x[0] == c.noun]  # "at least half of the 11 points"
                    if rel and not any(_says(c, "part", x[3], x[4] or c.total) for x in rel):
                        x = rel[0]
                        of = f" of {x[4]:f}" if x[4] is not None else ""
                        why.append(f"{said!r}: a cited fact counts {x[3]:f}{of} as {label}")
                elif c.how in ("all", "def") and len(labels[fam]) == 1:
                    rel = [x for x in rel if x[0] == c.noun]
                    if rel and not any(
                        x[3] == c.k if c.k is not None else x[3] == x[4] for x in rel
                    ):
                        x = rel[0]
                        of = f" of {x[4]:f}" if x[4] is not None else ""
                        why.append(f"{said!r}: a cited fact counts {x[3]:f}{of} as {label}")
        if c.how == "rest":
            continue
        why += _verdict_set(c, parts, facts)
        same = _same(c, said_by)
        if not same:
            continue
        if not any(_says(c, t.how, t.k, t.total) for t in same):
            why.append(f"{said!r}: a cited fact gives another count of them: {same[0].raw!r}")
    return why


def _same(c: _Count, said_by: list) -> list:
    """The cited facts' counts (``said_by``: ``(count, its fact's words)``) of the things
    ``c`` counts with the same statement: its words are the fact's, its first verb too or
    another one ("lie in the reference window" against "falls in the window the reference ...
    covers"); "fall outside the window" against "fall in the window" counts the rest of them
    (#336); or its own verb, of the same things, the rest in the fact's words ("flagged as a
    heating leak" against "heating leak: the rule flagged ...", #338)."""
    if not c.lead:
        return []
    words = c.pred - _GENERIC - _NOUN_STEMS
    other = bool(words & _ELSEWHERE)
    words -= _ELSEWHERE
    same = [
        t
        for t, fwords in said_by
        if t.noun == c.noun
        and (
            (
                words <= t.pred | t.mods | {c.lead}
                and (c.lead in t.pred | {t.lead} or len(words - {c.lead}) >= 2)
            )
            or (c.lead == t.lead and c.mods <= t.mods | t.pred and words <= fwords)
        )
        and not (other and (t.pred & _ELSEWHERE or t.total is None))
    ]
    if other:
        same = [
            replace(t, k=(100 if t.how == "pct" else t.total) - t.k)
            for t in same
            if t.k is not None
        ]
    return same


def _says(c: _Count, how: str, k, total) -> bool:
    """Whether a sentence's count ``c`` agrees with a cited fact's (``how``, ``k`` of
    ``total``; "pct": ``k`` percent of ``total``) of the same things (#336, #338): "some" is
    more than none, "most" more than half, "half" a share of the total ("at least half of the
    11", "fewer than half of the 106"), a percentage the fact's share, and a count the fact's."""
    if k is None:
        return False
    ck: Any = c.k
    want: Any
    if how == "pct":  # the fact's share as a count, when it has a total
        share = k
        k = k * total / 100 if total else None
    else:
        share = 100 * k / total if total else None
    if c.how == "pct":
        if share is None or ck is None:
            return False
        places = max(0, -ck.as_tuple().exponent)
        return round(share, places) == ck or (how == "pct" and share == ck)
    if c.how.startswith("frac"):
        if share is None:
            return False
        f, op = share / 100, c.how[4:]
        return {
            ">=": f >= ck,
            ">": f > ck,
            "<": f < ck,
            "<=": f <= ck,
        }.get(op, f == ck)
    if k is None:  # a share of a total not given: only "none" and "all" are read
        return (c.how == "none" and share == 0) or (c.how in ("all", "def") and share == 100)
    if c.how == "some":  # "some of the 8", "several"
        return k > 0
    if c.how == "most":
        return total is not None and 2 * k > total
    if c.how == "none":
        want = Decimal(0)
    elif c.how in ("all", "def"):
        want = ck if ck is not None else total
    else:
        want = ck
    if want is None:
        return False
    if how == "pct":  # a rounded share: within its rounding
        return abs(want - k) <= (total or 0) / 200
    return want == k


def _verdict_set(c: _Count, parts: list, facts: list) -> list:
    """A verdict said of a whole set ("The 5 rules passed", "The 7 equipment are healthy", "the
    rules all returned a fault", "5 rules passed" where a cited fact runs 5 rules) against the
    cited facts' verdict counts (#338): every result must have that verdict, so a count of
    another verdict above 0 (or none of this one) fails it. A figure in what it says ("returned
    14 faults") is checked as a figure instead."""
    labels = {lab for fam, lab in c.predfam if fam == "verdict"} | c.listed
    if not labels or c.noun in _VERDICT_UNITS or any(w.startswith("#") for w in c.pred):
        return []
    if c.k == 0 or c.how == "none":
        return []  # "0 rules returned a warn": a zero, read as one
    whole = c.how in ("all", "def") or (
        c.how == "count"
        and c.k is not None
        and any(
            f.unit == c.noun and f.value == c.k and f.role == "result"
            for fc in facts
            for f in fc.claims.qty
        )
    )
    counts = [x for x in parts if x[1] == "verdict"]
    if not whole or not counts:
        return []
    others = sorted({(x[2], x[3]) for x in counts if x[2] not in labels and x[3] > 0})
    said = ", ".join(sorted(labels))
    if others:
        given = ", ".join(f"{v:f} {lab}" for lab, v in others)
        return [f"{c.raw!r}: a cited fact counts {given}: not every result is {said}"]
    if all(x[3] == 0 for x in counts if x[2] in labels):
        return [f"{c.raw!r}: a cited fact counts no {said}"]
    return []


def _bound_words(sentence: str, own: list, vocab) -> list | None:
    """For a sentence that cites one fact for a clause and another for the next ("A [F1], and
    B [F2]."), the words each cited fact's clauses state, in ``own``'s order (None: the whole
    sentence's, as for a sentence with one citation at its end) (#338). A clause with no
    citation of its own goes with the citation after it."""
    pieces, cut = [], 0
    for m in _CITES.finditer(sentence):
        if re.search(r"[A-Za-z]", _CITES.sub(" ", sentence[m.end() :])):
            pieces.append((sentence[cut : m.end()], extract_cites(m.group(0))))
            cut = m.end()
    if not pieces:
        return None
    pieces.append((sentence[cut:], extract_cites(sentence[cut:])))
    out = []
    for fid in own:
        words = [_parse(t, vocab).affirmed for t, ids in pieces if fid in ids]
        out.append(set().union(*words) if words else None)
    return out


@dataclass
class _Judged:
    """One judged sentence: its span of the text, the ids it cites and the first check it fails
    (``reason`` empty: supported) with what did not match (``why``)."""

    start: int
    end: int
    text: str
    cited: list  # known ids it cites (visible citations only)
    unknown: list  # ids it cites that are not in the context
    reason: str  # "" | "uncited-sentence" | "uncited-number" | "unsupported-claim"
    why: list


def _judge(
    text: str,
    context,
    *,
    per_sentence: bool = False,
    notices=(),
    aliases=None,
    terms=(),
    rules=(),
) -> list:
    """Every judged sentence of ``text`` against ``context``'s facts, as :class:`_Judged`, in
    order (headings and known notices are not judged; see the module doc). ``aliases`` maps a
    facility's registry name (or id) to its id, ``terms`` are rule, tool and field names (never
    an equipment), ``rules`` the rule names among them (#328)."""
    text = text or ""
    all_ids = set(context.ids())
    vocab = _Vocab(_known_names(context), aliases, terms, rules)
    fact_vocab = _Vocab(_known_names(context), None, terms, rules)
    cache: dict = {}

    def claims(ids):
        for fid in ids:
            if fid not in cache:
                cache[fid] = _FactClaims(context.by_id(fid), fact_vocab)
        return [cache[fid] for fid in ids]

    text_cited = [c for c in extract_cites(_visible(text)) if c in all_ids]
    # a fact's own text, cited with its id, is the server's words (#328 follow-up)
    quotes = {}
    for fid in set(extract_cites(text)) & all_ids:  # only the facts the text cites
        rx = _quoted_rx(getattr(context.by_id(fid), "text", ""))
        if rx is not None:
            quotes[fid] = rx
    out = []
    for seg in _segments(text, notices, quotes):
        if seg.kind not in ("sentence", "quote"):
            continue
        sent = text[seg.start : seg.end]
        vis = _visible(sent)
        cites = extract_cites(vis)
        own = [c for c in cites if c in all_ids]
        unknown = [c for c in cites if c not in all_ids]
        reason, why = "", []
        if seg.kind == "quote":
            pass  # supported: it says what the fact it cites says, word for word
        elif per_sentence and not own:
            reason, why = "uncited-sentence", ["it cites no known fact"]
        else:
            p = _parse(vis, vocab)
            why = _number_problems(p, claims(own if per_sentence else text_cited))
            if why:
                reason = "uncited-number"
            elif own:  # without per_sentence, a sentence that cites nothing makes no claim
                why = _claim_problems(p, claims(own), vocab, vis, _bound_words(vis, own, vocab))
                reason = "unsupported-claim" if why else ""
        out.append(_Judged(seg.start, seg.end, sent, own, unknown, reason, why))
    return out


def _states_verdict_or_caveat(sentence: str) -> bool:
    """Whether a sentence gives a verdict, a negation, a direction or a caveat (a qualifier such
    as "only", "conditional", "not verified"): what an answer loses when it is removed (#328)."""
    p = _parse(_visible(sentence), ())
    caveat = re.search(
        r"\b(only|conditional|caveat|uncertain|uncertainty|information|however|although|unless|"
        r"until|but|may|might|possibly|likely|risk|at risk|first)\b",
        sentence,
        re.I,
    )
    return bool(p.verdicts or p.negs or caveat)


def _rebuild(text: str, drop, unknown=()) -> str:
    """``text`` without the sentences of ``drop`` (:class:`_Judged`), each id of ``unknown`` taken
    out of its citations. Every other line stays as it was (headings, blank lines, list
    markers: a removed sentence that opened a list item leaves its marker to the next one); a
    line whose sentences all went is removed, with a blank line it would double."""
    text = text or ""
    gone = sorted((j.start, j.end) for j in drop)
    unknown = set(unknown)
    out: list = []
    pos, g = 0, 0
    removed_last = False
    for line in text.split("\n"):
        ls, le = pos, pos + len(line)
        pos = le + 1
        cuts = []
        while g < len(gone) and gone[g][0] < le + 1:
            a, b = gone[g]
            m = _MARKER.match(text, a)
            if m is not None and not text[ls:a].strip():  # keep "1." for what follows
                a = m.end()
                while a < b and text[a] in " \t":
                    a += 1
            cuts.append((a, b))
            g += 1
        if not cuts:
            if not line.strip() and removed_last and out and not out[-1].strip():
                continue  # the blank line after a removed one would double
            out.append(_strip_ids(line, unknown) if unknown else line)
            removed_last = False
            continue
        kept, at = [], ls
        for a, b in cuts:
            kept.append(text[at:a])
            at = b
            while at < le and text[at] in " \t":
                at += 1
        kept.append(text[at:le])
        new = "".join(kept).rstrip()
        if not new.strip() or _MARKER.fullmatch(new.strip()):
            removed_last = True
            if out and not out[-1].strip() and len(out) == 1:
                out.pop()
            continue
        out.append(_strip_ids(new, unknown) if unknown else new)
        removed_last = False
    while out and not out[0].strip() and text[:1] not in ("\n", " ", "\t", "\r"):
        out.pop(0)
    tail = text[len(text.rstrip("\n")) :]  # the draft's own trailing line breaks, no more
    return "\n".join(out).rstrip("\n") + tail


def _strip_ids(text: str, unknown: set) -> str:
    """``text`` with each id of ``unknown`` taken out of its citation; an emptied citation goes
    with the space before it."""

    def sub(m):
        ids = [c for c in _ID_SEP.split(m.group(1).strip()) if c not in unknown]
        return f"[{', '.join(ids)}]" if ids else "\x00"

    out = _CITES.sub(sub, text)
    if "\x00" in out:
        out = re.sub(r"[ \t]*\x00", "", out)
        out = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", out)
    return out


def check(
    text: str,
    context,
    *,
    strict: bool = True,
    source: str = "llm",
    require_cite_each_sentence: bool = False,
    notices=(),
) -> Grounded:
    """Verify (and in ``strict`` mode repair) ``text`` against ``context``'s fact whitelist.

    ``require_cite_each_sentence`` (0.107, #194): a sentence without a resolving ``[id]`` is
    flagged ``uncited-sentence`` (and dropped in ``strict`` mode), and a sentence's claims must
    trace to the facts it cites itself rather than to any fact cited anywhere in ``text``.
    ``notices`` (0.112): banner texts whose ``NOTICE:`` line is kept and not judged.
    """
    text = text or ""
    all_ids = set(context.ids())
    unknown = [c for c in extract_cites(_visible(text)) if c not in all_ids]
    flagged: list = [{"reason": "unknown-citation", "text": c} for c in unknown]
    bad = [
        j
        for j in _judge(text, context, per_sentence=require_cite_each_sentence, notices=notices)
        if j.reason
    ]
    flagged += [{"reason": j.reason, "text": j.text, "why": "; ".join(j.why)} for j in bad]
    out_text = _rebuild(text, bad, unknown).strip() if strict else text
    cited = [c for c in extract_cites(_visible(out_text)) if c in all_ids]
    facts = [context.by_id(c) for c in cited]
    return Grounded(
        text=out_text,
        cited=cited,
        facts=[f for f in facts if f is not None],
        grounded=not unknown and not bad,
        flagged=flagged,
        source=source,
    )
