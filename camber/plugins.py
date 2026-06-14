"""Plugin API — discover and register third-party rules, ingest adapters, and report formats.

CAMBER is extensible without forking: a separate package can ship a rule, a `SourceAdapter`, or
a report renderer and advertise it via Python **entry points**, and CAMBER will discover it. The
same objects can also be **registered in-process** for quick/local extensions. Plugins are
duck-typed against the existing protocols (a rule has ``name``/``roles_required``/``analyze``; an
adapter has ``point_names``/``load_points``/``units``; a report is callable or has
``to_text``/``to_html``/``render``) — no base class to import, no new dependency.

Entry-point groups (declare these in a plugin package's ``pyproject.toml``)::

    [project.entry-points."camber.rules"]
    my_rule = "my_pkg.rules:MyRule"
    [project.entry-points."camber.adapters"]
    my_source = "my_pkg.io:MySource"
    [project.entry-points."camber.reports"]
    my_report = "my_pkg.report:render"

Discovery loads each entry point in isolation: a plugin that fails to import or validate is
recorded as an error rather than breaking the others.
"""

from __future__ import annotations

from dataclasses import dataclass

# Plugin kind -> entry-point group name.
GROUPS = {"rules": "camber.rules", "adapters": "camber.adapters", "reports": "camber.reports"}


def _is_rule(o) -> bool:
    return (hasattr(o, "name") and hasattr(o, "roles_required")
            and (callable(getattr(o, "analyze", None)) or callable(getattr(o, "analyze_fleet", None))))


def _is_adapter(o) -> bool:
    return all(callable(getattr(o, m, None)) for m in ("point_names", "load_points", "units"))


def _is_report(o) -> bool:
    return callable(o) or any(callable(getattr(o, m, None)) for m in ("to_text", "to_html", "render"))


_VALIDATORS = {"rules": _is_rule, "adapters": _is_adapter, "reports": _is_report}


@dataclass
class LoadedPlugin:
    """The outcome of loading one entry point (``obj`` is None when it failed)."""

    name: str
    kind: str
    obj: object | None = None
    dist: str = ""
    error: str = ""


def _group_entry_points(group: str):
    from importlib.metadata import entry_points
    try:
        return list(entry_points(group=group))          # Python 3.10+ selection API
    except TypeError:                                    # very old importlib.metadata
        return list(entry_points().get(group, []))      # pragma: no cover


def discover(kind: str, *, source=None) -> list:
    """Load + validate plugins of ``kind`` from entry points (or an injected ``source``).

    ``source`` is an iterable of entry-point-like objects (``.name``, ``.load()``); when None the
    installed ``camber.<kind>`` entry-point group is used. Returns :class:`LoadedPlugin` items —
    each either carries the loaded object or an ``error`` (import or validation failure).
    """
    if kind not in GROUPS:
        raise ValueError(f"unknown plugin kind {kind!r}; use one of {sorted(GROUPS)}")
    eps = source if source is not None else _group_entry_points(GROUPS[kind])
    out = []
    for ep in eps:
        name = getattr(ep, "name", repr(ep))
        try:
            obj = ep.load()
        except Exception as e:  # noqa: BLE001 — isolate a bad plugin
            out.append(LoadedPlugin(name=name, kind=kind, error=f"load failed: {e}"))
            continue
        if not _VALIDATORS[kind](obj):
            out.append(LoadedPlugin(name=name, kind=kind, error=f"not a valid {kind} plugin"))
            continue
        dist = getattr(getattr(ep, "dist", None), "name", "") or ""
        out.append(LoadedPlugin(name=name, kind=kind, obj=obj, dist=dist))
    return out


class PluginRegistry:
    """Holds discovered + in-process plugins by kind, with the load errors recorded."""

    def __init__(self):
        self._by_kind: dict = {k: {} for k in GROUPS}
        self.errors: list = []

    def register(self, kind: str, obj, *, name: str | None = None):
        """Register an in-process plugin object; validates it for ``kind``. Returns it."""
        if kind not in GROUPS:
            raise ValueError(f"unknown plugin kind {kind!r}")
        if not _VALIDATORS[kind](obj):
            raise TypeError(f"object is not a valid {kind} plugin")
        nm = name or getattr(obj, "name", None) or getattr(obj, "__name__", None) or repr(obj)
        self._by_kind[kind][nm] = obj
        return obj

    def load_entrypoints(self, *, kinds=None, source_for=None) -> "PluginRegistry":
        """Discover + register plugins from entry points. ``source_for(kind)`` (optional) injects
        the entry-point iterable per kind for testing. Failed loads land in ``self.errors``."""
        for kind in (kinds or GROUPS):
            src = source_for(kind) if source_for else None
            for lp in discover(kind, source=src):
                if lp.obj is not None:
                    self._by_kind[kind][lp.name] = lp.obj
                else:
                    self.errors.append(lp)
        return self

    def of_kind(self, kind: str) -> dict:
        return dict(self._by_kind[kind])

    def rules(self) -> dict:
        return self.of_kind("rules")

    def adapters(self) -> dict:
        return self.of_kind("adapters")

    def reports(self) -> dict:
        return self.of_kind("reports")

    def get(self, kind: str, name: str):
        return self._by_kind[kind][name]


def apply_rules(plugins: PluginRegistry, rule_registry):
    """Register a plugin registry's rule plugins into a :class:`camber.rules.base.Registry`.

    Rule plugins given as classes are instantiated (zero-arg); instances are used as-is. Returns
    the same ``rule_registry`` for chaining (e.g. with ``builtin_registry()``).
    """
    for obj in plugins.rules().values():
        inst = obj() if isinstance(obj, type) else obj
        rule_registry.register(inst)
    return rule_registry
