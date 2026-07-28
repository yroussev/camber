# Grounded explanation & Q&A

`camber.agent` turns CAMBER's deterministic results into plain-language, **cited** explanations and
natural-language Q&A — strictly grounded in the analytics layer, provider-agnostic, and fully useful
with **no LLM wired at all**.

Three principles, enforced in code (`tests/test_agent_readonly_guard.py`):

1. **Grounded** — an answer may only cite facts CAMBER produced, and every number it states must be
   traceable to one. Ungrounded content is flagged (and, in strict mode, repaired away).
2. **Provider-agnostic** — no LLM SDK is imported, no vendor is named, and nothing here touches a
   network. You inject a `complete(prompt, **opts) -> str` callable; the deterministic layer works
   without one.
3. **Read-only** — the agent explains and answers; it never writes back to a BAS.

## The grounding surface — `Context` of `Fact`s

A `Fact(id, kind, equip, text, data)` is one citable statement built from an existing deterministic
object. Its `text` comes straight from that object's template surface (`Finding.summary`,
`Recommendation` title + action, `FaultCost` basis/dollars) and its `data` is the object's `as_dict()`.
Ids are **order-stable and deterministic** (`F1`, `C1`, `R1`, `G1`, … per kind) so citations are
reproducible.

```python
from camber.agent import build_context

ctx = build_context(findings, loads=loads, price=price)
print(ctx.to_prompt_block())
# [F1] (finding, AHU-1) Both coils open 20% of hours.
# [C1] (cost, AHU-1) Estimated annual cost ≈ $2,897 (8,802 kWh, 1,314 therms; reheat-gas+paired-cooling).
# [R1] (recommendation, AHU-1) Lock out simultaneous heating and cooling. ...
```

`to_prompt_block()` is the **only** thing ever put in front of a model — it cannot invent equipment
or metrics that aren't in the fact set. Builders exist for every deterministic layer:
`facts_from_findings` (finding + cost + recommendation + root-cause), `facts_from_run`,
`facts_from_scorecard`, `facts_from_completeness` (why a rule *couldn't* run), `facts_from_history`
(**bounded stats only**, never raw series), and `facts_from_mapping`.

A **cost** fact never fabricates a dollar figure: when the estimate is uncosted it states the basis /
missing input ("No dollar figure — no cost model for rule 'unmet_setpoint_hours'.") instead of a number.

## Explain a finding — `explain`

```python
from camber.agent import explain

g = explain(findings, loads=loads, price=price)  # no client -> deterministic template
print(g.text)  # grouped by equipment, every claim cited [F1]/[C1]/[R1]
print(g.source)  # "template"
print(g.grounded)  # True
```

With **no client**, `explain` returns the deterministic `templates.explain_from_facts` answer — fully
grounded, no LLM. This is also the regression oracle the LLM path is verified against.

## Ask a question — `ask`

```python
from camber.agent import ask

ask("what should I do about AHU-1?", ctx).text  # -> [R1] Lock out simultaneous heating ...
ask("what will this cost?", ctx).text  # -> [C1] Estimated annual cost ≈ $2,897 ...
```

Without an LLM, `ask` routes the question over the fact set by keyword / equipment / kind and answers
honestly ("I don't know — no matching fact …") when nothing applies. It accepts a prebuilt `context`,
or builds one from `findings` / `run` / `read_api` / `scorecard` / `completeness` / `mapping_review`.

## Wiring an LLM — the provider-agnostic seam

CAMBER ships **no** provider. You wrap *your* SDK in a callable and inject it — exactly as
`ingest.haystack` takes an injected `his_read` transport:

```python
from camber.agent import client_from_callable, explain

# the contract is: complete(prompt: str, **opts) -> str   (opts: system, max_tokens, temperature)
client = client_from_callable(lambda prompt, **opts: my_provider.complete(prompt))

g = explain(findings, client=client, loads=loads, price=price)
g.source  # "llm"
g.cited  # ["F1", "R1"] — ids that resolve to real facts
g.grounded  # True only if every cite resolves AND every number is traceable
g.flagged  # problems found (unknown-citation / uncited-number)
```

The model is prompted with the fact block and instructed to cite every claim with its `[id]` and to
say it doesn't know when a fact isn't present.

## Verification — how grounding is enforced

Every generated answer runs through `verify.check(text, context, *, strict=True)`:

- an answer is **grounded** when every `[id]` it cites resolves to a real fact **and** every number it
  states appears in a cited fact's text or data (number-*traceability*);
- **strict** mode (default) *repairs* the text — dropping sentences that state an untraceable number
  and stripping unknown `[id]` tokens — so a partly-hallucinated answer degrades to its grounded
  subset instead of misleading;
- if strict verification empties the answer, `explain`/`ask` **fall back to the deterministic
  template** — you always get a useful, grounded response.

`Grounded(text, cited, facts, grounded, flagged, source)` — `source ∈ {"llm", "template"}` tells you
which path produced the answer.

## Guarantees

- **No vendor, no network** — `tests/test_agent_readonly_guard.py` parses the AST of every
  `camber/agent/*.py` (and `mapping_assist.py`) and fails if any LLM SDK or network client is imported.
- **Read-only** — the same guard fails on any write/command/actuation symbol.
- **Useful with zero LLM** — the template layer is a complete, grounded implementation on its own.

Used by [assisted point mapping](MAPPING-ASSIST.md)'s `LLMSuggester`, which proposes roles through this
same seam and re-scores them deterministically.
