"""Citation extraction + grounding verification for generated answers.

An answer is **grounded** when (a) every ``[id]`` it cites resolves to a real fact in the
:class:`~camber.agent.context.Context`, and (b) every claim it makes can be matched to a cited fact.
Requiring claim-*traceability* (rather than a citation token in the very same sentence) is what
lets a multi-sentence fact -- e.g. a recommendation whose ``[id]`` leads a paragraph of specifics --
stay grounded, while a fabricated figure the model invented is caught.

What a sentence must satisfy (0.112, #294) -- precise rather than clever, and failing closed:

- **figures** (``uncited-number``): each number must be one a cited fact states, with the same
  sign (``-45%`` is not ``45%``), the same unit when the sentence gives one (``%``, ``°F``,
  ``kWh``, hours, percentage points, ...), the same counted thing when the sentence names one
  (hours, samples, points, rules, faults, ...; for a percentage, what it is "of"), and the same
  role: a figure the fact gives as a threshold or limit (``OAT < 60°F``, ``above 20% min``) is not
  a result. A fact's figures are those its text states (outside quoted names: a number in a name
  from the data is not a figure) and its numeric ``value`` (with its ``unit``, when it has one),
  never the other numbers of its data. A date, time or year must be one a cited fact carries (in
  its text or in a date field); a list marker, a rank (``Ranked cause 1``) and an ordinal are
  labels, not figures. A number written in words (``forty-six``, ``half``) is a figure too. A
  derived number (a difference, sum or ratio of figures) is not supported unless a fact states
  it;
- **claims** (``unsupported-claim``): an equipment, meter or facility the sentence names (an id
  with a digit or an underscore, a quoted name, or the ``equip`` / ``facility_id`` / ``meter`` of
  any fact in the context) must be named by a cited fact; a verdict word (fault, warn, ok, pass,
  healthy, accurate, meets, significant, suspect, trusted, ...) must be one a cited fact states,
  with the same polarity ("meets G14" against "does not meet G14" fails); a direction word (rose,
  fell, increased, saved, avoided, wastes, ...) must be one a cited fact uses; a negation ("did
  not", "never") needs a cited fact that negates too, and "no <thing>" needs a fact with "no
  <thing>" or "0 <thing>".

Citations count only in the visible text: one inside an HTML comment or tag does not. ``[A, B]``
and ``[A; B]`` cite both ids (0.112).

:func:`check` enforces this. In ``strict`` mode it *repairs* the text -- dropping sentences that
fail and stripping unknown ``[id]`` tokens -- so a partly-hallucinated LLM answer degrades to its
grounded subset instead of misleading. The repaired text keeps the draft's lines: headings, blank
lines and list structure stay where they were, and only the failing sentences go (0.112, #322).
In non-strict mode the same problems are recorded in ``flagged`` but the text is left intact.

``require_cite_each_sentence=True`` (0.107, #194) is the stricter mode the RCx report's prose slots
use: every sentence must carry at least one ``[id]`` that resolves, and each claim it makes must
trace to a fact *that sentence* cites. A sentence that fails is flagged (``uncited-sentence``,
``uncited-number`` or ``unsupported-claim``) and, in ``strict`` mode, dropped. Without it, a
sentence's figures may trace to any fact the text cites, and only a sentence that cites a fact
itself has its names, verdicts, directions and negations checked (against the facts it cites).

A heading line (``# Title``, ``Findings:``) that states nothing -- no citation, figure, date,
negation or direction word -- is not a sentence: it is kept and not judged. A ``NOTICE:`` line
repeating one of the ``notices`` passed in (a server's own banners) is kept and not judged either.

A fact id may be context-qualified, ``[F1@ctx]`` (0.107, #194): the part after ``@`` names where the
fact lives (the RCx report uses ``summary``, ``appA``, ``appB`` and an issue's key), so ids from
several contexts can share one whitelist. Plain ``[F1]`` ids work as before.

Pure: regex + the fact whitelist, no LLM, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
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
#: split into sentences on end-punctuation *followed by whitespace* -> never inside "5.16" or
#: "Ch.5" -- and at every line break, so a line without a full stop (a heading, a notice) is a
#: sentence of its own (#314)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*\n\s*")
_SENT_END = re.compile(r"(?<=[.!?])[ \t]+")
#: a list marker ("1.", "2)", "(3)", "-", "*", "•", ">") that starts a sentence: it is part of the
#: sentence that follows it, and its number is not a figure to trace (#314)
_MARKER = re.compile(r"(?:\(?\d{1,3}[.)]|[-*•>])(?=\s|\Z)")
#: a heading line: markdown ``#``, a short line ending in a colon, or a bold-only line
_HEADING = re.compile(r"\s*(?:#{1,6}\s+\S.*|[^.!?]{1,80}:|\*\*[^*]+\*\*:?)\s*")
_NOTICE = re.compile(r"\s*(?:[-*•>]\s+)?NOTICE:\s*(.*?)\s*")
#: what a reader does not see: HTML comments (an unterminated one: to the end), tags,
#: zero-width marks, and a markdown link's target
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_INVISIBLE = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
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
    kind: str  # "sentence" (judged) | "heading" | "notice" (kept, not judged)


def _segments(text: str, notices=()) -> list:
    """The sentences, headings and known notices of ``text``, as spans of it, in order. Lines
    are split at sentence ends; an HTML comment is never split."""
    text = text or ""
    masked = _COMMENT.sub(lambda m: " " * len(m.group(0)), text)
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
        m = _NOTICE.fullmatch(line)
        if m is not None and " ".join(m.group(1).split()) in known:
            out.append(_Seg(s0, e0, "notice"))
            continue
        if _HEADING.fullmatch(line) and _is_heading(text[s0:e0]):
            out.append(_Seg(s0, e0, "heading"))
            continue
        spans, cut = [], s0
        for sm in _SENT_END.finditer(masked, s0, e0):
            spans.append((cut, sm.start()))
            cut = sm.end()
        spans.append((cut, e0))
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
        out.extend(_Seg(a, b, "sentence") for a, b in merged)
    return out


def _is_heading(line: str) -> bool:
    """A heading line that states nothing: no citation, figure, date, negation or direction."""
    vis = _visible(line)
    if _CITES.search(vis):
        return False
    p = _parse(vis.strip().lstrip("#").strip(), ())
    return not (p.qty or p.dates or p.times or p.negs or p.dirs)


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


_NEG = frozenset("not no never none nothing neither nor without cannot nobody".split())
#: a verdict word -> its class
_VERDICT = {
    **_words("fault faults faulty fail fails failed failing failure failures", "fault"),
    **_words("warn warns warning warnings", "warn"),
    **_words("suspect suspects", "suspect"),
    **_words(
        "ok pass passed healthy correctly properly normal normally accurate accurately fine",
        "ok",
    ),
    **_words("meet meets met compliant complies acceptable", "meets"),
    "info": "info",
    **_words("significant significantly", "significant"),
    "insignificant": "insignificant",
    "trusted": "trusted",
    "untrusted": "untrusted",
}
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
    **_words("down decrease decreased fall fell saving", "down"),
}
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
            "sample point rule equipment unit finding fault warn info ok span start issue meter "
            "facility building zone sensor check result sentence row dataset gap pair cycle "
            "alarm reading record fact trigger fit baseline entry event energy cost time "
            "value parameter role chart page"
        ).split()
    },
    "warning": "warn",
}
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
        "cutoff"
    ).split()
)
_CUE_THAN = frozenset("more less greater fewer higher lower".split())
_CUE_AFTER = frozenset(
    "min minimum max maximum limit threshold setpoint target floor ceiling cutoff".split()
)
_SKIP_BEFORE = frozenset("the a an its their configured high low set of is was at".split())
#: a cue noun after a comparison ("below target 54% of hours") or after its own figure ("the
#: 68°F high limit 43% of samples") names what the next figure is compared with: that figure is
#: the result, not the threshold
_CUE_NOUNS = frozenset(
    "limit limits threshold thresholds setpoint setpoints target targets floor ceiling".split()
)
_COMPARISONS = frozenset("above below under over beyond exceeds exceed exceeded exceeding".split())
#: words just before a figure that make it a label: "Ranked cause 1", "step 2"
_LABELS = frozenset(
    (
        "rank ranked cause step item priority option number part section appendix table "
        "figure fig chapter page phase stage tier round version"
    ).split()
)
_ORDINAL = frozenset({"st", "nd", "rd", "th"})
#: tokens shaped like equipment ids that are units or chemistry
_NOT_IDS = frozenset("co2 h2o pm2.5 pm10 no2 o3 so2 nh3 m2 m3 ft2 ft3 r410a r22 r134a".split())
_WORD_NUM: dict[str, Decimal] = {
    **{
        w: Decimal(i)
        for i, w in enumerate(
            (
                "zero _ two three four five six seven eight nine ten eleven twelve thirteen "
                "fourteen fifteen sixteen seventeen eighteen nineteen"
            ).split()
        )
        if w != "_"
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


_TENS = frozenset(Decimal(t) for t in range(20, 100, 10))


def _word_number(word: str):
    """A number written in words (``forty-six``, ``fifty``, ``half``) as a Decimal, or None.
    "one" alone is not read as a number: it is far more often a pronoun."""
    parts = word.split("-")
    if len(parts) == 1:
        return _WORD_NUM.get(word)
    tens = _WORD_NUM.get(parts[0])
    ones = Decimal(1) if parts[-1] == "one" else _WORD_NUM.get(parts[-1])
    if len(parts) == 2 and tens in _TENS and ones is not None and 1 <= ones <= 9:
        return tens + ones
    return None


def _negates(word: str) -> bool:
    return word in _NEG or word.endswith(("n't", "n’t"))


# --------------------------------------------------------------------------- parsing


@dataclass(frozen=True)
class _Qty:
    value: Decimal
    sign: str  # "" | "+" | "-" | "±"
    unit: str  # canonical unit or counted thing, "" when none
    base: str  # for a percentage: what it is "of", "" when none
    role: str  # "result" | "threshold"
    year: bool  # a bare 1900-2100 integer: may be a year

    def show(self) -> str:
        sign = "-" if self.sign == "-" else ("±" if self.sign == "±" else "")
        unit = f" {self.unit}" if self.unit else ""
        return f"{sign}{self.value:f}{unit}"


@dataclass
class _Parsed:
    qty: list = field(default_factory=list)
    dates: set = field(default_factory=set)  # (y, m, d, (h, m, s) or None)
    times: set = field(default_factory=set)  # (h, m, s)
    ids: set = field(default_factory=set)  # names, lower case
    verdicts: set = field(default_factory=set)  # verdict classes, negation applied
    dirs: set = field(default_factory=set)  # direction classes
    negs: set = field(default_factory=set)  # "neg", "none", or ("no", thing)
    nothing: set = field(default_factory=set)  # things it says there are none of ("no x", "0 x")


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"|“([^”]*)”')
_DATE = re.compile(r"(?<![\d\-])(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?(?!\d)")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_PATTERN_ID = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9_]*(?:[\-.][A-Za-z0-9_]+)*")
_TOKEN = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)"
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
    (re.compile("[\u2212\u2012]"), "-"),
    (re.compile("º"), "°"),
)


def _date(m) -> tuple:
    y, mo, d, hh, mm, ss = m.groups()
    return int(y), int(mo), int(d), ((int(hh), int(mm), int(ss or 0)) if hh else None)


def _parse(text: str, names) -> _Parsed:
    """The claims of one sentence (or of one sentence of a fact's text): see the module doc.
    ``names``: the context's subject names (lower case, longest first)."""
    p = _Parsed()
    s = _unlisted(_FACT_ID.sub(" ", _CITES.sub(" ", text)).strip())
    for rx, rep in _PRE:
        s = rx.sub(rep, s)

    def quoted(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        p.ids.add(" ".join(inner.replace('\\"', '"').split()).lower())
        return " NAME "

    def date(m):
        p.dates.add(_date(m))
        return " DATE "

    def time(m):
        p.times.add((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
        return " TIME "

    def ident(m):
        tok = m.group(0)
        if tok.lower() in _NOT_IDS or not ("_" in tok or any(c.isdigit() for c in tok)):
            return tok
        p.ids.add(tok.lower())
        return " NAME "

    s = _QUOTED.sub(quoted, s)
    s = _DATE.sub(date, s)
    s = _TIME.sub(time, s)
    low = s.lower()
    for n in names:
        if n in low:
            rx = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", re.I)
            if rx.search(s):
                p.ids.add(n)
                s = rx.sub(" NAME ", s)
                low = s.lower()
    s = _PATTERN_ID.sub(ident, s)
    toks = [(m.lastgroup, m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(s)]
    _claim_words(p, [t[1].lower() for t in toks if t[0] == "word"])
    for i, (kind, raw, _a, _b) in enumerate(toks):
        if kind == "num":
            value = _decimal(raw)
        elif kind == "word":
            value = _word_number(raw.lower())
            raw = ""
        else:
            continue
        if value is not None:
            q = _quantity(toks, i, value, raw)
            if q is not None:
                p.qty.append(q)
                if q.value == 0 and q.unit:
                    p.nothing.add(q.unit)
    return p


def _decimal(raw: str):
    try:
        return Decimal(raw.rstrip(",").replace(",", ""))
    except InvalidOperation:
        return None


def _claim_words(p: _Parsed, seq: list) -> None:
    """The verdicts (with their polarity), directions and negations of a sentence's words."""
    for k, w in enumerate(seq):
        if _negates(w):
            nxt = seq[k + 1] if k + 1 < len(seq) else ""
            if w == "no" and nxt:
                thing = _noun(nxt) or nxt
                p.negs.add(("no", thing))
                p.nothing.add(thing)
            elif w == "none":  # "none was skipped": a zero count of something
                p.negs.add("none")
            else:
                p.negs.add("neg")
        cls = _VERDICT.get(w)
        if cls is not None:
            if any(_negates(b) for b in seq[max(0, k - 3) : k]):
                cls = _FLIP.get(cls, f"not {cls}")
            p.verdicts.add(cls)
        d = _DIRECTION.get(w)
        if d is not None:
            p.dirs.add(d)


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
    if not unit and not sign and prev is not None:
        if prev[0] == "sym" and prev[1] == "#":
            return None
        if prev[0] == "word" and prev[1].lower() in _LABELS:
            return None
    if not unit:
        unit = _scan_noun(toks, k)
    base = ""
    if unit == "%" and k < len(toks) and toks[k][1].lower() == "of":
        base = _scan_noun(toks, k + 1, of=True)
    role = "threshold" if _cue_before(toks, j) or _cue_after(toks, k) else "result"
    year = raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100 and not unit and not sign
    return _Qty(value.normalize(), sign, unit, base, role, year)


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


def _has_figure(toks: list, j: int) -> bool:
    """Whether the cue noun at token ``j`` comes right after a figure of its own ("68°F high")."""
    j -= 1
    while j >= 0 and toks[j][0] == "word" and toks[j][1].lower() in ("high", "low"):
        j -= 1
    while j >= 0 and (
        (toks[j][0] == "sym" and toks[j][1] in "%\u00b0")
        or (toks[j][0] == "word" and toks[j][1].lower() in _UNITS)
    ):
        j -= 1
    return j >= 0 and toks[j][0] == "num"


def _cue_after(toks: list, k: int) -> bool:
    """A limit word just after a figure and its unit ("20% min", "65°F high limit")."""
    while k < len(toks) and toks[k][0] == "word" and toks[k][1].lower() in ("high", "low"):
        k += 1
    return k < len(toks) and toks[k][0] == "word" and toks[k][1].lower() in _CUE_AFTER


def _scan_noun(toks: list, k: int, of: bool = False) -> str:
    """The first counted thing (or unit word) in the 3 words from token ``k``, stopping at a
    figure, punctuation or a stop word; ``of``: what a percentage is of, in 5 words and past a
    count ("46% of the 64 free-cooling hours" is of hours)."""
    seen = 0
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
        if n:
            return n
        seen += 1
        k += 1
    return ""


# --------------------------------------------------------------------------- facts

#: structured fields of a fact that name its subject (an equipment, a facility, a meter)
_SUBJECT_KEYS = ("equip", "facility_id", "meter", "subject")
#: structured fields that are names: never read for dates
_NAME_KEYS = frozenset((*_SUBJECT_KEYS, "id", "rule", "role", "finding_id", "finding_ids", "kind"))


class _FactClaims:
    """What one fact exposes to the matcher: the claims of its text plus its structured fields
    (``value`` and ``unit``; ``value`` / ``verdict`` / ``severity`` as a verdict; ``direction``;
    every string field as a name it may carry; the dates of its non-name fields)."""

    def __init__(self, fact, names):
        text = str(getattr(fact, "text", "") or "")
        data = getattr(fact, "data", None)
        data = data if isinstance(data, dict) else {}
        p = _Parsed()
        for piece in _SENT_SPLIT.split(text):
            if piece.strip():
                q = _parse(piece, names)
                p.qty += q.qty
                for attr in ("dates", "times", "ids", "verdicts", "dirs", "negs", "nothing"):
                    getattr(p, attr).update(getattr(q, attr))
        value = data.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            unit = data.get("unit")
            u = (_UNITS.get(str(unit).lower()) or str(unit)) if unit else ""
            try:
                d = Decimal(str(value))
                p.qty.append(_Qty(abs(d).normalize(), "-" if d < 0 else "", u, "", "result", False))
            except InvalidOperation:
                pass
        for key in ("value", "verdict", "severity"):
            v = data.get(key)
            if isinstance(v, str) and v.lower() in _VALUE_VERDICT:
                p.verdicts.add(_VALUE_VERDICT[v.lower()])
        v = data.get("direction")
        if isinstance(v, str) and v.lower() in _VALUE_DIRECTION:
            p.dirs.add(_VALUE_DIRECTION[v.lower()])
        strings = [text.lower(), str(getattr(fact, "equip", "") or "").lower()]
        _strings(data, strings, p)
        self.claims = p
        self.hay = "\n".join(strings)

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


def _known_names(context) -> list:
    """The subject names of every fact in ``context``: lower case, longest first."""
    names = set()
    for f in getattr(context, "facts", None) or []:
        data = getattr(f, "data", None)
        vals = [getattr(f, "equip", "")]
        if isinstance(data, dict):
            vals += [data.get(k) for k in _SUBJECT_KEYS]
        for v in vals:
            if isinstance(v, str):
                n = " ".join(v.split()).lower()
                if len(n) >= 3 and any(c.isalpha() for c in n) and not n.startswith("<"):
                    names.add(n)
    return sorted(names, key=lambda n: (-len(n), n))


# --------------------------------------------------------------------------- judging


def _match(q: _Qty, facts: list) -> bool:
    for fc in facts:
        for f in fc.claims.qty:
            if f.value != q.value or f.role != q.role:
                continue
            if (q.sign == "-") != (f.sign == "-") or (q.sign == "±" and f.sign != "±"):
                continue
            if (q.unit and f.unit != q.unit) or (q.base and f.base != q.base):
                continue
            return True
        if q.year and any(d[0] == int(q.value) for d in fc.claims.dates):
            return True
    return False


def _number_problems(p: _Parsed, facts: list) -> list:
    why = []
    for q in p.qty:
        if _match(q, facts):
            continue
        if any(f.value == q.value for fc in facts for f in fc.claims.qty):
            why.append(
                f"{q.show()}: a cited fact has this number, but with another sign, unit, "
                "counted thing or role (a threshold is not a result)"
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


def _claim_problems(p: _Parsed, facts: list) -> list:
    why = [
        f"{n!r}: no cited fact names it"
        for n in sorted(p.ids)
        if not any(fc.names(n) for fc in facts)
    ]
    verdicts = set().union(*(fc.claims.verdicts for fc in facts))
    why += [f"verdict {v!r}: no cited fact states it" for v in sorted(p.verdicts - verdicts)]
    dirs = set().union(*(fc.claims.dirs for fc in facts))
    words = {"up": "an increase", "down": "a decrease or saving", "waste": "waste"}
    why += [f"{words[d]}: no cited fact states it" for d in sorted(p.dirs - dirs)]
    for n in sorted(p.negs, key=str):
        if n == "neg":
            if not any("neg" in fc.claims.negs for fc in facts):
                why.append("a negation: no cited fact negates")
        elif n == "none":
            if not any(fc.claims.nothing or "neg" in fc.claims.negs for fc in facts):
                why.append("'none': no cited fact gives a zero count or negates")
        elif not any(n[1] in fc.claims.nothing for fc in facts):
            why.append(f"'no {n[1]}': no cited fact says there is none")
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


def _judge(text: str, context, *, per_sentence: bool = False, notices=()) -> list:
    """Every judged sentence of ``text`` against ``context``'s facts, as :class:`_Judged`, in
    order (headings and known notices are not judged; see the module doc)."""
    text = text or ""
    all_ids = set(context.ids())
    names = _known_names(context)
    cache: dict = {}

    def claims(ids):
        for fid in ids:
            if fid not in cache:
                cache[fid] = _FactClaims(context.by_id(fid), names)
        return [cache[fid] for fid in ids]

    text_cited = [c for c in extract_cites(_visible(text)) if c in all_ids]
    out = []
    for seg in _segments(text, notices):
        if seg.kind != "sentence":
            continue
        sent = text[seg.start : seg.end]
        vis = _visible(sent)
        cites = extract_cites(vis)
        own = [c for c in cites if c in all_ids]
        unknown = [c for c in cites if c not in all_ids]
        reason, why = "", []
        if per_sentence and not own:
            reason, why = "uncited-sentence", ["it cites no known fact"]
        else:
            p = _parse(vis, names)
            why = _number_problems(p, claims(own if per_sentence else text_cited))
            if why:
                reason = "uncited-number"
            elif own:  # without per_sentence, a sentence that cites nothing makes no claim
                why = _claim_problems(p, claims(own))
                reason = "unsupported-claim" if why else ""
        out.append(_Judged(seg.start, seg.end, sent, own, unknown, reason, why))
    return out


def _rebuild(text: str, drop, unknown=()) -> str:
    """``text`` without the sentences of ``drop`` (:class:`_Judged`), each id of ``unknown`` taken
    out of its citations. Every other line stays as it was (headings, blank lines, list
    markers); a line whose sentences all went is removed, with a blank line it would double."""
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
            cuts.append(gone[g])
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
