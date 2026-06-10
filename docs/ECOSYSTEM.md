# Ecosystem — where to leverage existing OSS instead of reinventing

CAMBER deliberately ships a **dependency-light, zero-config core** (stdlib +
numpy/pandas/pyarrow/matplotlib). For several heavier or highly-standardized
pieces, mature open-source libraries already exist. The strategy is **not** to fork
them wholesale — it is to **integrate them as optional extras** so the core stays
light while users who need depth can opt in, and so we don't reinvent
well-trodden wheels.

> License notes: items marked ✓ were verified during research; items marked
> "confirm" are from general knowledge — check the repo's LICENSE before depending.

## Candidates by area

| Area | Project | License | What it gives us | Recommendation |
|------|---------|---------|------------------|----------------|
| **M&V** | [OpenEEmeter / eemeter](https://github.com/openeemeter/eemeter) (now `opendsm`) | Apache-2.0 ✓ | CalTRACK-compliant normalized metered energy / avoided-energy at meter scale | **Align + optional backend.** Keep our lightweight change-point/TOWT core; offer an `[eemeter]` path and match CalTRACK terminology for credibility. |
| **M&V weather** | [eeweather](https://github.com/openeemeter/eeweather) | Apache-2.0 (confirm) | NOAA station matching / normalization-year weather | Optional, complements our TMY/EPW loader. |
| **Ontology (Brick)** | [py-brickschema](https://github.com/BrickSchema/py-brickschema) + [rdflib](https://github.com/RDFLib/rdflib) | BSD-style / BSD-3 (confirm) | Robust Turtle/RDF parsing, Brick reasoning/validation | **Optional `[brick]` extra** using rdflib for full models; keep our minimal zero-dep parser as the default. |
| **Ontology (modeling)** | [BuildingMOTIF](https://github.com/NREL/BuildingMOTIF) | BSD-3 (confirm) | Template-driven Brick/223P model creation + SHACL validation | Watch / optional for Phase-2 full-ontology work. |
| **Haystack client** | [phable](https://github.com/rick-jennings/phable) (modern, zero-dep) / [pyhaystack](https://github.com/ChristianTremblay/pyhaystack) | confirm / Apache-2.0 ✓ | A real `hisRead`/Zinc client | **Optional `[haystack]` extra** wired as the transport our adapter already accepts — no need to hand-roll the HTTP/Zinc layer. |
| **PV modeling** | [pvlib-python](https://github.com/pvlib/pvlib-python) | BSD-3 ✓ | Rigorous PV performance/irradiance modeling | **Optional `[pv]` extra** for serious PV; keep our performance-ratio basics dep-free. |
| **Psychrometrics** | [PsychroLib](https://github.com/psychrometrics/psychrolib) | MIT ✓ | ASHRAE psychrometric properties (humidity ratio, enthalpy, wet-bulb) | Optional; back any psychrometric needs (e.g. App-C solar-MRT comfort, latent loads). |
| **Controls / agents** | [VOLTTRON](https://github.com/VOLTTRON/volttron) | Apache-2.0 (confirm) | BACnet/Modbus drivers, an agent platform, deployment | Reference for ingest drivers and a deployment target; don't vendor. |

## The strategy: optional extras, not forks

- **Core stays zero-dep.** Today's `camber/` runs on numpy/pandas/pyarrow/
  matplotlib only. None of the above becomes a hard dependency.
- **Add `pyproject` extras** so capability is opt-in, e.g.:
  `pip install camber[brick,haystack,pv]`. Each extra wires a mature library behind
  an interface we already have (the Haystack adapter's injectable transport, a PV
  backend, a Brick parser swap).
- **Why integrate rather than fork:** these projects are maintained, tested, and
  standards-tracking (CalTRACK, Brick, Haystack, PV). Forking would mean owning
  that maintenance; depending optionally gets the value without the burden, and
  keeps CAMBER's distinct contribution (the vendor-neutral role model + the unified
  FDD/M&V/RCx pipeline) clear.
- **Where we keep our own:** the role/mapping/entity model, the FDD rule engine and
  triage/lifecycle, the Std-211 reporting, and the Parquet store are CAMBER's
  reason to exist — no equivalent single OSS package combines them.

## Near-term integration picks

1. **Brick via rdflib (`[brick]`) — DONE.** `camber.interop.brick` has an rdflib
   backend (`backend="rdflib"`, auto-selected when installed); the zero-dep minimal
   parser remains the default. Verified identical to the minimal parser on the LBNL
   model and able to parse models it can't (`rdf:type`, full IRIs).
   `pip install camber[brick]`.
2. **Haystack via phable/pyhaystack (`[haystack]`) — DONE.**
   `camber.ingest.haystack` provides `client_transport(his_read)` to wire any
   maintained client into the adapter's transport seam in one line, plus a
   dependency-free `http_json_transport` for token/JSON-capable servers; the
   decoders handle both the v3 and Hayson JSON encodings. The `[haystack]` extra
   pulls phable (Python >= 3.11) or pyhaystack (older).
3. **M&V: align with CalTRACK / eemeter — DONE.** `mandv.caltrack.caltrack_savings`
   assembles the Option-C / CalTRACK-Daily NMEC workflow (baseline model → avoided
   energy + FSU); [docs/MANDV.md](MANDV.md) maps the terminology, notes where we
   differ from strict CalTRACK, and gives an eemeter cross-check recipe (no
   dependency added).

4. **Tariffs / OpenEI URDB — DONE (hybrid).** A native, dependency-free engine
   (`camber.tariff`) bills an interval load against a URDB-shaped rate (fixed, TOU
   energy + tiers, TOU/flat demand, ratchet) and covers the common cases; `camber.interop.openei`
   fetches + maps a URDB rate (stdlib `urllib`, API key). For exotic rates and
   cross-checking, an optional `[tariff]` extra bridges to **NREL PySAM**'s
   battle-tested `UtilityRate5` (`camber.interop.tariff_nrel`) — BSD-3-Clause, but a
   ~47 MB binary, so it stays an opt-in extra, never a core dependency. Same own-it +
   cross-check-the-heavyweight pattern as M&V/eemeter. (NREL REopt's tariff logic is
   also BSD-3 but Julia-native — reachable via the REopt API, not embedded.)

## Cross-validation: G36 fault conditions vs. open-fdd

Our G36 AHU fault engine (`camber.fdd_g36`) is a clean-room implementation of the
ASHRAE Guideline 36 §5.16.14 fault conditions. To check it independently, we
cross-validated it against [open-fdd](https://github.com/bbartling/open-fdd) (MIT) —
a separate, independently-authored G36 FDD library — on the **public LBNL simulated
single-duct AHU dataset** (the CC-BY dataset the [`examples/lbnl_fdd`](../examples/lbnl_fdd)
example uses). Running on a public, downloadable dataset makes this corroboration
fully reproducible and shareable, with no client data involved.

We pinned **open-fdd 0.1.5**, the last release that still exposes the classic
**FC1–FC16** per-fault API (the current 3.x line replaced it with a generic
configurable engine, so the named-FC comparison is no longer apples-to-apples
there).

### What was runnable

With the signals available in this dataset, the runnable common set was
**FC2, FC3, FC5, FC8, FC10, FC12**. The rest were unrunnable **in both tools** for
lack of inputs, not because of any Camber limitation:

- **FC7, FC9, FC11, FC13** need a **supply-air-temperature setpoint** trend, which
  this dataset didn't include.
- **FC14, FC15** need **coil entering/leaving temperatures**, also not trended.

Neither tool can evaluate a fault whose required inputs aren't present, so these
were excluded symmetrically.

### Result — the equations agree

On a **common denominator** (the intersection of each rule's applicable rows, so
both tools are scored over exactly the same hours):

- **FC5, FC8, FC10, FC12 match open-fdd to 0.00 percentage points on every AHU
  fault scenario tested.** The fault *equations* are equivalent; the only differences
  observed came from how each tool frames its denominator (see the convention below).
- **FC3** had a lone residual of **≤ 2.3 pts**, an immaterial mixed-air-bounds edge
  artifact on a fault that **fires in neither tool** (it is a boundary-rounding
  difference in the "applicable" count, not a disagreement about any flagged hour).

**Conclusion: Camber's G36 implementation is independently corroborated** — a second,
independently-written G36 library computes the same fault equations and, on a like-
for-like denominator, the same fault rates.

### Convention: operating-state gating vs. single-signal gating

The one systematic difference between the two tools is **which hours each fault is
considered "applicable"** — the denominator of the fault percentage, not whether a
fault fires or where:

- **Camber gates each fault by its G36 operating-state classifier.** We classify
  every interval into an operating state **OS#1–OS#5** from the heating/cooling
  valve commands plus the OA-damper position (`classify_os` →
  heating / free-cooling / mechanical+economizer / mechanical+min-OA / simultaneous),
  and evaluate each FC **only in the operating states G36 §5.16.14.9 lists for it**
  (`OS_FAULTS`). So FC10 ("OAT/MAT should track in 100% economizer"), for example,
  is scored only over the hours the AHU is actually in that economizer state.
- **open-fdd gates on a single-signal threshold.** Each fault is applied over the
  rows selected by one signal (e.g. fan running, or a single mode flag), without the
  full multi-signal operating-state classification.

These two definitions select **different sets of "applicable hours,"** which changes
the reported fault **magnitude** (the percentage) — but **not which faults fire, nor
the hours at which they fire** (the fault equations and their per-row results are the
same). That is exactly why the cross-validation matches to 0.00 pts once both tools
are put on a common denominator.

**Camber's operating-state gating is the chosen convention**, deliberately, because
it is the more **G36-faithful** definition of when a fault is applicable: G36 ties
each fault condition to the operating state(s) in which it is meaningful, and we
honor that mapping directly. **Tradeoff:** operating-state gating yields a
**narrower applicable set** than a single-signal gate (an FC is counted over fewer
hours — only those in its valid operating states), so Camber's denominators are
smaller and its percentages are computed over a stricter, more specific population
of hours. We consider that the correct, standard-aligned behavior; the
single-signal framing is broader but less precisely tied to the standard's intent.

> For cross-tool comparison, `run_g36_afdd(..., comparability=True)` additionally
> emits a single-signal-gated (input-validity) fault % alongside the default
> operating-state-gated %, so a reviewer can reconcile Camber's numbers with an
> open-fdd-style denominator without changing Camber's default outputs. See
> `camber/fdd_g36.py`.
