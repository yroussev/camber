# Assisted point mapping

Getting BAS tags mapped to CAMBER's vendor-neutral `Role` vocabulary is the gate on everything else —
a rule can't run on a point it can't find. `camber.model.mapping.MappingProvider` resolves a tag by
alias or regex, and `camber.mapping_confidence` scores how sure that resolution is. `camber.mapping_assist`
adds the missing piece: when a tag **doesn't** resolve, propose the most likely roles.

```mermaid
flowchart LR
  token["BAS point token"]
  feat["FeatureSuggester"]
  ml["MLSuggester (ml)"]
  llm["LLMSuggester"]
  score["mapping_confidence re-score"]
  sugg["RoleSuggestion (ranked)"]
  review["review_unmapped list"]
  operator["operator confirms + edits mapping"]
  token --> feat
  token --> ml
  token --> llm
  feat --> score
  ml --> score
  llm --> score
  score --> sugg
  sugg --> review
  review -- advisory --> operator
```

*Any suggester proposes; the deterministic `mapping_confidence` re-score arbitrates; the operator applies.*

It is **advisory only, by construction** — it returns a ranked, human-confirmed review list and
**never mutates a `MappingProvider`**. A confirmed suggestion is applied by the operator editing the
mapping spec (`MappingProvider.from_dict`), the same boundary `camber.aso` keeps toward the BAS.

## One interface, three suggesters

Every suggester implements `suggest(token, *, series=None, unit=None, k=3) -> list[RoleSuggestion]`:

| Suggester | Dependency | Signal |
|-----------|------------|--------|
| `FeatureSuggester` | numpy / stdlib (always available) | tag string + unit + physical-range fit |
| `MLSuggester` | `scikit-learn` (`[ml]` extra, lazy) | learned char-n-gram classifier |
| `LLMSuggester` | an injected LLM callable (the [agent](AGENT.md) seam) | model proposal, deterministically re-scored |

A `RoleSuggestion` is `token, role` (always a valid `Role` value), `confidence` (0..1), `basis`
(`initials`/`ngram`/`edit_distance`/`unit`/`range_fit`/`combined`/`ml`/`llm`), `rationale`, and `as_dict()`.

## Baseline — `FeatureSuggester`

Dependency-light and always on. It scores every `Role` from three signals:

- **String match** — tag initials (`SAT` → `supply_air_temp`) and per-word edit distance
  (`difflib.SequenceMatcher`) against each role slug. This is the dominant term.
- **Unit compatibility** — a `ROLE_UNIT` table (degF/degC → temp, `%` → valve/damper/speed, cfm →
  airflow, kW → power, gpm → flow, inH2O → duct static, ppm → CO₂). A compatible unit gives a small
  bump; a **known-incompatible** unit strongly demotes the role.
- **Physical-range fit** — if a `series` is given, `sensorhealth.range_violation_frac(series, role)`
  demotes any role whose physical bounds the data violates (a 500 °F "supply air temp" falls away).

```python
from camber.mapping_assist import suggest_roles

for s in suggest_roles("AH1_SAT", unit="degF", series=sat_series, k=3):
    print(s.role, round(s.confidence, 2), s.rationale)
# supply_air_temp 0.93  'AH1_SAT' matches the initials of supply_air_temp; unit 'degf' fits ...
```

## Review the unmapped tags — `review_unmapped`

The front door for a whole tag set. It reuses `mapping_confidence.review()` to find the tags that
**don't** resolve, attaches ranked suggestions to each, and returns a human-confirm artifact:

```python
from camber.mapping_assist import review_unmapped

rev = review_unmapped(
    tokens, mapping, series_by_token={"VAV12_DmprPos": damper_series}, units={"VAV12_DmprPos": "%"}
)
rev["n_unmapped"]  # how many didn't resolve
rev["review_list"]  # [{"token", "suggestions": [RoleSuggestion.as_dict(), ...]}, ...]
```

The mapping is **never modified** — you review `rev["review_list"]`, then apply the confirmed roles by
editing your mapping JSON.

## Optional learned backend — `MLSuggester`

Behind the `[ml]` extra (`pip install camber-toolkit[ml]`), imported lazily so the core stays pure. A
character-n-gram `scikit-learn` classifier. It ships **no pretrained weights** (clean-room); you train
it on your own labels or bootstrap from an existing mapping:

```python
from camber.mapping_assist import MLSuggester

ml = MLSuggester.from_mapping(mapping)  # labels from mapping.aliases
# or: MLSuggester().fit([("AH1_SAT", "supply_air_temp"), ("RTU3_OAT", "oat"), ...])
suggest_roles("AH9_SAT", suggester=ml)
```

Learned predictions pass through the **same physical-range gate** as the baseline, so a confident
guess the data contradicts is still demoted. Accuracy scales with how many labels you provide; the
numpy `FeatureSuggester` remains the always-available floor.

## Optional LLM backend — `LLMSuggester`

Reuses the provider-agnostic [agent client](AGENT.md) — **no new dependency, no vendor named**. The
model sees the tag, its unit + bounded sample stats, and the whole `Role` vocabulary, and proposes
roles. Then the deterministic layer disposes:

- every proposal is validated `Role(value)` — out-of-vocab proposals are dropped;
- every surviving proposal is **re-scored** through `mapping_confidence.score_token`, so a
  physically-inconsistent suggestion can't outrank a good one.

```python
from camber.mapping_assist import LLMSuggester
from camber.agent import client_from_callable

client = client_from_callable(lambda p, **o: my_llm.complete(p))  # you own the SDK
suggest_roles("AH1_SAT", suggester=LLMSuggester(client), series=sat_series)
```

The LLM proposes; the deterministic layer is always the arbiter. See [AGENT.md](AGENT.md) for the
seam and its no-vendor/no-network guarantees.
