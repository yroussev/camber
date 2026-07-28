# Plugin API

Extend CAMBER without forking it: ship a rule, an ingest adapter, or a report format from a
separate package (discovered via Python **entry points**), or register one **in-process**.
Plugins are duck-typed against the existing protocols — no base class to import.

| Kind | Entry-point group | Must look like |
|---|---|---|
| rule | `camber.rules` | `name`, `roles_required`, and `analyze(equip, frame)` (or `analyze_fleet`) |
| adapter | `camber.adapters` | `point_names()`, `load_points(names, resample=None)`, `units()` |
| report | `camber.reports` | a callable, or `to_text` / `to_html` / `render` |

## Shipping a plugin package

Declare entry points in the plugin package's `pyproject.toml`:

```toml
[project.entry-points."camber.rules"]
my_rule = "my_pkg.rules:MyRule"

[project.entry-points."camber.adapters"]
my_source = "my_pkg.io:MySource"

[project.entry-points."camber.reports"]
my_report = "my_pkg.report:render"
```

Once the package is installed alongside CAMBER, it's discovered:

```python
from camber.plugins import PluginRegistry, apply_rules
from camber.rules.builtin import builtin_registry

plugins = PluginRegistry().load_entrypoints()  # finds installed camber.* entry points
rules = apply_rules(plugins, builtin_registry())  # built-ins + plugin rules in one registry
plugins.adapters()  # {name: adapter class/factory}
plugins.reports()  # {name: report callable/renderer}
plugins.errors  # any plugin that failed to import/validate (isolated, not fatal)
```

Discovery is **isolated**: a plugin that fails to import or doesn't satisfy its protocol is
recorded in `errors` rather than breaking the others.

## In-process registration (no packaging)

For a local/quick extension, register objects directly:

```python
from camber.plugins import PluginRegistry, apply_rules
from camber.rules.base import Registry

reg = PluginRegistry()
reg.register("rules", MyRule)  # name taken from MyRule.name
reg.register("adapters", MySource, name="my_source")
rules = apply_rules(reg, Registry())  # MyRule is instantiated and registered
```

`apply_rules` accepts rule **classes** (instantiated zero-arg) or **instances**.

## Validation

`register` validates immediately (raising `TypeError` on a mismatch); `discover` /
`load_entrypoints` validate per plugin and capture failures. The validators check the duck-typed
protocol above — e.g. an adapter offered as a rule is rejected.

## Testing plugins

`discover(kind, source=...)` and `load_entrypoints(source_for=...)` accept an injected iterable of
entry-point-like objects (`.name`, `.load()`), so plugin discovery is testable without installing a
package (see `tests/test_plugins.py`).
