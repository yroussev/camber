# Sequence-of-Operations (SOO) conformance example

Where the rule library asks *"is this a known fault?"*, SOO conformance asks
*"is the equipment doing what its sequence of operations says it should?"* You write
the sequence down as a short list of declarative **clauses** and the engine measures,
per clause, the fraction of applicable intervals the equipment actually conformed.

`ahu_sequence.json` is a small sequence in CAMBER's clause schema. Each clause is
`when <gate> then expect <predicate>` (the gate is optional → always). Both gate and
expectation are **predicates** over [roles](../../camber/model/roles.py):

```json
{
  "name": "sat_tracks_setpoint",
  "when":   {"subject": "supply_fan_status", "op": "on"},
  "expect": {"subject": "supply_air_temp", "op": "within", "ref": "supply_air_temp_sp", "tol": 2.0}
}
```

**Operators:** `lt le gt ge` (vs a `value` or another role `ref`), `eq`/`ne`/`within`
(use `tol`), and `off`/`on` (treat the subject as a 0/1 status/command point).
Optional per-clause bands: `fault_below` / `warn_below` (conformance %) and
`min_samples`.

## Run

```python
import json
from camber.soo import spec_from_dicts, evaluate_soo, soo_findings

spec = spec_from_dicts(json.load(open("examples/soo/ahu_sequence.json")))
report = evaluate_soo(role_frame, spec, equip="AHU-1")   # role_frame from resolve()
print(report.overall_conformance, report.severity)
for c in report.clauses:
    print(c.summary)

# Or get Findings that flow through the same prioritization / report / triage:
findings = soo_findings(role_frame, spec, "AHU-1")
```

Because clauses key off **roles**, the same sequence spec runs on any building once its
points are mapped — the spec is a config artifact, not code. Conformance is a
*measurement* of operated-vs-designed behavior: a low score points at the clause and
the data behind it, leaving the diagnosis to the analyst and the rule library.
