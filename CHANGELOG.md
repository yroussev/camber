# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## Older releases

Releases before 0.110.0 are archived under [`docs/changelog/`](docs/changelog/index.md):

- [0.106.0 to 0.109.1](docs/changelog/changelog-0.106-0.109.md)
- [0.103.0 to 0.105.0](docs/changelog/changelog-0.103-0.105.md)
- [0.98.0 to 0.102.0](docs/changelog/changelog-0.98-0.102.md)
- [0.90.0 to 0.97.0](docs/changelog/changelog-0.90-0.97.md)
- [0.60.0 to 0.89.0](docs/changelog/changelog-0.60-0.89.md)
- [0.1.0 to 0.59.0](docs/changelog/changelog-0.1-0.59.md)

## [Unreleased]

### Security
- **A citation is supported only by a fact this server gave the session (#294).** `check_citations`
  used to believe whatever facts the caller passed, so an invented fact, an altered value with a
  recomputed id, or a fact about a facility the caller may not see could make a sentence
  "supported". The server now records every fact it sends in a per-session ledger (`camber mcp`:
  one per process, kept in the server process, so a killed or recycled worker loses nothing) and
  looks each cited id up there: an id it never sent in this session (from another server,
  config or store, from before a restart, or made up) is `unknown_citation`. The ledger is
  bounded (about 8 MB of the most recent facts); a cited fact it has since dropped is the new
  verdict `forgotten_citation` -- run the tool again and cite the new id.
- **Research-only data is refused wherever it is copied (#319).** A partial or altered copy of a
  research-only facility -- one month copied into a test facility, a whole partition with one
  part file deleted, renamed or merged, or a copy into another store -- was served in full by
  `camber mcp` under its new id: the 0.111 checks compared whole partitions. `camber datasets
  ingest` now writes the dataset id, its licence and a research-only flag into every Parquet
  part file's metadata, every rewrite keeps it (append, `store normalize`, `store
  migrate-partitions`, `retention apply`, a forced re-ingest, archive and restore), and the
  capability registry refuses (`refused_licence`) any facility holding a part marked
  research-only, in any facility, directory or store, before the call runs and again when a tool
  reads it. The refusal names the facility and the dataset, never a path. `camber run`, `camber
  report` and `camber lab` still read research-only data locally, with the licence banners.
  **Re-ingest to get the marks:** a store ingested before 0.112 has none, keeps the 0.111
  checks, and a partial copy of it is still served; `camber datasets ingest <id> --store DIR
  --force` adds them.
- **A research-only part file is checked whatever it is named (#329).** The store reads every
  file in a facility directory whose name does not start with `_` or `.`, but the research-only
  check looked only at files named `*.parquet`, so a research-only part copied in as `x.parq`,
  `x.PARQUET`, `x` or `x.parquet.tmp` let `facilities` and `points` list its equipment, classes
  and roles (the data-reading tools still refused it). The check, `camber datasets provenance`
  and the store readers now share one definition of a part file, and a facility directory
  nested too deep to check in full is refused.
- **A dangling symlink or an unreadable directory no longer hides research-only parts (#334).**
  The research-only check walked a facility directory and stopped at the first entry it could
  not resolve, so research-only parts copied in by hand after a dangling symlink (or a directory
  it may not list) went unchecked: `facilities` listed the facility, `points` served its
  equipment ids, classes, roles and data span, `camber datasets provenance` showed nothing
  marked and a local report had no non-commercial banner (the data-reading tools still refused
  it). The check now goes on past every entry it skips, and a facility it cannot check in full
  -- a dangling symlink, a directory it may not list, a symlink loop or files nested too deep --
  is refused with the reason that not all of its files could be checked (`refused_licence`,
  `licence_unverified`). A change below the month level (a copied-in subdirectory) is now seen
  by the cached check too.

### Fixed
- **A facility is no longer refused while a re-ingest swaps its files (#334).** The provenance
  walk counted a file removed between its listing and its `stat` as a dangling link, so a
  reader during a forced re-ingest could get `refused_licence` ("not all of its files could be
  checked"). Such a file is now skipped; a dangling link is still refused.
- **`check_citations` no longer calls a wrong claim "supported" (#294).** A sentence was
  supported when it cited a known fact and its numbers appeared somewhere in that fact, so
  `-45%` passed against 45 %, `47,980 MWh` against a figure with no unit, a 60 °F threshold as
  "saves 60%", another air handler's or meter's name with this one's figures, "consumption
  rose" against a saving, "meets G14" against a baseline that fails it, a citation hidden in an
  HTML comment, and a number that was only part of a name from the data. Each figure must now
  be one a cited fact states, with the same sign, unit, counted thing (hours, samples, points,
  ...) and role (a threshold is not a result); each date or year one a cited fact carries; and
  each equipment, meter or facility named, verdict, direction and negation one a cited fact
  states; a claim's own figure (a `claimed_figure` fact) supports only a sentence that says it is
  the claim's. Only visible citations count. Docs: CAPABILITY-REGISTRY.md, "Where a cited fact comes from".
- **`check_citations` no longer rejects what a fact does support (#294).** A rank ("Ranked cause
  1", "step 2"), an ordinal or a list marker is not a figure; "in 2017" matches a fact whose
  period is in 2017; a `NOTICE:` line repeating the text of a `notice` fact among the facts
  is kept and not judged; and `[F1@a, F2@b]` (or `[F1@a; F2@b]`) cites both ids instead of nothing.
- **`check_citations` reads each figure, zero and verdict with its own clause (#332).** Four
  kinds of wrong sentence were still marked supported because a word matched somewhere in the
  cited fact. A whole count ("All 11 points are trusted", "106 of the 106", "0 of the 11 points
  are suspect") now needs a fact that says all, or none, of that many; a zero or "no" ("No
  faults were found", "0 rules returned a fault") needs the fact's zero of the same thing in its
  own list item, not the `0 rule(s) skipped` of another; a share keeps its label, so "the
  cooling valve leaked on 86%" fails against `HW-leak 86% / CHW-leak 0%`; and a finding re-judged
  with the sensor's bias taken off supports only its new verdict, not "is still a fault". Also
  read now: "it is false that", "is incorrect", "but that is wrong" and a closing "-- not";
  "the same days" against "other days"; rank words ("the top issue", "the second issue", "#1",
  "the worst", "most severe") only where a cited fact ranks; a cause or effect ("accounts for",
  "due to", "about the same size as", "will clear this warn") only where a cited fact states
  the explanation. True sentences that were stripped now pass: "bias +0.0 and RMSE 0.1", "the
  CHW-leak share is 0%", "HW-leak 0% / CHW-leak 0%" without what it is a share of, "0
  facilities are visible" (a server's own summary was stripped), "None of the baselines meets
  G14" when each cited fact says its baseline does not, a paging notice restated with what it
  counts ("only the first 25 of 223 findings"), and whole sentences of a fact or notice quoted
  word for word. `4P` and `3P` are model names, not counts, and a `why` gives the words as
  written (no internal placeholders or stems) and says when a claim spans several facts.
- **`check_citations` reads what a sentence says of a counted thing, and labels as closed sets
  (#336).** Fresh wordings of the same wrong claims still passed: a dropped or moved quantifier
  ("The 8 flagged hours ... all fall in the reference window" or "8 out of 8 flagged hours fall
  ..." against "None of the 8 flagged hours ... falls"; "The 11 points are trusted" against 9
  trusted, 2 suspect), a zero said otherwise ("Not a single fault was found", "No rule passed",
  "is fault-free" against a run with faults; "No rule returned a fault or a warn", which an
  earlier 0.112 build rejected, borrowed the run's `0 warn`), and a figure moved to another
  label ("the hot-water valve", "reheat", "preheat" or "steam" valve given the cooling side's
  share; FC10's share given to FC11; "1 point is suspect" against 2 suspect, 1 untrusted). Now,
  with the quantifiers CAPABILITY-REGISTRY.md lists, before the noun, after it, or none at all
  (where "the 11 points" is all of them), the count a sentence gives must be a cited fact's
  count of that label or of that statement, the heating and cooling sides (each with its
  spellings), the trust classes and the fault conditions each bind their own figures, and a
  figure the fact tells apart by one of them fails when the sentence does not say which. Also:
  "stands", "remains valid" and "still holds" no longer keep a finding re-judged ok a fault;
  "no fan heat was set" no longer matches "measured_fan_heat_f not set". True sentences that
  were stripped now pass: "declined because of the untrusted input" when a cited notice says
  "declined because an input scored below the trust floor", "with the +3.0 °F bias taken off"
  against "corrected by -3.0 °F" (a figure with the other sign says so in its `why`), "the
  figures above", "the 2016 baseline", and "the least trusted point" for the first of
  `sensor_trust`'s least-trusted-first list (its point facts now carry their `rank`). The
  prompts ask for `check_citations` on the whole revised draft each time, not only the
  sentences changed. The forms still not read, and those that fail closed, are listed in
  CAPABILITY-REGISTRY.md, "What it does not check".
- **`check_citations` reads "both", "each" and "all" across items, verdicts on a whole set, and
  more forms of zeros and counts (#338).** The fourth round of the 0.112 acceptance test still
  found wrong sentences marked supported. "Both AHUs fail leaking_valve" (or "each AHU", "the
  AHUs all", "on both units", "every unit") citing one air handler's finding, or citing a second
  one that says "no valve leak", now fails: such a set is read against the equipment, points,
  meters or facilities the cited facts are about, each needs a cited fact stating the claim of
  it, and "both" or a number needs that many. "The 5 rules passed", "The 7 equipment are
  healthy" and "The 3 rules returned a fault" now fail against a run with faults, warns or oks
  besides. Zeros said as "Faults on X: 0", "Number of faults: 0", "faults were not found",
  "there aren't any faults", "fault free", "suspect-free", "free of suspect points" and
  "neither suspect nor untrusted points" now agree with the fact's count. "The 37 judged samples
  were flagged" against "flagged 8 hours (32 of 37 judged samples, 86.5%)", "The 1672
  free-cooling hours ran mechanical cooling" against 42% of them, "There were 8 flagged hours in
  the reference window" against "None of the 8 ... falls in the window", "1 point is below the
  trust floor and 1 is suspect", "A single point is suspect", "They are all trusted" and a table
  cell "all trusted" now fail where the fact says otherwise. "The majority of" and "the bulk of"
  are read as "most of", "over 9" as more than 9, and "half of" as a share of the count, so
  "At least half of the 11 points are trusted" passes again. True sentences that were stripped
  now pass: "86.5% of the judged samples were flagged", "0% heating and 0% cooling leak", one
  clause's "high" next to another clause's "neither high nor low" cited to another fact, "trusted
  at 0.97 and the ... setpoint at 1.00", "One ok result" and "The claim names 2017, 12% and
  2016". Every result now says that it covers only the text passed, and the prompts that a
  reworded answer must be checked again; MCP.md says the same for a question asked in the
  conversation. The forms still not read, and those that fail closed, are listed in
  CAPABILITY-REGISTRY.md, "What it does not check".
- **`check_citations` screens every sentence for a claim of checking, and binds verdicts to the
  unit, rule and point they are said of (#342).** Round 8 of the 0.112 acceptance test found
  short, status, lead-in and first-person claims of checking passing ("All supported",
  "Checked", "✅ Supported", "Status: all supported", "I reviewed each rule result", "This triage
  was checked", "The 2.5% saving was validated by IPMVP methods"), and uncited ones not judged
  at all (kept in `verified_text` with `grounded` true, seen in a real host). Every sentence,
  cited or not, is now screened: a claim that the text, the work, or what it states was checked,
  verified, supported, validated, grounded, confirmed, reviewed, fact-checked or backed -- of
  any noun, by the writer, by its evidence ("the cited fact supports"), by the checker ("The
  check passed") or as a checkmark -- fails, never reaches `verified_text`, and opens it with the
  NOTICE; a check word a cited fact states of the data ("checked against its reference") still
  passes. Units, rules and points: "AHU 1", "AHU–1", "AHU - 1" and "Air handler 1" are bound to
  AHU-1 when one cited id fits (else they fail closed); "B fails X, unlike A", "but not A", "(not
  A)", "as opposed to A", "Not A but B", "A's damper, not B's", "B fails X and A passes" (or a
  comma or dash), "A fails X, but B does not", "A: no valve leak" and "No valve leak was found on
  A" read each unit against its own facts, as do "A agrees with its reference while B
  disagrees" and "leaking_valve flags A"; a finding's "conditional on ... warn" and a rule run's
  summary of another rule's result ("... supply_air_control (ok)") give no verdict of the
  finding; a sentence naming a point is read against that point's facts ('"oa_damper" never
  moves' citing the heat valve's flat line fails); a site's verdict, negation or quantifier
  needs a fact about the whole site ("site-north has no valve leak" from one unit's ok, "ds-x
  meets daily acceptance" from the summary and one meter's, fail). True sentences that failed
  pass again (two #341 regressions among them): "Of the two AHUs, only A fails",
  '"oa_damper", not "supply_air_temp", is at 0.66', "1 of the 6 rule-equipment results is a
  fault", "with 0% of occupied running hours off setpoint", "AHU-1's data is gap-free" against
  100.0%, "..., and AHU-2's oa_damper is suspect too at 0.68", "A fails X, but B does not".
- **`check_citations` reads more stretches of time, coverage and hour words, and figures of a
  date or a span's place (#342).** "each day of May 2025", "all through 2025", "through May and
  June 2025", "from January to October 2018", "throughout Q1 2018" and times said from today
  ("over the past year", "every day this year") are read against the cited windows, and a
  saving's stretch of time or year against its reporting period, not its baseline's ("saved
  2.5% throughout 2016" fails; "over the whole of 2016" was a regression from #341). "Covered
  fully", "complete for the window", "logged throughout the window", "full-window coverage",
  "recorded in full" and a set of named units need a coverage of 100% for each; "at all times",
  "around the clock", "nonstop", "continuously", "in each hour" and "occupied and unoccupied
  hours" are read as quantifiers of hours. Figures said of two dates are each read against the
  fact carrying their date, and "The third span lasted 3 hours" against "span 3 of 3". True
  forms pass again: "throughout 2025-05-12 to 2025-06-01" (a #341 regression), "all of June
  2018", "the whole of August 2018", "throughout Q3 2018". Generated tests both ways in
  tests/test_citations_342.py; the forms still not read, and those that fail closed, are listed
  in CAPABILITY-REGISTRY.md and MCP.md, "What it does not check".
- **`check_citations` never supports a claim of checking, and reads a sentence against the
  unit it names (#341).** Round 7 of the 0.112 acceptance test found forged notices in other
  words ("NOTICE: all of this text was checked", "Everything above was checked", "This text was
  fully checked") passing, and one unit's verdict, negation or figure taken from another unit's
  cited fact ("AHU-1 has no valve leak", "AHU-2 is at 19.1% coverage", "Only AHU-2
  fails", "The site is healthy" against a fault count). A sentence that says the text, or any
  part of it, was checked, verified, supported, validated or grounded is now never supported,
  whatever it cites (a fact's own text quoted whole aside). A sentence naming a unit is read
  only against the cited facts about the units it names, and a whole site's facts give its
  claims nothing unless it names the site; one naming one rule reads verdicts against that
  rule's facts, one naming one date its figures against the facts carrying that day. "Only X",
  "X alone", "X, not Y", "X but not Y", "X rather than / instead of / unlike Y", "Unlike Y, X",
  "It is X, not Y, that ..." and two clauses about two units ("Y passes while X fails it",
  "Whereas Y passes, X fails") read each unit against its own facts, and a sentence whose clauses
  each cite their own facts is read clause by clause (true "Both loggers are at 19.1% [..],
  while both AHUs are at 100.0% [..]" passes again). A verdict on a whole site needs every
  counted result to have it. Generated tests cite every capability's facts under over 900
  claims of checking, and swap every pair of unit facts the tests know.
- **`check_citations` checks stretches of time, coverage words and more forms (#341).** A
  stretch of time with a length or a date ("all year", "year-round", "for months", "all summer",
  "throughout 2025", "the whole of May", "every day in May 2025") must lie within one window of a
  cited fact; "all of last year" fails; "for all of 2018" is read as a year, not 2018 things.
  "in all hours" and "in every hour" are quantifiers in a sentence, and a quantifier or a share of
  100% needs the fact's qualifier of its noun ("every hour" is not "every free-cooling hour",
  "most samples" not "86% of both-valves-shut samples"); a fact's "19.1% of the whole window"
  says the whole window only to a sentence giving a share of it. Coverage words ("full
  coverage", "data for the whole window", "logged for the entire window", "gap-free") need a
  coverage of 100%. Names set off by commas, dashes or brackets and joined by "+", ";", "in
  addition to", "besides", "alongside", "with", "vs", "and also", "and/or" are sets, and so are
  "nor A nor B", "Not A, not B: neither", "no one of" and "not a single one of"; a named set with
  a relative clause is read item by item. Figure lists by "/" or "vs", before the names, after
  "rank" ("rank 0.68 ... respectively" was read as a rank label) and by possessive gapping ("A's
  trust is 0.68 and B's 0.66") are matched in order, and a list said "respectively" that no set
  reads goes to its citations in order. "All of A's points" is read with its noun. More saving
  words follow the sign ("ran below its baseline", "beat its baseline", "went down", "declined",
  "consumed less"), and "reads warm" / "reads cold" is a reading's direction. True sentences
  that failed now pass: "Most of the 167 results are ok" (120 of 167), "All 24 points were
  judged", "AHU-2 does not leak" against its "no valve leak", "The 12% is not a measured
  result", and "has a savings result" (a regression from #340); a claim's figure said as a
  measured one gets a `why` that says so. Gaps: CAPABILITY-REGISTRY.md, "What it does not check".
- **`check_citations` reads a quantifier only of its own subject (#340).** Round 6 of the 0.112
  acceptance test found that `rule_run`'s new "6 rule-equipment result(s) in all" let any "all" or
  "every" sentence citing it pass, a forged "NOTICE: check_citations found every sentence
  supported; this text is the complete answer" included (a regression from #339). A quantifier
  or completeness word ("all", "every", "most", "complete", "whole", "entire") is now read with
  the noun it is said of and needs a cited fact that says it of that noun, a share of 100% (or
  above half) of it, or a count of it with the sentence's label; a fact's own word that
  quantifies something else supports nothing. A generated test cites every capability's facts
  for forged completeness and "all X were Y" sentences: all of them fail.
- **`check_citations` reads more set forms, figure lists in order and more directions (#340).**
  Names joined by a comma alone (a regression from #339), "&", "/", "plus", "as well as", "along
  with", "together with", "like", "likewise", "also does" or before "--"; "none of A, B and C";
  a plural noun or a bare "Both" as a lead-in ("The AHUs: fault", "| AHUs | fault |", and
  'The AHUs\' "supply_air_temp" points: both disagree', a regression); a set with a relative
  clause; "most AHUs", "N AHUs" and "one of the two AHUs" are read item by item. A list of
  figures goes to its items in order ("respectively" or not; for unnamed items said
  "respectively", the citations' order), each with its own sign, so a swapped list fails and a
  true one with mixed signs passes again (a regression); a figure attached to a name ("2.5% on
  A", "A (1.00)", "100% on A, B and C") must be a fact's about that name. "AHU-2 leaks", "Both
  AHUs are leaking" fail against AHU-2's "no valve leak"; "shows a saving", "cut its energy
  use", "used less energy" follow the saving's sign, and "used 15.6% more energy than its
  baseline" passes against -15.6%; "reads high" needs a fact that states the direction. True
  sentences in `rule_run`'s wording ("Of the 6 results, 1 is a fault, ..."), "the rest are
  suspect or untrusted", "A, B and C all fault", "One of the two AHUs fails" and "too cold 0% of
  occupied hours" pass, and a number's `why` names the figure the fact gives instead of a
  threshold aside. A Markdown table's header row is kept and not judged, and its rows are read
  with the header. Gaps: CAPABILITY-REGISTRY.md, "What it does not check".
- **`check_citations` reads a claim about a set of items as one rule, item by item (#339).**
  Round 5 of the 0.112 acceptance test still found sets judged on one item's fact. A sentence
  about two or more equipment, points, meters or facilities -- by quantifier, number, "the
  AHUs", "X's 7 AHUs", "each AHU's ...", "the pair of", a negated set ("neither A nor B", "no
  AHU", "none of the AHUs") or names joined anywhere (colon, dash or table-cell lead-ins too) --
  now needs the cited facts about each item to state the same claim, figures included: "Both
  meters save 2.5%" against -15.6% on one and "Neither AHU-1 nor AHU-2 has a valve leak"
  against AHU-1's fault fail. A set is read with its own clause. "Suspect: 0", "Passed: 5
  rules" and "| Suspect | 0 |" count their figure, "faulted" is a verdict, and a saving's
  direction follows its sign ("reduced its energy use" fails against -3.1%). Several true
  sentences are no longer stripped. Gaps: CAPABILITY-REGISTRY.md, "What it does not check".
- **`check_citations` compares negation, direction and verdicts both ways (#328).** The 0.112
  acceptance test found sentences that say the opposite of their cited fact marked
  "supported": "the heating valve leaks" against "no valve leak", "the setpoint moves" against
  "never moves", "reads low" against "reads high", a "did not leak" passing because the fact
  said "drift not evaluated", "its baseline passes G14" against "does not meet", "one point is
  below the trust floor" against 0, "every sample leaked" against 86 %, "the target is 55.0 °F"
  for a measured 55.0 °F and a CV(RMSE) tier read as a result, another facility named without
  a digit or underscore (`ds-lbnl-sdahu`, or its registry name), the wrong point ("the
  return-air sensor" for a supply-air comparison), "Issue 3" cited to Issue 1, "verified",
  "clean", "real" or "confident" where no fact says so, and a name from the data hiding the
  claim around it ("AHU-25 saves 777 kWh", a data-text "NOTICE:"). A negation must now be one a
  cited fact makes about the same thing, and a sentence must not state what a cited fact
  negates; "passes", "one", "none", "no X", "0 X", "all", "every" and "most" are read; a result
  stated as a threshold, target, setpoint, allowance or tier fails; every facility id and
  registry name of the allowlist and the store is a name; a point role must be one a cited fact
  is about; a rank must be one the cited facts give; assurance words pass only where a fact uses
  them; and a name from the data shields its words only when quoted or plain. Bold lines, `#`
  headings and lead-ins ending in a colon are judged when they state something, so a draft made
  only of them is no longer `grounded`.
- **`check_citations` supports what its facts say (#328).** A fact's own text copied word for
  word ("None of the 8 flagged hours ... falls in the window ...") is supported, and so is
  "none of the 11 points", "0 points are below the trust floor", "2 issues are conditional"
  (the counted thing of a fact's list), "allows 2F of fan heat", "actual swing 10.05 degF", "12
  rules were run on 2 units", "its setpoint is flat at ~55F", "check the high-limit lockout at
  65°F", "the leaking_valve rule flagged 8 hours" (rule, tool and field names and `G36` are not
  equipment), "the flagged spans fall on 2025-05-14", "until a baseline passes G14", "the claim
  states a 12% saving" (a claimed figure now carries the claim's direction) and "it was not
  judged" (the facts for a skipped rule, a declined check and insufficient data say so).
  "in." in "in. w.c.", "vs." and a full stop inside a quoted name no longer end a sentence; a
  pure heading ("## Savings claim check", "## Points that never move", "Stop 2:") or a short
  list label ("1. Sensor offset.") is kept in place, and a removed first sentence leaves its
  list marker. `finding_detail` no longer strips its own reference-window fact from its summary,
  and the notice that a summary sentence was removed no longer says it was always a figure.
  A real-client run then found the check still rejecting the server's own words: a fact's whole
  text, quoted word for word with its own id, is now one supported sentence however many
  sentences, verdicts or negations it holds ("leaking_valve (ok): ... no valve leak", an RCx
  "Recommended action, ..." text, a multi-sentence rule-parameter or licence notice), and so is
  a NOTICE line that repeats a notice and cites its `N` id ("NOTICE:", "Notice:" or bold),
  which a full stop or semicolon in it no longer splits into an uncited half. A quote with a
  word added, changed or left out, or citing another fact, is judged as before. The
  `check_savings_claim` prompt no longer recommends "The claim states a 12% saving", which
  fails when the claim was a bare "12%": it asks for the claimed figure with no word of
  direction ("The claim states 12%"). A share listed before the one that says what it is of
  ("HW-leak 0% / CHW-leak 0% of the samples") is now read as a zero of that too, as in the
  facts: copying that list is supported, and "HW-leak 0%" cited to a fact with "HW-leak 86%"
  no longer is.
- **`verified_text` keeps the draft's lines (#322).** It joined the draft into one paragraph
  and dropped headings ("# AHU triage", "Findings:") as uncited sentences. It is now the draft
  with only the failing sentences removed: paragraphs, list markers, headings and the trailing
  line break stay. `camber.agent.verify.check` repairs a text the same way, so `camber explain`,
  `camber ask` and the RCx report's prose keep their lines too.
- A store fence whose process id has 4,301 or more digits, or is otherwise not 1 to 10 ASCII
  digits, is a malformed fence: it no longer makes every call on its facility and the whole
  `facilities` listing fail with an internal error, and `datasets ingest --force` resets it
  instead of failing (#322).
- Ctrl-C early in `camber mcp` exits 130 quietly. An interrupted import inside numpy or pandas
  could turn the Ctrl-C into a misleading numpy `ImportError` traceback with exit status 1, or
  swallow it so that the server started anyway and ignored further Ctrl-C (#322).
- `camber mcp` ended by a signal no longer dies by a second signal that arrives 20-100 ms
  later, after its cleanup: it exits 143, 129 or 130 as documented, so a supervisor reading the
  wait status sees the exit status, not "killed by signal" (#326).
- The matplotlib directory of a `camber mcp` killed with `SIGKILL` is now removed by the next
  server also when every server runs as pid 1 in containers sharing a `/tmp` volume: a server
  holds a lock on a file in its directory while it runs, and the sweep goes by that lock
  instead of the process id (#326).
- The container image's `HEALTHCHECK` probes the read API only when the container runs it (the
  default command or `camber serve`); a container running `camber mcp` or any other command no
  longer turns `unhealthy`. With a 0.111 image, start such a container with `--no-healthcheck`;
  DOCKER.md and MCP.md show how to run the MCP server from the image (#326).
- **A momentary failure to read the facility registry no longer changes an answer (#324).** A
  writer replaces `_facilities.json` with a rename, which some filesystems (a Docker Desktop
  bind mount) show a reader as briefly missing. A `camber mcp` call that read it at that moment
  ran a catalog-dataset facility with every built-in rule at its defaults instead of its packaged
  template (different `rule_run` findings and `sensor_trust` scores, with no notice), or refused
  it as `refused_licence`, or answered under a notice saying nothing was served. A failed read is
  now tried again for a moment; one that keeps failing while the file changes, or fails with an
  I/O error, is `unavailable` with `"retryable": true, "reason": "registry_unreadable"`, and
  `camber mcp` refuses to start rather than guess. A call reads the registry once, so its licence
  check, config template, timezone and notices agree. A registry that stays missing or damaged
  is still refused (#290).
- **A host and a container no longer write one store at once (#325).** The store and cache lock is
  the kernel's `flock`, which a macOS host and a Docker Desktop container on one bind mount (or two
  machines on one share) do not share: both wrote, ingests failed (`ENOENT`, `Directory not
  empty`) and left `_trash-ingest-*` directories, and readers got wrong answers. A writer now
  also claims `<dir>/_lock.owner` and keeps a heartbeat in it; a writer on another system waits
  for it and is then refused with `… locked by <pid>@<host> … on another system sharing this
  directory`, and a claim whose writer was killed is taken over (at once on its own system,
  after 10 s without a heartbeat from another). CAMBER 0.111 and older make no claim, so write a
  store from the host or from containers, not both (DATASETS.md, CLI.md, PORTFOLIO.md,
  DOCKER.md).
- `ParquetStore.partitions()` and `camber store normalize`'s part check no longer open a FIFO
  named like a part file (which waited for a writer for ever); the retention roll-up's partition
  read skips one too (#319).
- **Archiving a research-only facility no longer stops `camber mcp` serving the rest of the store
  (#329).** After `facility offboard` and `facility archive`, the registry's research-only entry
  had no directory, which read as a renamed or moved partition, so every facility was refused
  with "directory is missing (renamed or moved?)". An archived or purged entry no longer counts;
  the archived facility itself is still refused as research-only.
- **Deeply nested JSON no longer crashes the provenance checks (#329).** A part file's
  provenance mark or a `_facilities.json` nested tens of thousands of levels deep made every
  call fail with an internal error, `camber mcp` exit with a traceback at startup, and `camber
  datasets provenance` crash. Such a mark (or one longer than 4 KB) now counts as research-only,
  and such a registry is the usual "cannot be read" registry problem.
- **A registry broken again and again is `unavailable`, never a licence refusal (#329).** When
  `_facilities.json` was renamed, truncated or half-written again before the read retries ended,
  every retry failed alike and 1-4 % of calls were refused with `licence_unverified`. A registry
  whose file or directory changed between the retries is now `unavailable` with `"reason":
  "registry_unreadable"`. A registry that stays a directory, unreadable (no read permission) or a
  symlink loop was reported as retryable for ever; it is now the registry problem that a retry
  does not fix (`refused_licence`, with the notice to repair it).
- **A local `camber report` or `camber run` on research-only parts copied elsewhere carries the
  banner (#329).** The non-commercial banner and the "Data source & licence" block came only
  from the facility's registry entry, so a report built from research-only parts copied into
  another facility or store had neither. They now also come from the part files' provenance
  marks.
- **A writer that was stopped no longer finishes without its claim and loses data (#330).** A
  writer paused for more than 10 s (Ctrl-Z, `docker pause`, a debugger) had its #325 claim taken
  over by a writer on the other side. That was right, since a stopped process cannot beat, but
  when it resumed it finished its ingest anyway. Both exited 0, and a facility silently lost
  points. A writer now checks that its claim is still its own immediately before every step that
  changes a store, cache or workspace: a partition swap or delete, a write fence, a registry,
  manifest or workspace file write, an append's renames. If the claim is gone it fails with
  `… this writer lost its claim …`, naming the new holder, and leaves the directory as that
  writer has it. A writer that loses its claim after its last change fails too, rather than
  report success. DATASETS.md describes the one window that remains: a pause of over 10 s
  between the check and the rename right after it.
- **Two containers on one Docker Desktop bind mount no longer both get a store's lock (#330).**
  `flock` excludes nothing there, and a claim of the same kernel was taken over at once as dead,
  so both writers ran. Such a claim is now taken over at once only when its process is seen to be
  gone: same pid namespace, and its pid has exited or started at another time. Otherwise the
  writer waits for the claim's heartbeat to stop for 10 s, so a container killed while writing
  is taken over after 10 s, not at once.
- **A stopped writer is now waited for by every writer of its own system, and the refusal says
  it is stopped (#335).** On a Docker Desktop bind mount the `flock` of the open that created
  the lock file excludes nothing, while any other open's `flock` excludes every container of the
  VM. So a container paused while it wrote a store was taken over by another container after
  10 s when it had created the store's `_lock`, and blocked it for good when the file already
  existed; the docs said it was always taken over. The lock file is now never locked through the
  open that created it, so a stopped writer keeps its kernel's lock: another host process, or
  another container of the same VM, waits for it as for a running one, and is refused with
  `… -- a writer that is alive but stopped (Ctrl-Z, docker pause, a debugger) …`. Resume or end
  the stopped writer. Only a writer on another system (the host against a container, another
  machine) takes a stopped writer's claim over after 10 s. DATASETS.md, CLI.md, PORTFOLIO.md and
  DOCKER.md now say exactly when a claim is taken over.
- **A writer that lost its claim no longer leaves `_facilities.json.tmp` in the store (#335).**
  The registry, cache manifest and workspace file writes made their temporary file before they
  checked the claim, and left it when the check failed. They now check the claim before making
  the file and again before the rename, give it a name of their own
  (`<file>.tmp-<pid>-<token>`, so writers in two containers never share one), and remove it on
  any failure. `camber store verify` lists such a file at the store root as a leftover
  (`--strict` exits 1), and the next ingest removes it.
- **A waiting writer no longer reads another writer's claim while it is empty (#335).** A claim
  was created empty and then written, so a writer reading it at that moment named no holder
  ("locked by an unknown process"). A claim is now written whole under a temporary name and
  hard-linked into place, and a waiting writer names the holder by the last whole record it read.
- **A starting `camber mcp` no longer removes a live server's matplotlib directory on a
  bind-mounted `/tmp` (#330, a regression in the 0.112 candidate).** The #326 sweep trusted a
  lock that a Docker Desktop bind mount does not enforce. The directory now records its server
  (pid, kernel, pid namespace, start time), and the server touches it every 10 s. A server of
  the same kernel and pid namespace is judged by its pid and start time. Another container's
  server is judged by its lock where locks work (probed once), and by its heartbeat (2 minutes)
  where they do not. A killed pid-1 server's directory is still swept.
- **A killed pid-1 container ingest's `_staging/ingest-1-*` is removed by the next ingest
  (#330).** Pid 1 is alive on every system, so the directory was kept forever and `store verify`
  said "its process is still running". Each staging directory now records its writer in
  `_owner.json`. One made in another container or on another system counts as live only while
  its writer holds the store's claim. `store verify` says so, and says when the next ingest
  will remove it.
- **A host that shows the model only `structuredContent` now shows it the facts and notices
  (#331).** Some MCP hosts, a command-line coding assistant among them, pass a tool result's
  `structuredContent` to the model and drop the text blocks, so the model never saw the facts list
  or the `NOTICE:` lines: it could cite almost nothing (`data_quality_review` could not be checked
  at all), and research-only banners, sensor-reference conditions, paging notices and the
  stripped-caveat notice of `check_citations` were never shown. Every tool's `structuredContent` now
  starts with a `camber` object: `facts` maps each fact id to its text (the same ids and text as the
  facts block, all of them resolved by `check_citations`) and `notices` lists the notices. The
  prompts say where to find fact ids in either form.
- **On Linux, an MCP worker that grew far past its usual memory is replaced again.** A worker
  measured its peak memory with `ru_maxrss`, which on Linux carries over the peak of the server
  process that started it: once the server had held more memory than a worker's first call
  needed, every worker looked that large, and a call that took it well past its usual size
  never had it replaced, so it kept that memory. A worker now reports its own peak (`VmHWM`).
  macOS was not affected.
- **Default results fit a host's tool-output limit (#333).** On a 648-point facility the default
  `rcx_report` was about 54,000 characters of `structuredContent`; a command-line host that caps
  a tool result at 25,000 tokens set it aside in a file the model could not read, so a site-visit
  plan covered 3 of 32 issues. The JSON runs at about two characters a token, so the "100 KB (25k
  tokens)" the docs gave was wrong. Every default result now stays within 25,000 characters of
  `structuredContent` (facts and notices included; up to about 21,000 on the largest facilities
  tested), a test holds every tool to it, and docs/MCP.md gives the real figures. Paging notices
  give the exact `offset` of the next page.
- **The `facilities` notice names the research-only facilities it withholds (#333).** It gave
  only a count, so a model told a user that a correct facility id might be wrong. It now lists
  the withheld ids (never a path) and says the id is right but the data's licence forbids this
  use; the notice for facilities whose licence cannot be established names them too.
- **docs/MCP.md says how to pass a multi-word prompt argument (#333).** A host's slash command
  can split `check_savings_claim`'s `claim` on spaces, quoted or not; give the claim in the
  conversation instead.
- **A facility nested too deep or holding a symlink loop is no longer called research-only
  (#334).** It was refused as holding "part files marked as research-only" even when it held
  only open data, and `camber datasets provenance` showed a marked part that did not exist. The
  refusal now says that not all of its files could be checked, and the provenance counts only
  real parts.
- **`camber datasets provenance` says when a facility counts as research-only (#334).** A
  facility whose marks cannot be read (damaged, longer than 4 KB) or not all of whose files
  could be checked had no tag in the table, though `--json` said `research_only: true` and the
  server refused it. The table now adds `[mark unreadable: counts as research-only]` or `[not all
  files could be checked: counts as research-only]`, and `--json` adds `unreadable_marks`,
  `unchecked` and `research_only_datasets`.
- **`coverage` with `equips` or `roles` keeps the facility's whole window (#337).** With a
  filter, the window shrank to the selection's own first and last sample, so the same point read
  "19.1% of the whole window, 2 gaps" without it and "100.0% of the whole window, 0 gaps" with
  it, and both could be cited. An open start or end is now the facility's first or last stored
  sample whatever the filter selects, so a point's coverage and gaps no longer change with it.
- **`camber store verify` goes on past a directory it cannot list (#337).** A directory of mode
  000 or 0o111 (a month, a year or the facility directory) stopped it with `Permission denied`
  and an absolute path, with no counts, list or JSON. It now lists the directory as unreadable
  by its store-relative path, checks the rest of the store, prints the counts (or `--json`) and
  exits 1. A directory it can list but not enter is said to be a permission problem, not "a
  dangling symlink or a loop?". `store normalize` walks the same way.
- **Lock and cleanup timings in the docs (#337).** A killed writer's claim is taken over at most
  10 s after its last heartbeat, at once on the same system when the lock already shows it gone;
  a killed `camber mcp` server's matplotlib directory is removed by the next server to start, at
  the latest by one starting 2 minutes after the kill (DATASETS.md, CLI.md, PORTFOLIO.md,
  DOCKER.md, MCP.md).

### Changed
- **The changelog's 0.106.0 to 0.109.1 sections are archived** in
  `docs/changelog/changelog-0.106-0.109.md`, linked from the changelog, its index and the docs
  site (#340), so `CHANGELOG.md` stays under the 200 KB render limit.
- **`structuredContent` has a new first key, `camber` (#331).** It holds `{"facts": {id: text},
  "notices": [...]}` and is declared (and required) in every tool's `outputSchema`; the rest of
  `structuredContent` is the result as before, and the JSON text block in `content` is that rest
  (it no longer equals `structuredContent` byte for byte). A client that compares
  `structuredContent` with a fixed object, or rejects unknown keys, should ignore `camber`
  (`camber.mcp.RESULT_KEY`). No capability may declare that key. `tools/list` grows by about
  1.7 KB (47.7 KB) and a reply by 5 to 17 %.
- **Fact ids no longer depend on how a run config spells the paths it reads (#326).** The id
  token covered the config's text, so an absolute or a relative `source.store`, one through a
  symlink or with a trailing slash, and the same store mounted at another path (a host and a
  container) gave different ids for the same data. A path now counts by what it resolves to: the
  store by its data, a folder by its entries, the mapping and `shared_oat` files by their size and
  modification time. The ids of a server started with a `--config` change once; cite the new ones.
- **`rule_run`'s summary fact counts its verdicts as rule-equipment results (#339):** "... and
  3 of them returned a result, 6 rule-equipment result(s) in all: 1 fault, ...". Match its start.
- **`check_citations`'s summary says what it covers (#338).** Its text now ends "This check
  covers only the text passed in this call ...: give the user only text a check marked
  supported, and if you reword, shorten or add to it afterwards, even one sentence, check the
  whole text again before you give it or say that it was checked." A client
  that compares that text with a fixed string should match its start. A sentence that says
  "both", "each" or "all" of several equipment or points now needs a cited fact about each one:
  cite them all ("Both AHUs fail leaking_valve [F2] [F7]").
- **`verified_text` says when it dropped a verdict or caveat (#328).** Removing an unsupported
  verdict or caveat ("for information only", "conditional on the sensor") while keeping the
  figures they qualified made a checked answer read as more certain than its draft. When a
  removed sentence gave a verdict, a negation or a caveat, `verified_text` now opens with a
  `NOTICE: check_citations removed sentences ...` line and the result carries a banner: show
  such a text only after fixing those sentences. Checking that text again keeps the line.
- **Stricter citation matching can mark sentences unsupported that passed in 0.111 (#328).**
  Write negations, verdicts and quantifiers as a cited fact does ("no valid sample", not "no
  data"; "fails G14", not "is not a pass"), quote a name from the data, and cite the fact about
  the facility, point or issue rank a sentence names. A model's own verdict, advice or
  confidence is never supported: the prompts now ask for it after the checked text, labelled as
  judgment. `rule_run`, `drift`, `rcx_report`, finding facts and the trust-floor notice now
  say "not judged" for skipped, declined and insufficient-data checks, the dataset-template
  notice says "not a config tuned for your own use of the data", finding and RCx facts carry
  their `facility_id`, and `finding_detail`'s evidence facts their `finding`; the wording of
  what "supported" means is in MCP.md and CAPABILITY-REGISTRY.md.
- **More sentences need a fact that says them (#332).** A whole count, a rank word, a denial
  ("false", "wrong") and a cause or effect are now read, so a draft that relied on them can
  lose sentences that passed before: say "9 of the 11 points are trusted" rather than "all",
  cite the issue fact for a rank, and give an inference after the checked text, labelled as
  judgment. The prompts now ask the model to run `check_citations` again on its revised draft
  until every sentence is supported, and to give only that text and its labelled judgment.
  `plan_site_visit` and `rcx_executive_summary` ask it to page `rcx_report` with the `offset`
  a notice names rather than ask for a larger `top_n`, and `check_savings_claim` to use the
  whole claim from the conversation when the argument looks cut short (a host's slash command
  can pass only its first word).
- **`check_citations` needs only the text (#294).** `facts` is optional. If you still pass it,
  every fact must be one the session was sent, unchanged, or the call is refused with
  `invalid_params` (`data` lists the ids under `not_issued`, `forgotten` or `changed`): leave `facts` out
  instead. The output gains `provenance` (`issued`, or `unverified` when a direct Python caller
  gives no `CallContext.ledger` and the facts are taken as given, as before), the verdict
  `forgotten_citation` in `verdicts` and a sentence's `forgotten` ids; code that compares
  `verdicts` with a fixed dict needs the new key. The built-in prompts no longer ask the model to
  pass facts back.
- **`check_citations` has a new verdict, `unsupported_claim` (#294)**, for a sentence whose
  figures match but whose equipment, verdict, direction or negation no cited fact states; the
  `verdicts` counts gain the key, and each failing sentence gains `why`, which says what did not
  match. A client that lists the verdicts should add it. Some drafts that passed before now
  fail: a sentence that infers beyond its facts ("so no check was declined"), uses a verdict
  word the cited fact does not ("the suspect sensor" citing the finding instead of the sensor's
  fact), restates a metric the fact's text does not state (44.9 % for "45%"), or derives a
  number (a sum, a difference, a ratio). Say it as a fact says it and cite that fact.
  `camber.agent.verify.check` applies the same rules and flags such a sentence
  `unsupported-claim`.
- The built-in prompts point at the new facts: cite a span, gap, rank or notice by its own fact,
  number a ranked list with a marker at the start of the line, say a fault is explained by a
  sensor only when `finding_detail` says so, and never cite a claimed figure for a measured
  result (#294).
- Results have more facts (#294). No existing fact id is renumbered: the new facts take their
  own prefixes (`S`, `L`, `P`, `G`, `A`, `N`, and `R` in `finding_detail`). A caller that
  expects a result's facts to be only its `F` facts, or counts them, should filter on `kind` (a
  notice is `kind` `notice`).
- The `facilities` capability no longer fails as a whole with `unavailable` while one allowed
  facility is being replaced (a re-ingest, prune or removal in progress, or one killed between
  the two renames of its swap). It lists every facility and marks that one with
  `"unavailable": {"retryable": true, "reason": "being_replaced"}`, null `start` / `end`, zero
  counts and a banner saying to retry later; calls on the facility itself still answer
  `unavailable` with `"retryable": true`. Every `unavailable` row now carries a `reason`
  (`being_replaced` or `unreadable`). A client that retried the whole listing on `unavailable`
  should read the rows' `unavailable` instead (#322).
- The `camber` console script now starts from `camber.__main__:main` (`camber.cli:main` still
  works), so a Ctrl-C while the CLI loads exits 130 quietly. Packagers that list the entry point
  themselves should change it (#322).
- **Smaller default pages, and `rcx_report` pages its issues by size (#333).** The defaults are
  now `rule_run` 12 findings (was 25), `sensor_trust` 40 points (100), `coverage` 20 rows per list
  (50), `points` 200 (250), `facilities` 40 (100), `drift` 10 per list (25) and `rule_params`
  15 rules (25; 7 with `detail`, was 10). `rcx_report` without `top_n` returns as many of the best-ranked issues as
  fit (at most 8; it was the config's `top_n`, else 8) and 5 appendix A entries (20); its
  `fact_index` entries are `{id, kind}`, no longer repeating each fact's `text` and `equip` (read
  them from the result's facts). `dataset_search` and `mv_summary` are paged (`limit`, `offset`:
  20 datasets, 5 meters with all their findings; their counts still cover everything). `drift`'s
  `diagnoses[].detail` no longer repeats `equip`, `severity` and `summary`, and a long metric note
  that several findings of a page share is said once, in a notice, not in each finding's
  `metrics`. A paging notice says "call again with offset=N for the next page" and no longer
  suggests a larger limit. A caller that read a whole list from one default call should page
  with `offset`, or pass `limit` / `top_n` (a larger page can pass a host's limit).
- **`camber store verify` checks every file the store reads (#334).** It looked only at
  `*.parquet` files in `year=/month=` directories, so a stray `notes.txt`, `README`, `.csv` or
  Finder `Icon` file that makes a facility's reads fail (`unavailable ... damaged`) was not
  reported, and it exited 0. It now uses the store readers' definition of a part file: such a
  file is unreadable, a `x.parquet~` backup or a part at the facility root or below the month is
  listed as not canonical, and an entry that keeps the research-only check from seeing all of a
  facility (a dangling symlink, an unreadable directory, a symlink loop, files nested too deep)
  is unreadable. **A script that runs `store verify` may now see exit code 1** on a store that
  passed before: remove or move the files it lists out of the facility directory.
- **One rule for a provenance mark with a field of the wrong type (#337).** A hand-written mark
  whose `dataset_id` was a number or an object was tagged "counts as research-only" in the
  `datasets provenance` table while its `--json` said `research_only: false` and the server
  served the facility. A mark whose `dataset_id` is not a non-empty string, whose `licence` is
  not a string or whose `research_only` is not `true` / `false` (or that lacks one of them) is now
  unreadable everywhere: research-only in the table, in `--json` and in the server's checks.
  **A facility holding such a mark is now refused by `camber mcp`**; `camber datasets ingest
  <id> --force` writes a well-formed mark, or remove the hand-edited part.

### Added
- **Facts the model can see (#294).** A `camber mcp` tool result now ends with a third text
  block listing its facts, one `[id] text` line each, because hosts such as Claude Desktop show
  the model `content` but not `_meta`; `_meta["camber/facts"]` stays for programmatic clients.
  It adds 10 to 15 % to a reply and counts toward the 2 MB reply cap. In Python,
  `camber.capabilities.FactLedger` holds one session's facts: give it in
  `CallContext(ledger=...)` and `Registry.call` records into it and `check_citations` reads from
  it (`Registry.ledger_view` picks out the facts one call needs, for a transport that runs calls
  in another process).
- **Facts for what a model is asked to say** (#294). A sentence giving one of these can now be
  cited, where before `check_citations` always stripped it:
  - `finding_detail`: each flagged span (`S1@finding_detail-...`: start, end, hours), the
    longest span in the hours fact, the rule's inputs that never move over the window, and for
    a finding conditional on a sensor reference, the finding re-judged with that sensor's bias
    taken off ("... ok; so the finding is explained by the sensor offset", or "the finding
    stands") and how many of its flagged hours fall in the window the reference comparison
    covers (none, part or all).
  - `coverage`: each point's coverage and each listed gap with its dates (at most 200 of each
    per page).
  - `sensor_trust` and `rcx_report`: the points that never move over the window (a valve that
    never opens, a setpoint that never resets), which can score trusted yet decide a diagnosis.
  - `rcx_report`: each issue's fact reads "Issue 1 of 6 on ...", so a numbered ranking can be
    cited.
  - every capability: each notice is a fact too (`N1@<capability>-...`), its text exactly the
    banner's.
  - `mv_summary` takes an optional `claim`: each figure it states becomes a `claimed_figure`
    fact ("The claim under check states 15%: the claim's own figure, not a measured or computed
    result"); `check_savings_claim` passes the claim to it.
  - The new facts carry their figure as fields: `value`, `unit`, `subject` (facility,
    equipment, point) and, where one applies, a `verdict`. See docs/CAPABILITY-REGISTRY.md.
- `sensor_trust` gives a sensor reference's bias its unit (`°F` for a temperature, `%` for a
  valve, damper or speed) in the summary, the reference's fact and a new `unit` field of
  `data.references`.
- `camber --version` prints the installed version, and `python -m camber` runs the CLI (#322).
- **`camber datasets provenance --store DIR [--missing] [--json]` (#319)** lists each facility
  with how many of its part files carry their dataset's provenance, and names the catalog
  facilities that lack it (ingested before 0.112). It reads the files' footers and writes
  nothing.
- **`mv_summary` states each meter's baseline and reporting dates (#333).** A claim's periods
  ("against the 2016 baseline") could not be checked: no result gave a date. Each finding's
  `metrics` now carry `baseline_start` / `baseline_end` (and on a saving `reporting_start` /
  `reporting_end`), and its fact gives them as `baseline_period` / `reporting_period` and in its
  text ("baseline period 2016-01-01 to 2016-12-31 (the year 2016)"), so a sentence can cite them.
  An `mv_baseline` finding's metrics gain the `window` the versioned one already had.

## [0.111.0] — 2026-10-31

**0.111: the MCP server hardened by a five-round acceptance test (#282, #289–#293, #295–#318, #320, #321).** Testers who had not built `camber mcp` attacked 0.110.0 and then each fix candidate with protocol fuzzing, hostile data, load, concurrent re-ingests and removals, killed processes, real clients and a blind fault-triage test; every finding they reported for 0.111 is fixed, and in the last round both testers recommended release. Tool calls now run in worker processes that a timeout, a cancel or a closed client stops, at most `--max-concurrent` at a time (#292, #309, #316). A reader overlapping a forced re-ingest or a removal gets the retryable `unavailable`, never a partial answer (#306, #317, #321). Results come a page at a time and fit a model's context (#299). The trust floor no longer withholds healthy plant checks (#297, #311), and a sensor that its configured reference finds wrong now ranks above the faults it explains, so the `triage_ahu` prompt names a biased supply-air sensor before a healthy valve (#313). Fact ids depend on the config, trust floor and stored data (#295, #312); text from the data can no longer forge notices (#291); research-only data stays refused when the registry is damaged or a facility is renamed (#290, #312). **Behaviour changes:** a rule parameter outside its documented range is refused unless the rule entry's `basis` says where the value came from, and one of the wrong type (text for a number, a fraction for an hour) is refused (#308, #315); a run config that repeats a key is refused (#308); ingest refuses ids holding control, bidi or invisible characters (#291); `check_citations` returns a compact verdict, with `verified_text` under `detail: true` (#299); every `camber mcp` launch mistake exits 2 (#296).

**Known issues, for 0.112:** `check_citations` checks that cited fact ids exist and that their values appear, which proves less than "supported" suggests, and hosts that hide `_meta` do not show the facts to the model (#294). A partial or altered copy of research-only data under a new facility id is still served (#319). `verified_text` loses a draft's paragraph breaks and headings, a hand-damaged fence file or a facility stuck mid-swap can make calls fail until the next ingest, and Ctrl-C in the first tenth of a second can still print a traceback (#322).

### Fixed
- **A reader no longer gets a wrong answer while a facility is re-ingested (#306).** A forced
  `camber datasets ingest` swaps a facility's partition in with two renames, and for a moment
  between them the facility is absent; a `camber mcp` call (or a store read from the CLI) that
  listed it then answered from an empty or partly listed facility -- a fault fewer, 0 %
  coverage, "no stored data", `not_found` -- with nothing to tell it from a real answer. Every
  writer that replaces or removes a facility's files (the ingest's swap, `datasets remove`, a
  subset's dropped facility, `prune`, `drop_partition`, `retention apply`, `migrate-partitions`,
  a facility archive or purge) now marks the facility in a per-facility write fence
  (`<store>/_fence/<facility_id>`). A read waits up to a second for a swap in progress, a read
  that a swap overlapped is read again once, and a capability call that a swap overlapped is
  `unavailable` with `"retryable": true` whatever it would have answered; a retry gets the new
  data. Readers still take no lock, and calls on other facilities are not affected.
- **Two edge cases of that write fence (#317).** An ingest killed in the middle of its swap
  could leave the facility marked busy with its process id; once the system gave that id to an
  unrelated process, every call on the facility (and every `facilities` listing) answered
  `unavailable` until the next ingest, and retrying never helped. The mark now also records when
  the writer started, so a reused process id is not taken for the writer, and the next command
  that takes the store lock clears a mark whose writer is gone. And `camber datasets remove
  --purge-store` (or `drop_facility(forget=True)`) now keeps the fence until the facility's
  registry entry is removed too: a call in the last few milliseconds of a removal got a
  non-retryable `not_found` or `invalid_params`, where it now gets the retryable `unavailable`.
- **More edge cases of the store's write fence, and Ctrl-C at start (#321).** A fence mark
  naming a process id too large for the system (a damaged or hand-edited store file) made every
  call on the facility and every `facilities` listing fail with an internal error, and `camber
  datasets ingest --force` crashed on it; such a mark (or a malformed one) now blocks nothing
  and the next writer clears it. An ingest killed in the few milliseconds between the two
  renames of its swap left the facility registered without its data, so calls answered a
  non-retryable `not_found` or `invalid_params` and the listing called it "active with 0
  points" until the next ingest; calls now get the retryable `unavailable`, and the next
  command that takes the store lock finishes the swap from the leftovers it kept. A first
  ingest now writes the facility's registry entry before it lifts its fence, so a `facilities`
  listing in between no longer says the registry needs repair. And Ctrl-C in the first moments
  after `camber` starts now ends it quietly with exit status 130, instead of a traceback or a
  misleading numpy import error with status 1.
- **Equipment and point names with a slash read as themselves again (#307).** Since the 0.111
  path redaction, a name such as a Niagara ORD (`/Drivers/BacnetNetwork/AHU_1`),
  `slot:/Config/AHU-6`, `/AHU-4` or anything holding `</` became `<path>` in summaries, banners
  and errors (four air handlers all read `"<path>"` while the facts named them). A name from the
  data now gets only the server's own directories (store, config, data directory, home, temp,
  working directory, Python prefix) taken out, and the `not_found` listing of stored ids shows
  them exactly. Paths of the server are still redacted everywhere, and a value the caller sent
  is echoed redacted as before.
- HTML tags, markdown links and URLs in data-borne names no longer reach summaries as markup
  (#307): `<img ...>` reads `‹img ...›`, `[click](https://...)` reads `[click] (https[:]//...)`,
  so a client that renders markdown or HTML shows the name but no tag or clickable link.
- Prompt-argument completion no longer offers names that hold markup, a link or a fact-id
  bracket, or that start with `NOTICE` (#307).
- The notice after the summary's citation check says what it removed: "N sentence(s) ... were
  removed: they state a figure that no fact they cite carries", or a count of citations of an
  unknown fact id (the sentences kept), instead of "N statement(s) were removed" for both. A
  quoted name is never cut by it (#291).
- Schema patterns no longer accept a trailing newline (`"lbnl-sdahu\n"` is now `invalid_params`,
  not `not_found`), and `finding_detail` opens a finding on equipment whose stored id holds a
  newline (#291).
- One very long name (100 KB) no longer makes `rule_run` or `rcx_report` `too_large` for the whole
  facility: a string over 2,000 characters in a result is cut, with a hash that keeps cut names
  apart (#291).
- Prompt arguments lose control, bidi and invisible characters as well as newlines (#291).
- Path redaction goes on across a space after a known root while the next word has a separator,
  so `<path>/John Smith/My Drive/x` no longer leaves `Smith/My Drive/x` in an unquoted message
  (#291, #302).
- **A capability call reads only the facilities it is about (#293).** While the store's point
  catalog was stale (after an ingest), every `camber mcp` call that needed it -- `facilities`,
  `points`, `coverage`, `sensor_trust`, `rule_run` and the rest -- rescanned every facility in
  the store, because a no-persist call never writes the catalog back: a `points` call on one
  small facility took seconds and gigabytes in a large store, and the server kept that memory.
  A call on one facility now scans only that facility (and remembers the result until its data
  changes), and `facilities` reads each listed facility's catalog entry and file statistics
  instead of its rows. `ParquetStore.points(facility_id=...)` and
  `equipment(facility_id=...)` behave the same way outside MCP. In a 33-facility test store,
  `facilities` and `points` on one small facility went from about 5 s and 2.4 GB to under
  0.1 s and about 130 MB, the same as in a store holding only that facility.
- **A `faults` section without `run_id` no longer crashes the run (#303).** `camber run` and
  `run_config` failed with `isoformat() argument 1 must be a unicode character, not str` when a
  config's `faults` section left out `run_id`; the default run id is now the run's UTC time to the
  second, as documented.
- **`finding_detail` explains the finding `rule_run` returned (#298).** It re-ran the one rule
  on its own, so a finding that depends on another rule read differently: a heat pump in a room
  whose two units fight was "a capacity shortfall (unit size, refrigerant)" in `finding_detail`
  but "the fight, not capacity" in `rule_run`, even when both rules were asked for. It now makes
  the same run as `rule_run` with the same arguments, so the rule, severity, summary and metrics
  match. It costs as much as the `rule_run` call.
- **More findings have flagged-hour spans, and the rest say why (#298).** About a third of the
  fault and warn findings had no spans. `actuator_stuck`, `control_hunting`, `filter_fouling`,
  `boiler_summer_lockout`, `hw_plant_deltat`, `hw_pump_dp_reset`, `source_loop_deltat`,
  `heatpump_defrost`, `compressor_staging`, `chiller_staging`, `hp_mode_vs_need`,
  `hp_capacity_shortfall`, `reheat_minimization_g36`, `overcooling_severity`,
  `supply_air_reset_compliance` and the SAT / static reset-effectiveness rules now mark the
  samples their finding counts (the reset-effectiveness rules per failure mode, as
  `conditions`). A finding without spans has a new `violations_note`: a fleet finding has no one
  unit's hours, and `supply_air_reset` judges the shape of supply air against outdoor air, not
  single hours. These masks also give the matching RCx issues their hours.
- **Spans are contiguous and the share says what it is of (#298).** A span ran on across the
  hours a mask did not cover (one "span" from one afternoon to the next evening); spans now
  break where the mask's samples are more than one and a half intervals apart. The share was
  called "% of the window" though it was of the samples the rule judged; the text now says "of
  the N samples it judged", and `violations` gives `judged_samples`.
- **Event rules count events apart from hours (#298).** `boiler_short_cycle` reported its starts
  times the sample interval as hours (106 h for 424 starts at 15 minutes, where the boiler fired
  119 h). Its hours are now the firing on short-cycling days, and `finding_detail` gives the
  starts as `events`; `heatpump_defrost`, `compressor_staging`, `chiller_staging` and
  `control_hunting` do the same with their reversals, stage changes and starts.
- **A parameter left unset shows the value the rule used (#298).** `finding_detail` listed
  `min_oa_pct = None` while `outdoor_air_fraction` judged on its generic 20%; it now shows 20 as
  the rule's built-in fallback (`fallback: true`).
- **No unpriced excess-OA energy on the generic minimum (#298).** On the generic 20% minimum,
  `outdoor_air_fraction` carried `oa_cost_excess_*_kbtu` (and Btu per cfm) figures that the cost
  estimator and the RCx report decline to price without `min_oa_pct`. They are now null there;
  with a configured `min_oa_pct` nothing changes.
- **RCx issues name a cause and a readable title (#298).** An issue from a rule with no packaged
  recommendation had an empty `cause` and a title such as "G36 afdd" or "Hw plant deltat". The
  cause is now taken from the finding's summary (for G36 AFDD, the worst fault condition), and
  the title spells out the rule's words ("G36 fault detection", "Hot-water plant delta-T").
- Path redaction no longer garbles normal text: the `refused_licence` message reads
  "non-commercial / no-derivatives" again (not "non-commercial <path> no-derivatives"), and an
  internal error shows one `<path>`, not `<path><path>` (#302).
- **A selection that matches nothing is `not_found`, not a clean "0 faults" (#296).** An unknown
  equipment id (`AHU-1` where the units are `AHU__economizer` ...), point role or equipment class
  gave a success such as "59 rules ran on 0 equipment, with 0 fault findings", "ranks 0 issues"
  or "0 points". Every capability that takes `equips` / `equip`, `roles` / `role` or
  `equip_class` now answers `not_found`, naming the unknown values and the facility's stored
  ones. An `equips` id the run config runs no rule on is `not_found` too (and no longer widens
  the run to every unit of a class the config lists by name). A window with no stored sample is
  `not_found` naming the stored span for `rule_run`, `finding_detail`, `rcx_report`, `drift` and
  `sensor_trust`; `coverage` still answers it with 0% and a notice.
- **`camber mcp --config` is checked at startup (#296).** A config `camber run` refuses (one
  trailing comma, a list, an empty file, YAML without PyYAML, an unknown rule or parameter, a
  facility the store does not hold) was silently ignored: the tools ran at defaults and said "No
  run config names this facility", and a labelled fault could disappear. A suffix other than
  `.json`, `.yaml` or `.yml`, a config that names no facility or one that is not allowed are
  refused too. The server now stops with one line naming the file and the problem (exit 2).
- **A config's `source.store` no longer overrides `--store` (#296).** Run tools read the
  config's store while the others read `--store`. A config whose store is not the `--store`
  directory (any spelling of the same directory is fine) is now refused at startup.
- **MCP and CLI papercuts from the 0.110.0 acceptance test (#304).**
  - `camber mcp --store ""` is refused instead of reading the current directory, and
    `--min-trust -inf` (or `-1e3`) gets the "from 0 to 1" message instead of "expected one
    argument".
  - `rule_run`'s headline no longer counts rule x equipment pairs as rules: it says how many
    rules were run, how many of them gave findings, and how many rule-equipment pairs (over how
    many rules) were skipped for missing inputs.
  - `drift` always states how many detector checks declined, so "0 equipment were not evaluated"
    no longer reads as everything judged.
  - `supply_air_reset` without outdoor air temperature says what its warn is about ("COLD SAT
    MOST COOLING HOURS; RESET NOT EVALUATED (no OAT)") instead of only "RESET NOT EVALUATED".
  - A `rule_run` call naming a drift detector, or any value outside a long list of allowed
    values, gets a short error with the closest names (and "drift detectors run with the drift
    capability") instead of a 3 KB list of every rule.
  - `rcx_report` issues list the `finding_ids` behind them (and each rule once), so the
    `plan_site_visit` prompt's `finding_detail` calls no longer need a guess or an extra
    `rule_run`.
  - `mv_summary` says what unit a saving is in (the meter's own units unless the run config has a
    `units` block) and, when the baseline fails G14 acceptance, that the saving is for
    information only, in the text and in a notice. M&V summaries print "undefined" instead of
    "nan" for a statistic that cannot be computed, and a baseline that fails acceptance says
    which criterion it failed.
  - Model-facing text no longer cites CAMBER's own module paths or release and issue tags
    ("(camber/oafraction.py)", "(0.98, #85)").
  - The heat-pump room-imbalance finding is on `<fleet>`, like every other fleet rule's, not
    `fleet`; an `equips` of `<fleet>` gets a `not_found` that explains a fleet finding.
  - `coverage` and `sensor_trust` say what their coverage is of (the whole window, or the
    point's own span or running hours), so one point reading 35% in one and 100% in the other
    is explained; the `data_quality_review` prompt says so too.
  - `camber mcp` keeps matplotlib's font cache in a temporary directory removed at exit (unless
    `MPLCONFIGDIR` is set): an `rcx_report` call wrote it under `$HOME`.
  - docs/MCP.md no longer says the tools "read trends" (none returns time-series values), and
    docs/CLI.md says that `datasets ingest` takes the cache lock only while it writes to the
    cache, as it always has.
- **The trust floor and `sensor_trust` read one score (#311).** `camber mcp` declined checks on
  inputs that `sensor_trust` rated trusted (CO2 trends logged in bursts scored 0.95-0.99 there
  and about 0.14 in the gate), so it withheld findings `camber run` reports while its notice
  pointed at a tool that contradicted it. The gate now scores each input on its equipment's
  whole run frame (every point at the run's resample, over the run's window), and
  `sensor_trust` reads those same frames with the same function, so a check is declined exactly
  when `sensor_trust` (same window) puts an input `below_floor`. A trend logged in bursts now
  shows its real coverage on that grid in `sensor_trust`. The decline names each input with its
  score ("declined -- untrusted input(s): co2 (trust 0.14)"), and so does the notice. The
  labelled datasets give the same findings through `camber mcp` and `camber run` as before.
- **A configured sensor reference reaches the triage tools (#313).** A supply-air sensor reading
  3 °F high against its duct logger scored 0.999 "trusted" in `sensor_trust` (an offset has no
  shape in the trend), and `rule_run` and `finding_detail` reported the leaking-valve fault it
  probably caused with no word of the sensor, so a model following `triage_ahu` would send a
  technician to a valve that is likely fine. Now:
  - `sensor_trust` lists every comparison of `report.rcx.references` under `references` --
    bias (the point minus the reference, so positive reads high), RMSE, correlation, paired
    samples, the overlap window and a verdict -- with a fact for each, a passing one too, so a
    reader can cite that a sensor was checked. A comparison at warn or fault makes the point
    `suspect` (flag `reference_warn` / `reference_fault`) whatever its score.
  - `rule_run` and `finding_detail` mark each finding whose rule reads that point as
    `conditional_on` the comparison, in the row, its fact and a notice ("treat each as a
    possible sensor problem first").
  - The RCx report's sensor-reference findings state the bias sign convention and the overlap
    window.
  - `triage_ahu`, `explain_finding`, `compare_equipment`, `sensor_trust_review` and
    `data_quality_review` tell the model to treat an input whose reference disagrees as a
    sensor problem first, and to rank that sensor above the fault.
- **MCP papercuts from the second acceptance round (#314).**
  - `too_large` reports the size of the reply it refused. Above about twice the cap it gave the
    size of the result alone (one copy of the data), so a page of 790 rows read 2.0 MB while one
    of 780 read 4.5 MB.
  - A rule's summary keeps the names it gives in its own words: a zone such as
    `/Drivers/Net/VAV_1` named after the unit no longer becomes `<path>`, and the server's own
    directories are still taken out.
  - `check_citations` splits sentences at line breaks, so a notice line no longer swallows the
    headline after it, and keeps a list marker (`1.`, `2)`, `-`) with its sentence: a numbered,
    fully cited draft passes, and the marker's number is not a figure to trace.
  - Prompt arguments are refused when too long (an id over 200 characters, a `claim` over 1,000,
    more than 20 `equips`) instead of being cut without a word.
  - Completion offers only facilities the store holds, does not offer an `equips` id twice, and
    `triage_ahu`'s `equip` offers no meters or weather.
  - `rule_params` says `min_oa_pct not set by default` (not "None %"), and no rule text a model
    reads cites a module path or a release or issue number.
  - The M&V acceptance notes state each figure as the summary does (one R² no longer reads 0.20
    and 0.19 in one sentence), and an undefined one reads "R2 undefined (must be at least
    0.75)", not "undefined < 0.75".
  - `camber run`'s `findings.json` holds the zone census's time-of-week profile as numbers (a
    column per count by time-of-week hour), not a printed table.
  - Protocol: an empty `method` is refused as empty; a message holding a lone surrogate is
    answered like every other invalid message, a notification too; every message refused as a
    whole is logged on stderr; a message with `"method": null` is no longer taken for a
    response and gets its one reply.
  - `camber mcp` no longer prints a traceback when the client closes stdout during a blocked
    write, no longer exits 120 when stderr is on a full disk, and no longer hangs at startup
    when the facility registry is a FIFO.
  - With `--allow-all-active`, the startup log names the research-only facilities it will
    refuse, counts them apart and no longer says they "use the config template"; a `--config`
    for one is warned about.
  - The default run of your own facility (no run config) no longer runs every rule on a class
    CAMBER does not recognise (a `LOGGER`) when the facility has recognised ones, so such a unit
    no longer gets a `warn` from a check that says it was not evaluated.
- **A rule parameter of the wrong type is refused, not run (#315).** The checks `camber run`,
  `camber mcp --config` and `rule_run`'s `params` make caught a NaN or out-of-range number but
  not the same value written as text: `min_oa_pct: "nan"` (or a bare YAML `nan`) turned two
  costed faults into "ok", `"500"` ran as a threshold, `fan_gate: "no"` ran as true and
  `start_hour: 7.5` ran as hour 8 while the facts said 7.5. Every rule parameter is now checked
  against its documented type: a number (not text, not true / false), a whole number for an
  hour, a count or a weekday (`7.0` runs as `7`), true / false for a flag, a listed word for a
  choice. A parameter's `basis` must be a non-empty text to keep a value outside the range.
- **`report.rcx.references` is checked before anything runs (#315).** A NaN `bias_warn` made a
  sensor 3 °F off "agree with its reference" and dropped the conditional mark from the findings
  that rest on it, and a text threshold failed `sensor_trust`, `rule_run` and `rcx_report` with
  an internal error. A threshold must now be a finite number (the bias and drift ones above 0,
  `min_correlation` from -1 to 1, `min_samples` a whole number of at least 1), and a sensor
  cannot be its own reference; `camber report` and `camber mcp --config` refuse such a config.
  `camber mcp --config` also refuses at startup (exit 2, one line) a reference to a unit or an
  equipment the store facility does not hold, or to a CSV file that does not exist, which before
  showed only as a banner in `sensor_trust`.
- **`camber mcp` usage mistakes are one line (#315).** A missing, malformed or out-of-range
  option now prints one line on stderr (exit 2), as docs/CLI.md says, instead of the usage block
  and the error; `camber mcp -h` shows `[--config PATH ...]` for the repeatable option.
- **`check_citations` returns the whole corrected text (#320).** With `detail: true`, its
  `verified_text` was cut at 2,000 characters, mid-sentence and with `truncated: false`, so a
  fully supported triage of normal length lost its last sentences without a word. The tool's
  own output (`verified_text` and the sentences) is now never cut. The 2,000-character cap
  still applies to values from the data, and a result with a value cut by it now has
  `truncated: true` and a notice saying how many values were cut.
- **A rule parameter of the wrong shape is refused (#320).** A config with `null` where a
  parameter has a value by default, a list (`[7]`) or an object where it takes one value, or one
  value where it takes a list (`occupied_days: 3`) was accepted at launch; every call then failed
  as `invalid_params` with Python error text, which no argument the model sends can fix.
  `camber mcp --config`, `camber run` and `rule_run`'s `params` now refuse it with the same one
  line as the other type errors (`'start_hour': null is not a whole number (...)`). A parameter
  whose default is null still accepts null.
- **Sensor reference edges (#320).** In `report.rcx.references`, a warn level above the fault
  level (`bias_warn` 5 with `bias_fault` 1, which rated a +3 bias "fault") and a value that is
  not an object (`0`, `null`, `[]`, read as "no references") are now refused at launch. A
  reference that starts but cannot be compared (its equipment has no data for the role) is now
  named in a notice of `rule_run` and `rcx_report` too, not only of `sensor_trust`.
- **The trust floor gates an input a rule cannot judge without (#320).** `dcv_verification`
  was judged on an outdoor-air flow below the trust floor, because that input is optional (a
  zone's CO2 alone is joined to its air handler), although the verdict is about that very
  signal. The signal a check is judged on now gates it like a required input: the OA flow (else
  the OA damper) of `dcv_verification`, the OAT of `outdoor_air_fraction`, the wet-bulb source of
  `condenser_water_reset` and `cooling_tower_approach`, and the fan signal of
  `night_weekend_setback`. At the default floor no finding of any catalog dataset changes.
- **Equipment named like the fleet is not taken for it (#320).** A unit stored with the id
  `<fleet>` was shown like the fleet pseudo-equipment of a fleet rule's finding, and
  `finding_detail` read `...@‹fleet›` as the fleet, so the finding id of a unit stored as
  `‹fleet›` returned another unit's finding (or `not_found`). Only a fleet rule's `<fleet>` is
  now shown as such (a unit with that id is shown defanged like any name), and the `‹fleet›`
  spelling is taken for the fleet only in a fleet rule's finding id.
- **Counts and wording (#320).** `rule_run`'s notice about findings that rest on a disagreeing
  sensor reference counted the findings on other pages as "listed" (with `limit: 1` it said 2
  listed); it now counts the page and names the other pages apart. A new fact (`C1@rule_run`)
  names the conditional results the page does not list, a hidden `ok` included, so the notice can
  be cited. `sensor_trust` says "Of 11 matching points" for a selection of equipment or roles
  rather than "Of 11 points on" the facility. docs/SENSOR-HEALTH.md said `longest_flat_hours` is
  always reported; it now says when it is null (a role with no duration limit, a fan-dependent
  point read without a fan gate, a plant point that never ran), and the output schema says so too.
- **A `basis` of invisible characters is empty (#320).** A rule entry's `basis` holding only a
  zero-width (or other invisible) character counted as a stated reason and kept an out-of-range
  value; it is now refused like an empty one.

- `camber mcp` no longer exits when a request holds a lone UTF-16 surrogate escape (such as
  `"\ud800"`) in its id, method, tool name, prompt argument or anywhere else: the request is
  refused with an Invalid Request error (-32600) and the server keeps serving. More generally, a
  reply that cannot be serialised or written is now logged on stderr and answered with an Internal
  error (-32603) instead of ending the session (#289).
- **`camber mcp` stops work that timed out, was cancelled or whose client has gone, and answers
  other requests while a call runs (#292).** The server handled one request at a time, so a
  `ping` or `server/discover` waited up to a minute behind a long `rule_run`; it ignored
  `notifications/cancelled`; and a call that hit its time cap answered `timeout` while it went on
  running, so each retry added another (three retries on a large facility kept about 4.5 cores
  and 10 GB busy for over five minutes; six timeouts under a short cap reached 32 GB). Closing
  stdin waited for the call in flight to finish, and a server with abandoned scans could crash
  (SIGSEGV) on exit. Now each tool call runs in a worker process that is killed, releasing its
  CPU and memory, on a timeout, a cancel, or when the client closes stdin or stdout; at most
  `--max-concurrent` calls (default 2) run at once and `--max-queued` (default 8) wait, and a call
  beyond that gets the retryable tool error `busy` at once. Every other message is answered
  immediately, so replies may come out of order (each with its own id); a cancelled call gets no
  reply; the server exits within about a second of stdin closing. Results are unchanged. Using
  `McpServer`, `serve` or `serve_stdio` from Python works as before, in-process, unless you pass
  `workers=WorkerPool(...)`.
- **The trust floor no longer withholds healthy plant checks, and says when it declines one
  (#297).** A condenser-water temperature or chiller approach trended only while the chiller
  runs (blank while it is off) scored about 0.39 for "low coverage", so under the default trust
  floor of 0.5 every `camber mcp` and `trust_gate` run declined the plant checks that read it.
  On the two synthetic chilled-water plant sets this dropped 5 of the command line's true
  detections, and the RCx report ranked 1 issue instead of 6. A plant point is now scored on the
  share of running samples it holds (flag `logged_while_running`, `SensorTrust.run_coverage`),
  and a cooling tower is judged on its running samples, gated on its condenser-water flow. A
  rule's trust gate now finds the chiller's status or power even when the rule does not read
  them. With the default floor, `rule_run`, `drift` and `rcx_report` now give the same results
  as the command line on every labelled synthetic set. Whenever the floor declines checks,
  these three capabilities add a NOTICE with the count, the rules and the untrusted inputs, and
  their summary gives the count. The benchmark gates are unchanged.
- **`rcx_report` honours `top_n` (#299).** Every issue came back whatever `top_n` said (73
  issues and 1.2 MB on one fleet). It now returns the `top_n` best-ranked issues (default: the
  config's, else 8; up to 200), with only their facts and sections; `offset` gets the next ones.
  The KPIs still count every issue.
- **`truncated` is set whenever a list was cut (#299).** `coverage` cut `gaps` to 500 with a
  notice but left `truncated` false. Every list a capability cuts now sets the flag and adds a
  notice, by construction.
- **The 2 MB cap bounds what is sent (#299).** It was measured on the result's data alone, while
  a `camber mcp` reply carries that data twice plus the facts, so replies of 3-4 MB passed. The
  registry now counts the whole result (data, summary, facts and notices) and `camber mcp` the
  whole reply; a larger one is `too_large`.
- JSON-RPC error replies to a request whose id cannot be read (a parse error, a batch, a line over
  1 MiB, a bad id) now carry `"id": null`. The official MCP SDK dropped these replies before, so
  the client waited until its own timeout (#300).
- `camber mcp` refuses a request object that repeats a key at any depth (-32600) instead of
  taking the last value, so a proxy that reads the first key cannot see a different method, id or
  protocol version from the one served (#300).
- A request with an integer too long to read (more than 4300 digits) is now an Invalid Request
  (-32600), not a misleading "Invalid JSON" parse error (#300).
- A second `initialize` is refused (-32600) and the session keeps its negotiated version;
  `initialize` now needs a `capabilities` object, and `clientInfo` must be an object when sent, in
  both protocol revisions, matching the stateless path's rules (#300).
- docs/MCP.md lists the JSON-RPC error codes as the server returns them and names the required
  2026-07-28 `_meta` keys (#300).

- **A store or data problem is a tool error a model can act on, not an internal error** (#301).
  Over MCP these were JSON-RPC -32603 "internal error" responses; they are now `isError` tool
  results with the new, documented code `unavailable`, whose data says whether a retry will
  likely work and names the facility:
  - a truncated, empty or non-Parquet part file, or an unreadable facility directory, affects
    only that facility (`unavailable`, not retryable). Calls on other facilities keep working,
    and `facilities` still lists every facility, marking the unreadable one.
  - a read while `camber datasets ingest --force` rewrites a facility no longer fails reads of
    other facilities: a read of one facility lists only that facility's files, and the catalog
    scan goes one facility at a time. A part file that vanishes during a read is retried once,
    then reported as `unavailable` (retryable).
- `coverage` with a `start` after the last stored sample (or an `end` before the first) returns
  an empty result with a notice instead of an internal error; `sensor_trust` adds the same
  notice. A date the calendar does not have, such as `2025-02-30` or `2025-13-45`, is
  `invalid_params` in every capability that takes a `start` / `end` (#301).
- An integer argument given as a whole-number float (`"limit": 5.0`, `"offset": 25.0`) runs as
  that integer. In the paged tools (`facilities`, `points`, `coverage`, `sensor_trust`,
  `rule_run`, `rcx_report`, `rule_params`, `check_citations`) it passed validation and then
  failed as a JSON-RPC internal error naming a Python exception (#308).
- `camber mcp` reads each `--config` once, when it starts, and serves that copy for the life of
  the process. An edit to the file while the server ran was read by the next call without the
  startup check: it could point `rule_run` at another store while `points` read `--store`, or
  (one stray comma) drop the tuned rules with the false notice "No run config names this
  facility" (#308).
- The `--config` startup check also reads the `report.rcx` section the way `camber report` does
  (a `references` entry naming no CAMBER role made every `rcx_report` call an internal error), and
  a ventilation zones file that cannot be read now exits 2 with one line naming the config, not 1
  with a bare `[Errno 2]` (#308).
- A message line over 1 MiB is refused with its own `id` when the line's first bytes name one,
  so the client's call ends at once; with `"id": null` the official SDK waited for its own
  timeout (#308). A line that repeats its top-level `id` is answered with `id: null` (#318).
- `camber mcp` worker processes, from the second round of the 0.111 acceptance test (#309):
  - A worker that ended while idle (a crash, or the system stopping it for lack of memory) is
    replaced before a call is given to it; the next call no longer fails with -32603. A call
    whose worker ends under it is the retryable tool error `unavailable`, not -32603.
  - `--max-concurrent` is 1 to 64 and `--max-queued` 0 to 1024, in plain digits. A huge value
    started one thread per slot and crashed with a traceback or used memory without bound; it
    now exits 2 with one line, as other launch mistakes do.
  - `SIGTERM` and `SIGHUP` stop the server like Ctrl-C (a log line, calls stopped, exit 143 or
    129) and remove matplotlib's temporary directory, which they left behind. The directory of
    a server that was killed with `SIGKILL` is removed by the next `camber mcp` to start.
  - Killing a worker also kills the processes it started: matplotlib's font scan
    (`system_profiler` on macOS) no longer runs on for about 11 s after a cancel, a timeout or
    the server's exit.
  - A `tools/call` that reuses the id of a call still in progress is refused (-32600); both used
    to run and be answered with the same id, and one cancel stopped both.
  - A timeout says the call "was stopped" (the worker is killed, not abandoned) and comes within
    about a second of the 120 s cap instead of 5 s after it.
  - docs/MCP.md says that the time cap counts from when a call reaches a worker, how long a call
    can wait in the queue behind long ones, and a wait over a second is logged.
- **`finding_detail` states the share the rule states, of the samples the rule judged (#310).**
  "N of the M samples it judged" divided by every sample in the window for most rules, so the
  same flagged hours read as two different citable shares: a dirty filter "66.7 %" in
  `finding_detail` where `rule_run` said 100 % of fan-on samples, a low delta-T loop "32.7 %"
  where it said 100 % of running hours. `judged_samples` is now the rule's own mask (fan-on,
  occupied, running, both valves shut ...), a new `judged_basis` names it, and the fact reads
  "(2688 of 2688 judged fan-on samples, 100.0%)". A finding that judges named
  conditions (each coil of `leaking_valve`, each G36 fault condition, each failure mode of a
  reset, excess OA and under-ventilation) states each condition's share of its own samples as a
  fact, matching the summary. The flagged hours, the spans and every finding are unchanged.
- `leaking_valve` calls its denominator "both-valves-shut samples", not "closed hours"; the
  `rule_run` headline says how many rules "returned a result (ok included)" rather than "gave
  findings"; and `finding_detail`'s `chart.roles` names a diagnostic scatter's two axes instead
  of `[]` (#310).
- **`camber mcp` answers every call it accepted, even when its workers are killed (#316).** A
  call handed to a worker that died while it was still starting (the system stopping several
  processes at once for lack of memory) got no reply at all, was logged nowhere, and kept its id
  "in progress", so a retry with the same id was refused. Such a call is now tried once more on a
  fresh worker and, if that one dies too, answered `unavailable` with `"retryable": true`.
- **A stray temp directory no longer stops `camber mcp` from starting (#316).** A directory named
  `camber-mcp-mpl-<a number too large for a process id>-...` in the temp directory made every
  start crash with a traceback; on a shared `/tmp` any local user could create one. Such names
  are now skipped, and the startup sweep of leftover directories can no longer fail the start.
- **`camber mcp` workers and signals (#316).** `SIGTERM`, `SIGHUP` and Ctrl-C now end a server
  that is blocked writing to a client that stopped reading (it waited until the client closed
  the pipe). A worker killed from outside no longer leaves the font-scan process it started
  running for a few seconds. With stderr closed or unwritable the server exits 0 again (143, 129
  or 130 after a signal), Ctrl-C while the server is starting or stopping exits 130 without a
  traceback, and a second Ctrl-C no longer interrupts the cleanup.
- **Accuracy and rendering fixes from the third acceptance round (#318).**
  - A check the trust floor declines now names every input it reads that is below the floor,
    optional ones included ("declined -- untrusted input(s): co2 (trust 0.02), oa_airflow (trust
    0.01), occupancy (trust 0.02)"), not only the required one. An optional input below the
    floor on its own still does not decline the check; the docs now say which inputs decide.
  - `rule_run`'s notice about findings that rest on a disagreeing sensor reference counts the
    findings it lists, and says how many more results (such as an `ok` verdict) rest on that
    input but are below `min_severity` and not listed. It used to say "3 finding(s)" next to a
    list of two.
  - `finding_detail` on a G36 AFDD finding states only the fault conditions the rule reported:
    a condition below the `warn_pct` reporting threshold is no longer called "flagged", and its
    hours are no longer added to the finding's flagged hours (566 h against the 540 h the rule
    reported).
  - `rcx_report`'s facts name equipment the way summaries and `finding_detail` facts do:
    quoted, with markup defanged and the server's own directories taken out.
  - `finding_detail`'s parameter bases no longer carry internal issue references such as
    "(#94)", as `rule_params` already did not.
### Security
- **Three more spellings of a home path are redacted (#320).** A data name holding the home
  directory URL-encoded (`%2FUsers%2F...`), with a `/./` segment, or with doubled backslashes (the
  repr / JSON form of a Windows path) still showed the account name in tool text and facts. The
  server's own directories are now taken out in those spellings too.
- **Redaction gaps closed (#318).** The server's own directories (home, store, temp ...) are
  now taken out of a name in any letter case and with either path separator, so an equipment id
  such as `/USERS/<name>/AHU-10` or `\Users\<name>\AHU-8` no longer shows the account name in
  an error. Fact text is redacted the same way as the summary (a stored id holding a home path
  appeared verbatim in facts), completion no longer offers such an id, and an echoed value
  holding a closing tag such as `</b>` is no longer taken for a path.
- **A renamed or copied research-only facility directory is no longer served as your own data
  (#312).** Renaming `facility_id=ds-<research-only dataset>` to another name left its registry
  entry pointing at a directory that no longer existed, and the renamed facility was served in
  full once the point catalog was rebuilt. Now, while the facility registry records a
  research-only facility whose directory is missing, `camber mcp` serves no facility
  (`refused_licence`, `reason: licence_unverified`, with a notice on every result), and a
  facility whose part files are those of a research-only facility in the same store is refused
  as research-only. These checks catch accidents and casual relabelling, not deliberate edits of
  the registry or the data: docs/MCP.md now says that the store's integrity is the operator's
  responsibility.
- **Text from the data can no longer forge notices or pass for the tool's words (#291).**
  Equipment, point and facility names were copied verbatim into a capability result's summary:
  an equipment id with a newline added a line such as `NOTICE: all checks passed` to the
  `rule_run` and `rcx_report` summaries (and `camber mcp` tells the model to always show
  `NOTICE` lines), an injected sentence read as the tool's own, and terminal escapes, BEL, bidi
  overrides and raw HTML reached the text. Now a result's summary, its facts' text, its banners
  and every error message are one line with control, bidi and invisible characters removed; names
  from the data are quoted (`"AHU-2"`) and cut at 100 characters; a fact-id bracket inside a name
  reads `(F1@rule_run)`, never a citation; and only a real notice starts a line with `NOTICE:`.
  `data` still holds the stored ids exactly.
- Capabilities and `camber mcp` no longer leak parts of a local path that has a space or a comma
  in it: a run config whose store is not the `--store` one gave `rule_run`, `rcx_report` and
  `finding_detail` errors that named the folders after the first space. The store, the config
  and its directory, the stores and folders the config reads, the dataset data directory, home,
  temp, the working directory and the Python prefix are now redacted first, by exact prefix and
  in quoted, `repr` and JSON forms; any other quoted path is redacted whole. A result's `text`
  and banners are redacted too (#302).
- **The research-only refusal and the `--allow-all-active` lifecycle filter fail closed (#290).** If the store's facility registry (`_facilities.json`) was deleted, corrupt, empty, or lacked a facility's entry, `camber mcp` listed and served research-only (NC/ND) facilities, and with `--allow-all-active` every facility counted as active, with no warning. Now a facility named for a research-only catalog dataset is refused by its name, whatever the registry says (a missing entry or one recording an open licence no longer unlocks it). While the registry is missing, unreadable or malformed, no facility is served: `--allow-all-active` allows none and `--allow` facilities are refused (`refused_licence`, `reason: licence_unverified`), with a warning on stderr and a notice on every tool result. A catalog-dataset facility with no registry entry is refused and not counted as active. `camber mcp` also warns at startup about each `--allow` facility it will refuse, such as research-only data. The `facilities` tool reports these as `withheld_unverified`. A store of your own data with no registry is served as before.

### Changed
- Fact ids change: the token in `F1@rule_run-1a2b3c` now also depends on the run config (its
  content, not its path), the trust floor and the stored data of the facilities the call reads,
  not only on the call and the allowlist. Two servers that differ in `--config`, `--min-trust` or
  store contents, or one server before and after an ingest, no longer give the same id to
  different values, so their facts merge for `check_citations` without a clash and a draft
  written from one server's facts no longer passes against the other's. The same call on the
  same server and unchanged data keeps its ids, also across a restart (#295).
- **Ingest refuses ids with control, bidi or invisible characters (#291).** `write_long` /
  `write_role_frame` (and so every ingest) raise a `ValueError` for an equipment id, equipment
  class or role that holds a newline, tab, ESC, NUL or other control character, a bidi override,
  a zero-width character or a line separator, naming the id. Ids already stored are read as
  they are; rename such an id in the mapping before re-ingesting. Valid ids are unchanged.
- Capability date arguments accept only valid months (01-12), days (01-31), hours (00-23) and
  minutes (00-59) in their schema pattern (#301).
- A handler's `OSError` is reported as `unavailable` rather than `internal`, and a failed store
  read raises `camber.store.parquet_store.StoreReadError` (an `OSError`) instead of pyarrow's
  `ArrowInvalid` or a bare `FileNotFoundError` (#301).
- **`camber mcp --config` can be given once per facility (#296)**, so `--allow-all-active` can
  serve tuned results for every facility; two configs for the same facility are refused.
  `CallContext.config` takes a tuple of paths as well as one.
- **A catalog-dataset facility with no `--config` runs with its dataset's packaged config template
  (#296)**, the one `camber datasets config` writes (read from the package: nothing is fetched or
  written), with a banner and a startup line naming the dataset. At defaults the rankings misled:
  on one LBNL air-handler dataset the fault-free unit was issue #1. Pass a `--config` for the
  facility to use your own. `rcx_report` now also carries the "default parameters" banner when
  no config is used.
- Every `camber mcp` launch mistake (a bad store, `--min-trust` out of range, an empty `--allow`,
  a bad config) exits 2, like the argument parser's usage errors; it was 1 (#296).
- `camber run` and `camber mcp --config` warn about a top-level config key no run reads (a typo
  such as `"rule"` was silently ignored); keys starting with `_` are notes. Neither refuses the
  config (#304).
- `rule_params` rows carry `run_with` (`rule_run` or `drift`), and an `rcx_report` issue's
  `finding_ids`; the `equip` of a heat-pump room-imbalance finding is now `<fleet>`, so its
  finding id is `hp_room_imbalance@<fleet>` (#304).
- **The RCx report ranks a root sensor issue above the issues conditional on it (#313).** A
  sensor whose configured reference disagrees now ranks just above the first issue that rests on
  it, whatever their severities: the leaking-valve fault that a 3 °F supply-air offset explains
  was Issue 1 and the sensor Issue 3. In `camber report --layout rcx` and `rcx_report` alike.
- An `rcx_report` issue has a `heading`: the heading the report prints (the cause found, else
  the recommended action). `title` is the recommended action and `cause` the cause or the
  finding's figures, which is why neither was the heading for every issue (#314).
- One `offset` pages every list of a result; a short list whose rows are all on the first page
  now says so on a later one instead of "offset N is past its 1 row(s)" (#314).
- The `rcx_executive_summary` prompt tells the model to page the issues, so an issue
  conditional on a sensor fix beyond the first page is not missed (#314).
- `camber mcp` keeps a worker that needs a lot of memory for every call instead of replacing it
  after each one (#316). A worker was replaced after any call that took it past 1 GiB, so every
  call on a facility whose load alone passes that, even a one-row `points`, loaded it again (0.02 s
  became 2.5 s). Now it is replaced only after a call that takes it past 1 GiB and past twice
  the memory it held after its first call: an unusually large call. The first call a worker
  serves sets that level, so a worker may now keep what a large facility needs between calls.

- `rcx_report` runs the run config's `drift` section, as `camber report --layout rcx` does, so
  drift issues are ranked through MCP too. The fact of an issue that rests on a sensor that a
  configured reference (`report.rcx.references`) finds wrong now says it is conditional on that
  check (#297).
- `finding_detail`'s `violations` has `judged_samples` and, for an event rule, `events`, and
  its result has `violations_note`; a parameter entry can carry `fallback` (#298).
- **Capability results are model-sized by default (#299).** A plain `rule_run`, `rcx_report`,
  `sensor_trust` or `coverage` call on a 650-point fleet returned 0.3 to 1.2 MB, far more than a
  model's context. Every list now comes a page at a time -- `rule_run` 25 findings, `sensor_trust`
  100 points, `coverage` 50 rows per list, `points` 250, `facilities` 100, `drift` 25,
  `rule_params` 25 rules (10 with `detail`), `rcx_report` 8 issues and 20 appendix facts -- and
  takes `limit` (up to 1,000) and `offset` for more; a notice says what was left out and the call
  that gets it. Typical results are now 50 to 100 KB on the wire.
- **`check_citations` returns a compact verdict (#299).** 50 KB of text in gave up to 764 KB out.
  It now returns the counts per verdict and the sentences that fail; `detail: true` lists every
  sentence and adds `verified_text` (no longer returned by default). `cited` lists each id once.
- `rule_params` facts carry each parameter's name, default, unit and range, not its basis and
  calibration prose, which stay in `data` (#299).
- **A shorter MCP tool list (#299).** Tool descriptions are tighter, and `tools/list` leaves out
  the default `$schema`, the `annotations.title` that repeats the tool's title and the
  descriptions inside output schemas (46 KB to about 43 KB, with the new paging arguments). The
  JSON text block of a tool result is compact. See docs/MCP.md for why the result is still sent
  both as text and as `structuredContent`.
- `camber run`, `camber mcp --config` and `rule_run`'s `params` refuse a rule parameter that is
  not a finite number (`NaN` or infinity) or is outside its documented range (`camber rules params
  RULE`). A NaN minimum outdoor-air percentage turned every fault of the rule into "ok" with no
  warning, and 500 for a 0 to 100 % parameter faulted every unit. A value outside the range is
  kept when the rule entry's `"basis"` says where it came from (a calibration, the building's own
  sequence); the two packaged templates that use one (ornl-frp-vav, nist-heatpump-fdd) now give
  it (#308).
- A run config that repeats a key in one object (JSON or YAML) is refused, as MCP messages are;
  the last value silently won, which could move a config to another facility (#308).
### Added
- **Prompt-argument completion in `camber mcp` (#304).** The server declares `completions` and
  answers `completion/complete` in both protocol revisions: `facility_id` from the allowed
  facilities a call would read (never a research-only one) and `equip` / `equips` from the stored
  equipment of the facility already chosen, so a person starting a prompt no longer has to know
  the ids. `Registry.complete` gives the same to any adapter.
- **`pip install "camber-toolkit[all]"`** installs every optional runtime extra (not `dev` or
  `docs`) (#304).
- `camber datasets list` marks a dataset that needs an optional extra (`[needs xlsx]`, with the
  install command), and `camber datasets info` says whether it is installed, before anyone
  fetches it; docs/DATASETS.md marks `ornl-frp-vav` as needing the `xlsx` extra (#304).

## [0.110.0] — 2026-10-25

**0.110: an MCP server over a read-only capability registry (#275–#278).** `camber mcp` lets an AI client such as Claude Desktop query a store over stdio, speaking MCP 2026-07-28 and 2025-11-25. It sees 13 read-only tools and 8 prompts from the new `camber.capabilities` registry, which enforces one policy for every caller: a facility allowlist set at launch, research-only (NC/ND) data always refused, a trust floor, caps and time limits, cited fact ids checked against the facts, and no local paths in any message. Runs behind it are no-persist, a mode now also available as `run_config(persist=False)` and `camber run|report --no-persist`. The registry, the server and the capabilities are provisional.

### Added
- **The capability registry, `camber.capabilities` (#275), provisional.** One read-only core the
  MCP server and, later, the A2A server expose as thin adapters. A `Capability` has JSON Schema
  2020-12 input and output schemas and a handler returning a `CapabilityResult` (`data`, a `text`
  citing `[F1@ctx]` fact ids, `facts`, `banners`, `truncated`). `Registry.call` enforces the
  policy in one place: input validation, a required per-caller facility allowlist
  (`not_allowed`), research-only (NC/ND) data refused with no override (`refused_licence`),
  no-persist runs, a `min_trust` floor of 0.5, caps on rows (truncated with a banner), bytes
  (`too_large`) and run time (`timeout`), citations checked with `camber.agent.verify`, and any
  other failure reported as `internal` with no local path in the message. `default_registry()`
  carries the built-in capabilities (below); adding one is a single `_cap_<name>.py` module. See docs/CAPABILITY-REGISTRY.md.
- **A no-persist config run (#276).** `run_config(..., persist=False)` and
  `run_config_file(..., persist=False)` compute the same findings, drift and report but write
  nothing: the `faults` fold runs against an in-memory copy of the stored history and is not saved
  (`run.faults["persisted"]` is `False`), the config's `report.out_text` / `out_html` files are not
  written, nothing is listed in a workspace manifest, the store does not write back its catalog
  cache, and an M&V billing entry fetches no weather. `RunResult.persisted` says which it was. On
  the CLI: `camber run --no-persist` and `camber report --no-persist` (the `--out` file is still
  written). A run inside the private `camber._nopersist.no_persist()` scope is always no-persist;
  the capability registry uses it.
- **The first read-only capabilities and prompts (#278), provisional.** `default_registry()` now
  carries 13 capabilities for the MCP server: `coverage` (samples, interval, coverage and gaps
  per point and equipment), `sensor_trust` (trust scores and flags per point against the
  caller's trust floor), `rule_run` (the FDD findings of a facility, worst first, through the
  caller's run config or every built-in rule), `finding_detail` (one finding's metrics, the
  parameters it ran with, its flagged hours as time spans and which evidence chart applies),
  `rcx_report` (the RCx report as data with its fact index, no file), `mv_summary` (the M&V
  results of a configured project), `drift` (against a known-good baseline window or the
  configured frozen baselines), `dataset_search` and `dataset_entry` (catalog metadata;
  research-only entries are listed with their licence and a notice that their data is refused),
  `rule_params` (the rules and their tunable parameters) and `check_citations` (per-sentence
  verdicts of a drafted text against the facts of earlier results), besides `facilities` and
  `points`. Every result cites `F<n>@<capability>` fact ids, and nothing is written: runs are
  no-persist and a configured network weather fetch is left out of the RCx report. Eight
  prompts package workflows -- `triage_ahu`, `rcx_executive_summary`, `explain_finding`,
  `check_savings_claim`, `data_quality_review`, `compare_equipment`, `sensor_trust_review` and
  `plan_site_visit` -- each naming the capabilities to call, asking for cited fact ids and
  ending with `check_citations` on the draft. Every capability's output schema is an object
  schema (`Registry.register` refuses any other, as MCP 2025-11-25 allows only an object
  `structuredContent`), each `text` repeats the cited values next to the fact ids, and an
  unknown capability name is the new error code `unknown_capability`, no longer `not_found`.
  Fact ids are unique per call: the registry appends a token (a hash of the capability, its
  arguments and the allowlist) to each id's context, `F1@rule_run-1a2b3c`, so the same call
  gives the same ids and the facts of several calls never clash.
  See docs/CAPABILITY-REGISTRY.md.
- **`camber mcp`: a Model Context Protocol server over stdio (#277, provisional).** AI clients
  such as Claude Desktop and the `claude` command line start it as a subprocess and see CAMBER's
  capability registry as read-only tools and prompts. It speaks both 2026-07-28 (stateless: the version and
  client capabilities in each request's `_meta`, `server/discover`, `resultType` on every result,
  `ttlMs` and `cacheScope` on the list results) and 2025-11-25 (the `initialize` handshake;
  2025-06-18 accepted too), so newer and older clients both connect. The store, the facility
  allowlist (`--allow FACILITY`, repeatable, or `--allow-all-active`, one of them required),
  `--config` and `--min-trust` come from the launch, never from the client. A refused or failed
  call the model can act on (a facility outside the allowlist, research-only data, bad
  arguments, nothing found, too large, timed out) is a tool result with `isError: true`; an
  unknown tool or a malformed request is a JSON-RPC error. Standard library only (the `mcp` extra
  is an empty marker); stdout carries protocol messages only, logs go to stderr. New package
  `camber.mcp` (`McpServer`, `serve`, `serve_stdio`, `build_context`, `launch`). See
  docs/MCP.md.

### Fixed
- **Capability errors keep no local path in their `data` either (#278).** `Registry.call`
  path-redacted a handler's `CapabilityError` message but passed its `data` through; it is now
  redacted the same way. An `invalid_params` refusal by the input schema no longer has its JSON
  pointers (`/facility_id: expected string`) mistaken for paths and redacted.
