# Site report

`camber.report.build_site_report` is the one-shot, owner-facing deliverable — a single
self-contained HTML page that answers, top to bottom:

- **How healthy is this building?** — the health scorecard (overall A–F grade + per-category scores).
- **What does the data look like?** — dashboard chart sections (readiness / carpet / data-quality).
- **What should we do?** — the ranked action plan (finding + $/yr + advisory recommendation).
- **How do we know?** — each finding's pattern-J evidence chart.

```python
from camber.report import build_site_report

html = build_site_report(
    df, findings=findings, rules=registry, loads=loads, price=price
)  # loads/price cost the action plan
open("site.html", "w").write(html)
```

Composed from the existing report fragments (`scorecard`, `actionplan`, the dashboard sections and
pattern-J evidence engine) — matplotlib inlined as base64, no web framework, read-only toward the
BAS. Flags: `sections` (A/B/E/I), `rank_by`, `top_n`, `normalize`, `frames` (per-equipment evidence),
`title`. With no findings it degrades to a charts-only report.
