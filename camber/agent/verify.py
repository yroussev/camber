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
  cooling valve leaked on 86%" against ``HW-leak 86% / CHW-leak 0%``) fails (#332). A date,
  time or year must be one a cited fact carries (in its text or in a date field); a list
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
    the finding ("the leaking_valve fault"): it supports no "is still a fault" (#332);
  - **directions**: a direction word (rose, fell, increased, saved, avoided, wastes, ...) must
    be one a cited fact uses, and "high" / "low" or "above" / "below" must not be the opposite
    of the cited facts';
  - **negations, both ways**: a negation ("did not leak", "never moves", "without", "no
    <thing>") must be one a cited fact makes about the same thing (a fact that negates something
    else does not count), and a sentence must not state what a cited fact negates ("the valve
    leaks" against "no valve leak", "moves" against "never moves"); a closing "not" is never
    supported. A zero ("0 <things>", "no <things>", "none of them") is read with its own clause
    and the fact's with its own list item: the fact's zero must count the same thing, with its
    words and first verb in the sentence, and the sentence must not deny what its counted
    things are (#332);
  - **quantifiers and ranks**: "all" / "every" needs a fact that says all, every or 100%, "most"
    one that says most or a share above half; a whole count ("all 11", "8 of the 8") a fact
    that says all of that many or gives the count again; "Issue 3" must be a rank a cited fact
    gives, when a cited fact gives ranks; a rank said as one ("the second issue", "#1") or a
    rank word ("worst", "top") one a cited fact gives (#332).

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
    **_words("fault faults faulty fail fails failed failing failure failures", "fault"),
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
    **_words("persists persist persisted persisting", "persists"),
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
    "suspect": "trusted",
    "significant": "insignificant",
    "insignificant": "significant",
    "trusted": "untrusted",
    "untrusted": "trusted",
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
}
_OPPOSITE = {
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
        "again further once own too yet still even above below beyond within across around "
        "near s"
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
_SYNONYM = {"chw": "cool", "hw": "heat", "chilled": "cool", "shut": "clos", "closed": "clos"}
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


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"|“([^”]*)”')
_DATE = re.compile(r"(?<![\d\-])(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?(?!\d)")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_PATTERN_ID = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9_]*(?:[\-.][A-Za-z0-9_]+)*")
_TOKEN = re.compile(
    r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"|(?P<word>[A-Za-z]+(?:['’][A-Za-z]+)?(?:-[A-Za-z]+)*)"
    r"|(?P<sym>\S)"
)
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
        return " QQDATE "

    def time(m):
        p.times.add((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
        return " QQTIME "

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
    toks = [(m.lastgroup, m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(s)]
    for i, (kind, raw, _a, _b) in enumerate(toks):
        if kind == "num":
            value = _decimal(raw)
        elif kind == "word":
            w = raw.lower()
            value = _word_number(w)
            if w == "one" and not _one_counts(toks, i):
                value = None  # "the one", "the suspect one", "to pick one": a pronoun
            raw = ""
        else:
            continue
        if value is not None:
            q = _quantity(toks, i, value, raw)
            if q is not None:
                p.qty.append(q)
    if fact:
        _fill(p.qty, toks)
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
    s = " ".join(out)
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
        total = (
            x >= 1
            and words[x] == "of"
            and (
                toks[x - 1][0] == "num"
                or words[x - 1] in _OF_TOTAL
                or bool(_word_number(words[x - 1]))
            )
        )
        seg[at] = (stems(a, at, -2), stems(at + 1, b, 2), total, groups[at])
    p.qty[:] = [
        replace(q, pre=seg[q.at][0], post=seg[q.at][1], total=seg[q.at][2], grp=seg[q.at][3])
        if q.at in seg
        else q
        for q in p.qty
    ]


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

    def clause_words(i) -> set:
        c = clause[i]
        return {s for x in range(n) if clause[x] == c for s in cont[x]}

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
    claimed = {clause[x] for x in range(n) if words[x] in _CLAIM_WORDS and clause[x] >= 0}
    p.qty[:] = [
        replace(q, claim=True) if 0 <= q.at < n and clause[q.at] in claimed else q for q in p.qty
    ]
    _figure_words(p, toks, cont, words, clause, fact)
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
            p.verdicts.add(c)
            p.stated.add(c)
            p.vraw.setdefault(c, f"{toks[x][1] + ' ' if x >= 0 else ''}{toks[k][1]} {nxt}")
        d = _DIRECTION.get(w)
        if d is not None and w.startswith(("fall", "fell")) and nxt in _PLACE:
            d = None  # "the spans fall on 2025-05-14", "fall outside the window"
        if d is not None and not hypothetical(k):
            p.dirs.add(d)
        if w in _CLAIM_WORDS:
            p.claim = True
        pol = _POLAR.get(w)
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
            elif w == "every" or nxt in _ALL_BEFORE:
                p.quant.add("all")
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
    if fact:  # a count given again, and the words of its later mentions
        at: dict = {}
        for q in sorted(q_at.values(), key=lambda q: q.at):
            if q.value and q.role == "result":
                at.setdefault(q.value, []).append(q)
        for v, qs in at.items():
            if len(qs) > 1:
                p.whole_words[v] = frozenset().union(*(q.pre | q.post for q in qs[1:]))


_CAUSAL_PAIRS = frozenset(_CAUSAL)


def _superlative(toks, words, cont, k):
    """``(word, what it is said of)`` for a rank word at ``k`` ("the worst finding", "worst: HW
    (heating) coil", "the most severe issue"), or None (#332)."""
    w = words[k]
    if w == "most" and k + 1 < len(toks) and words[k + 1] in _SEVERE:
        w, j = "most " + words[k + 1], k + 2
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
    base = ""
    if unit == "%" and k < len(toks) and toks[k][1].lower() == "of":
        base = _scan_noun(toks, k + 1, of=True)
    threshold = _cue_before(toks, j) or _cue_after(toks, k) or _cue_subject(toks, j)
    role = "threshold" if threshold else "result"
    if j >= 1 and toks[j][1] == "=" and toks[j - 1][1].startswith(_NAME):
        role = "setting"  # "fault_pct = 20.0": a parameter's value, a threshold or a result
    year = raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100 and not unit and not sign
    return _Qty(value.normalize(), sign, unit, base, role, year, i)


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
    count ("46% of the 64 free-cooling hours" is of hours)."""
    seen = 0
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
        for piece in _split_sentences(text):
            q = _parse(piece, names, fact=True)
            p.qty += _labelled(q.qty) + _paged(piece)
            p.negs += q.negs
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


def _match(q: _Qty, facts: list, claim: bool = False, bound: bool = True) -> bool:
    for fc in facts:
        if fc.claimed and not claim:
            continue  # a claim's own figure never supports a measured statement
        for f in fc.claims.qty:
            if f.value != q.value or (f.role != q.role and "setting" not in (f.role, q.role)):
                continue
            if (q.sign == "-") != (f.sign == "-") or (q.sign == "±" and f.sign != "±"):
                continue
            if (q.unit and f.unit != q.unit) or (q.base and f.base != q.base):
                continue
            if bound and not _bound(q, f):
                continue
            return True
        if q.year and any(d[0] == int(q.value) for d in fc.claims.dates):
            return True
    return False


def _number_problems(p: _Parsed, facts: list) -> list:
    why = []
    for q in p.qty:
        if _match(q, facts, q.claim):
            continue
        if _match(q, facts, q.claim, bound=False):
            other = sorted(
                {w for fc in facts for f in fc.claims.qty for w in f.sib & (q.pre | q.post)}
            )
            why.append(
                f"{q.show()}: a cited fact gives this figure for another of the things it lists "
                f"(not {', '.join(map(repr, other))})"
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


def _says_too(n: _Neg, f: _Neg) -> bool:
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
        if not req and not f.ctx:
            req = set(f.words) - {f.head}
        if not req <= n.clause:
            return False
        if not (n.head == f.head or n.head in f.words or f.head in n.clause):
            return False
        ctx = f.ctx or f.clause or f.words
        if n.lead and n.lead not in _GENERIC | _NOUN_STEMS | f.words | ctx:
            return False
        return not (n.pred & f.mods)
    own = f.words - ({f.subject} if f.subject and not f.named else set())
    if not own <= n.clause:
        return False
    return n.head in f.words or f.head in n.after


def _claim_problems(p: _Parsed, facts: list, vocab: _Vocab = _NO_VOCAB, text: str = "") -> list:
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
    verdicts = set().union(*(fc.claims.verdicts for fc in facts))
    final = set().union(*(fc.claims.verdicts - fc.superseded for fc in facts))
    for v in sorted(p.verdicts - final):
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
    why += [f"{words[d]}: no cited fact states it" for d in sorted(p.dirs - dirs)]
    polar = set().union(*(fc.claims.polar for fc in facts))
    why += [
        f"{c!r}: the cited facts say {_OPPOSITE[c]!r}"
        for c in sorted(p.polar)
        if c not in polar and _OPPOSITE[c] in polar
    ]
    quant = set().union(*(fc.claims.quant for fc in facts))
    several = " (a claim about all of several facts is not read: say it of each)" * (len(facts) > 1)
    for qc in sorted(p.quant - quant):
        what = "all or every" if qc == "all" else "most"
        why.append(f"'{what}': no cited fact says {what}{several}")
    wholes = set().union(*(fc.claims.wholes for fc in facts))
    for w in sorted(p.wholes - wholes):
        said = p.whole_words.get(w, frozenset())
        if any(said <= fc.claims.whole_words.get(w, {"-"}) for fc in facts):
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
        if 1 in ranks or any(w == word and own <= o and (own or not o) for w, o, _s in fsups):
            continue
        why.append(f"{said!r}: no cited fact ranks it so")
    fnegs = [f for fc in facts for f in fc.claims.negs]
    own = set().union(*(fc.own for fc in facts))
    for n in p.negs:  # a negation the sentence states needs a fact that states it
        if n.verdict:
            continue  # "does not meet": the verdict's polarity is checked above
        if n.kind == "zero" and n.head in ("fault", "warn") and own & {"ok", "info"}:
            continue  # "no fault" of a finding the cited fact judges ok
        if n.kind == "zero":
            # a zero needs a fact's zero; "none of the baselines meets" a "does not meet" in
            # each cited fact (#332)
            said = any(_says_too(n, f) for f in fnegs if f.kind not in ("verbal", "np")) or all(
                any(_says_too(n, f) for f in fc.claims.negs) for fc in facts
            )
        else:
            said = any(_says_too(n, f) for f in fnegs)
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
    for fc in facts:  # what a cited fact negates, the sentence must not state
        for f in fc.claims.negs:
            if f.verdict or f.kind == "zero" or f.head in affirmed or f.head in flagged:
                continue
            need = set(f.words)
            if f.kind == "share":  # "CHW-leak 0% of the samples": the leak, not the samples
                need.discard(f.subject)
            elif f.named and f.subject not in fc.subjects and f.subject not in p.ids:
                continue  # "measured_fan_heat_f not set" is about that setting only
            if f.head in p.affirmed and need <= p.affirmed:
                flagged.add(f.head)
                why.append(f"{f.raw!r}: a cited fact says so, and the sentence states the opposite")
    return why


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
                why = _claim_problems(p, claims(own), vocab, vis)
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
