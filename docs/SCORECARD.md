# Building health scorecard

`camber.scorecard` synthesizes FDD findings into the one-glance summary an owner or portfolio
manager reads: per-category scores (energy, comfort, ventilation, maintenance) and an overall grade.

```python
from camber.scorecard import build_scorecard

sc = build_scorecard(findings)
sc.overall_score, sc.overall_grade  # e.g. 90.0, "A"
for c in sc.categories:
    print(c.category, c.score, c.grade, c.n_faults, c.n_warnings)
```

Each category starts at 100; every actionable finding deducts by severity (`fault_penalty` /
`warn_penalty`), clamped to 0–100, graded A–F. The overall is a weighted mean across categories
(`category_weights`, equal by default). Rules map to categories via `RULE_CATEGORY`,
with unmapped rules in `other`. Pairs with `camber.actionplan` (what to do)
and `camber.fault_economics` (what it's worth). stdlib only.
