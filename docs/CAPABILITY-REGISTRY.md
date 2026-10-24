# Capability registry

`camber.capabilities` (0.110, #275; **provisional**) is one read-only core that CAMBER's assistant
servers expose: the MCP server (0.110) and the A2A server (0.112) are thin adapters over it, so
the rules about what a caller may see and do are enforced in one place.

A **capability** is a named, read-only operation with JSON Schema 2020-12 input and output
schemas and a handler. A call returns a `CapabilityResult`:

| Field | What it holds |
|---|---|
| `data` | the structured result, valid against the capability's output schema |
| `text` | a short summary that cites fact ids such as `[F1@points]` |
| `facts` | the fact index those ids name: `{"id", "kind", "value", "text", ...}` |
| `banners` | licence, truncation and citation notices, always shown to the user |
| `truncated` | `True` when a list was cut to the caller's row limit |

A refused or failed call raises `CapabilityError` with a `code`: `invalid_params`, `not_found`,
`not_allowed`, `refused_licence`, `too_large`, `timeout` or `internal`.

```python
import pandas as pd

from camber.capabilities import CallContext, default_registry
from camber.model.roles import Role
from camber.store import ParquetStore

idx = pd.date_range("2024-03-01", periods=24, freq="h")
store = ParquetStore("store")
store.write_role_frame(
    pd.DataFrame({Role.OAT: 60.0, Role.SUPPLY_AIR_TEMP: 55.0}, index=idx),
    facility_id="hq",
    equip="AHU-1",
    equip_class="AHU",
)

ctx = CallContext(store="store", allowed_facilities=frozenset({"hq"}))
reg = default_registry()
print([c.name for c in reg.capabilities()])  # ['facilities', 'points']
res = reg.call("points", {"facility_id": "hq"}, ctx)
print(res.text)  # hq stores 2 matching points on 1 equipment [F1@points].
```

## Built-in capabilities

| Name | Arguments | Returns |
|---|---|---|
| `facilities` | none | the allowed facilities: id, name, lifecycle state, time zone, first and last stored sample, point and equipment counts |
| `points` | `facility_id`; optional `equip`, `role`, `equip_class` | the facility's stored points (equipment, class, role), sorted, and their count |

More capabilities (rule runs, reports, history) follow in later releases (#278).

## The policy every call follows

`Registry.call(name, arguments, ctx)` enforces, in order:

1. **Input validation.** `arguments` must match the input schema, else `invalid_params` (the
   error's `data["errors"]` lists each problem with its JSON pointer).
2. **Facility allowlist.** `ctx.allowed_facilities` is required; empty means no facility is
   visible. A capability names a facility only through a `facility_id` or `facility_ids`
   argument, and one outside the allowlist is `not_allowed`, whether it exists or not, so a
   caller cannot probe for ids. A listing shows only allowed facilities, and a result that names
   another facility is an `internal` error.
3. **Research-only data is refused.** A facility ingested from a dataset the
   [catalog](DATASETS.md) holds research-only (a non-commercial or no-derivatives licence, or an
   open-licence dataset held back for a stated reason) is `refused_licence`. There is no override
   in any adapter: `--accept-noncommercial` is for the person running `camber datasets`, not for
   an assistant. The check reads the facility's recorded dataset block (`access`,
   `redistribution`, `licence`) and, by its `dataset_id`, the catalog's current terms. A listing
   leaves such facilities out and says how many it withheld.
4. **No-persist.** The handler runs inside the no-persist scope (#276): a config run folds no
   fault history, writes no report file or manifest entry, writes back no store cache and
   fetches no weather. See [Fault lifecycle](FAULT-LIFECYCLE.md#no-persist-runs).
5. **Trust floor.** `ctx.min_trust` (0.5 by default) is the lowest `trust_gate.min_trust` a config
   run gets through `run_config_for`; a config that asks for more keeps its own.
6. **Caps** (`ctx.caps`). A list in the result longer than `max_rows` (5,000) is cut, with
   `truncated=True` and a banner that says how many rows there were; a result whose JSON is
   larger than `max_bytes` (2 MB) is `too_large`; a call that runs longer than `max_seconds`
   (120 s) is `timeout`.
7. **Citations.** `text` is checked against `facts` with `camber.agent.verify.check` (strict): a
   sentence that cites an unknown id or states a figure no cited fact carries is removed, with a
   banner.
8. **No leaks.** Any other exception becomes `internal`, and no error message holds an absolute
   local path.

**The timeout cannot stop a handler.** The handler runs in a worker thread; Python cannot kill a
thread, so on a timeout the call returns `timeout` at once while the handler finishes in the
background and its result is thrown away. Handlers are read-only and run no-persist, so a late
finish changes nothing.

## Adding a capability

Adding one is a one-file job: a module `camber/capabilities/_cap_<name>.py` that defines
`CAPABILITIES` (and, optionally, `PROMPTS`). `default_registry()` imports every `_cap_*` module in
name order.

<!-- fence: skip (a module in the camber package, not a session) -->
```python
from camber.capabilities import Capability, CapabilityResult


def equipment_count(ctx, arguments):
    # arguments is valid, and its facility is allowed and licence-clean
    ...
    return CapabilityResult(data={...}, text="... [F1@equipment].", facts=[...])


CAPABILITIES = (
    Capability(
        name="equipment_count",
        title="Equipment count",
        description="What it does, when to call it and what it returns (for a tool picker).",
        input_schema={"type": "object", "required": ["facility_id"], "properties": {...}},
        output_schema={"type": "object", "properties": {...}},
        handler=equipment_count,
    ),
)
```

A handler names facilities only through `facility_id` / `facility_ids`, keeps a listing to
`ctx.allowed_facilities` and leaves out research-only facilities (`research_only(meta)`), calls
`run_config_for(ctx)` for any config run, and puts every figure its `text` states into a fact it
cites. The schemas use a subset of JSON Schema 2020-12: `type`, `properties`, `required`,
`additionalProperties`, `enum`, `const`, `items`, `minItems` / `maxItems`, `minimum` /
`maximum` (and the exclusive forms), `minLength` / `maxLength` and `pattern`. `default` is an
annotation and is not applied. Registering a schema with any other keyword is a `ValueError`, so a
constraint is never declared without being enforced.
