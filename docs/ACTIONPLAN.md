# Prioritized action plan

`camber.actionplan` fuses three layers into one operator punch list: **what's wrong** (FDD
findings), **what it costs** (`camber.fault_economics`), and **what to do** (`camber.aso`). Each
actionable finding becomes an `ActionItem` with its estimated annual dollar impact and its advisory
recommendation, ranked **worst-dollars-first** (severity breaks ties).

```python
from camber.actionplan import build_action_plan, action_plan_html
from camber.fault_economics import EnergyPrice, EquipmentLoad

plan = build_action_plan(findings,
                         loads={"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200)},
                         price=EnergyPrice(electricity_per_kwh=0.15))
for a in plan:
    print(a.severity, a.equip, a.rule, f"${a.annual_cost_usd:,.0f}", "→",
          a.recommendation.title if a.recommendation else "")
```

Each `ActionItem`: `equip`, `rule`, `severity`, `annual_cost_usd`, `costed` (False when sizing is
missing — the dollar figure is then omitted, never fabricated), `recommendation` (a
`camber.aso.Recommendation` or `None`), and the source `finding`. `action_plan_rows(plan)` gives
JSON/table rows; `action_plan_html(plan)` a self-contained table. Flags: `loads`, `price`, `params`
(cost models), `aso_params` (recommendation targets), `min_severity`, `costed_only`.

## In the audit report

`AuditReport.to_html(recommend=True, loads=…, price=…)` appends a **Recommended actions** section
(the same ranked plan) beneath the findings; `AuditReport.action_plan(...)` returns the items
directly. Everything stays advisory and read-only toward the BAS.

## In config-driven runs

A declarative run can emit the action plan too: set `"recommend": true` (and optionally
`"price": {"electricity_per_kwh": 0.15}`) in the config's `report` block, and the HTML report
(`out_html`) includes the ranked **Recommended actions** section.
