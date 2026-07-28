# Operating-schedule inference

`camber.schedule` infers the building's **actual** weekly operating schedule from an interval
load (or fan-status) series — what hours it really runs, versus what the schedule claims. It drives
setback verification, demand-response eligibility, and onboarding (a detected schedule seeds the
occupancy model).

```python
from camber.schedule import detect_schedule, compare_schedule

sch = detect_schedule(load_kw)  # threshold defaults to midway between base and peak
sch.days[0].start_hour, sch.days[0].end_hour  # Monday on-period
sch.occupied_fraction  # share of the 168 hour-of-week slots that are on

stated = [(d, h) for d in range(5) for h in range(9, 17)]  # weekday 9–5
cmp = compare_schedule(sch, stated)
cmp["extra_runtime_slots"], cmp["n_missing"], cmp["agreement"]
```

The method is transparent: mark each interval "on" above a threshold (default `base + 0.5·(peak −
base)` from the 10th/90th percentiles), then take the majority state per hour-of-week across all
weeks. `compare_schedule` returns the **extra-runtime** slots (running when it shouldn't — a setback
opportunity), the **missing** slots, and the agreement fraction. Flags: `threshold`, `on_level`,
`min_fraction`.
