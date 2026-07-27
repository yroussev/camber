# Ontology interop — Brick & ASHRAE 223P

CAMBER's `Role` vocabulary is the hub; `camber.interop` maps it to and from the building-ontology
models other tools share, so an already-tagged building needs no hand-written mapping and CAMBER's
model can be exported for downstream use.

## Brick

- **Import** — `mapping_from_brick` / `roles_from_brick` derive `Role` mappings from a Brick model
  (point classes + `hasPoint`/`hasPart` relationships). See `camber/interop/brick.py`.
- **Export** — `to_brick(equip_id, equip_class, roles)` emits Brick Turtle. The role→Brick map was
  broadened in 0.2 to cover CO₂, outdoor-air CO₂/RH, outdoor-air flow, and airflow setpoints in
  addition to the temperatures, pressures, valves, dampers, and fans already supported.
- **Whole-site round-trip** — `site_to_ttl` / `site_from_ttl` round-trip a Site→Equip→Point model
  (with relationships); minimal parser by default, rdflib used when the `[brick]` extra is present.

## Project Haystack

- **Export** — `haystack_tags(role)` / `equip_haystack_tags(roles)` emit a role's marker-tag set (from
  `HAYSTACK_HINT`). `camber/interop/export.py`.
- **Import (0.6)** — `role_from_tags(tags)` / `roles_from_haystack(points)` / `mapping_from_haystack`
  recover roles from a point's marker tags, closing the round-trip to Brick-level parity. A role matches
  when its hint tag-set is a subset of the point's tags; the **most specific** hint wins ties (so
  `…temp sp` beats `…temp sensor`). Accepts `(name, tags)` pairs or Haystack tag dicts. All 54 roles
  round-trip export→import. `camber/interop/haystack_semantic.py`.

## ASHRAE 223P (minimal profile)

ASHRAE Standard 223P is an RDF/SHACL semantic model for building systems — equipment, connections,
media, and the physical properties they observe. The full standard is large, SHACL-validated, and
still maturing, so `camber.interop.semantic223` exports/round-trips a deliberately **minimal
profile**: equipment, their observable properties, and each property's QUDT **quantity-kind** +
**medium** derived from the role.

```python
from camber.interop import site_to_223, site_from_223
ttl = site_to_223(site, profile="minimal", include_relations=True)
site2 = site_from_223(ttl)        # round-trips equip_class + the points' roles
```

Mapping coverage was broadened in **0.6** from 21 to **44 of 54 roles** — the full plant/hydronic side
(CHW/HW/CW temps, loop pressures, pump/tower speeds), power/thermal energy, ambient/humidity, and the
refrigerant-side approach temps. The remaining 10 roles are binary/enumerated **status & command**
signals (`*_status`, `*_stage`, `reversing_valve_cmd`, `econ_cmd`, warm-up/cool-down) that carry no QUDT
quantity-kind; they are listed in `_NO_223_QUANTITY` and intentionally omitted from the quantity map
(223P models them as enumerated states). A test asserts the mapped + unmapped sets partition every role,
so a newly-added role can't be silently forgotten.

Mapping (`ROLE_TO_223`, `role_223_quantity`): e.g. `SUPPLY_AIR_TEMP → (Temperature, Air)`,
`OA_AIRFLOW → (VolumeFlowRate, Air)`, `CHW_FLOW → (VolumeFlowRate, Water)`, valves/dampers →
`PositionRatio`. The emitted Turtle types each equipment as `s223:Equipment` (with the CAMBER
`equip_class` as `rdfs:label`) and each point as `s223:Property` with `s223:hasQuantityKind` and
`s223:ofMedium`, linked by `s223:hasProperty`.

### Option flags — `site_to_223`
| flag | default | effect |
|---|---|---|
| `profile` | `"minimal"` | `minimal` emits only role-mapped properties; `full` also emits unmapped roles as a generic dimensionless property |
| `include_relations` | `True` | emit the equip→property `s223:hasProperty` edges |

### Scope & honesty

This is a **profile, not a conformance claim.** It captures the equipment/property/quantity layer
of 223P that maps cleanly from CAMBER's model; it does not assert full Standard-223 conformance
(which requires validation against the published SHACL shapes and richer connection/medium
modeling). Serialization is plain Turtle and the reader is a string parser, so no new dependency
is required.
