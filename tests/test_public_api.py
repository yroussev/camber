"""Locks CAMBER's public API surface (see docs/API-STABILITY.md).

The public surface is computed from source (AST, no imports — so it is deterministic and
independent of which optional extras are installed) and compared against a committed
snapshot, ``tests/public_api_snapshot.json``. Adding or removing any public name fails this
test until the snapshot is regenerated — making every change to the promised surface a
deliberate, reviewed act.

Regenerate the snapshot after an intentional API change with::

    python tests/test_public_api.py --update

Policy recap: a name is public iff it has no leading underscore and lives in a module with no
leading underscore. A package ``__init__``'s surface is its re-exported ``__all__``; a regular
module's surface is its top-level non-underscore definitions (and its ``__all__`` must mirror
them — see ``test_all_matches_public_defs``).
"""

import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_ROOT, "camber")
_SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_api_snapshot.json")


def _is_public_module(rel: str) -> bool:
    """A module path is public iff no component (after 'camber') starts with '_'."""
    parts = rel[: -len(".py")].split(os.sep)
    return not any(p.startswith("_") for p in parts if p != "__init__")


def _all_literal(tree: ast.Module):
    """Return the list of strings assigned to ``__all__`` at module level, or None."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [
                    e.value
                    for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
    return None


def _defined_public_names(tree: ast.Module) -> list:
    names, seen = [], set()

    def add(n):
        if not n.startswith("_") and n not in seen:
            seen.add(n)
            names.append(n)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id)
    return names


def _iter_public_modules():
    """Yield (dotted_name, filename, ast_tree) for every public module under camber/."""
    for dirpath, _dirs, files in os.walk(_PKG):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, _ROOT)
            if not _is_public_module(rel):
                continue
            tree = ast.parse(open(abspath, encoding="utf-8").read())
            mod = rel[: -len(".py")].replace(os.sep, ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            yield mod, fn, tree


def compute_public_surface() -> dict:
    """Map each public ``camber`` module (dotted name) -> sorted list of its public names.

    A package ``__init__`` re-exports, so its surface is its declared ``__all__``. A regular
    module *defines* its surface, so it's the top-level non-underscore definitions (NOT its
    ``__all__`` — otherwise a new public name added without updating ``__all__`` would silently
    escape the lock, even though it is public by the policy). ``test_all_matches_public_defs``
    separately keeps each regular module's ``__all__`` in sync with those definitions.
    """
    surface = {}
    for mod, fn, tree in _iter_public_modules():
        if fn == "__init__.py":
            names = _all_literal(tree)
            if names is None:
                names = _defined_public_names(tree)
        else:
            names = _defined_public_names(tree)
        surface[mod] = sorted(names)
    return surface


def _load_snapshot() -> dict:
    with open(_SNAPSHOT, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- tests


def test_public_surface_matches_snapshot():
    current = compute_public_surface()
    snapshot = _load_snapshot()

    added_mods = sorted(set(current) - set(snapshot))
    removed_mods = sorted(set(snapshot) - set(current))
    changed = {
        m: {
            "added": sorted(set(current[m]) - set(snapshot[m])),
            "removed": sorted(set(snapshot[m]) - set(current[m])),
        }
        for m in set(current) & set(snapshot)
        if current[m] != snapshot[m]
    }
    msg = (
        "Public API surface changed. If intentional, regenerate the snapshot with "
        "`python tests/test_public_api.py --update` and review the diff.\n"
        f"  new modules: {added_mods}\n"
        f"  removed modules: {removed_mods}\n"
        f"  changed: {json.dumps(changed, indent=2)}"
    )
    assert not added_mods and not removed_mods and not changed, msg


def test_no_underscore_names_leak_into_all():
    """No ``__all__`` should advertise a private name (underscore-prefixed, non-dunder)."""
    surface = compute_public_surface()

    def _private(n: str) -> bool:
        return n.startswith("_") and not (n.startswith("__") and n.endswith("__"))

    leaks = {m: [n for n in names if _private(n)] for m, names in surface.items()}
    leaks = {m: ns for m, ns in leaks.items() if ns}
    assert not leaks, f"underscore-prefixed names in a public surface: {leaks}"


def test_declared_all_names_resolve():
    """Every ``__all__`` name must actually be importable from its module.

    Optional-extra modules that cannot import in this environment are skipped (the AST
    snapshot still covers them); everything importable is checked.
    """
    import importlib

    surface = compute_public_surface()
    unresolved = {}
    for mod_name, names in surface.items():
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue  # optional dependency not installed here
        if not hasattr(mod, "__all__"):
            continue  # snapshot came from AST, not a declared __all__
        missing = [n for n in names if not hasattr(mod, n)]
        if missing:
            unresolved[mod_name] = missing
    assert not unresolved, f"__all__ names that don't resolve: {unresolved}"


def test_every_public_def_is_documented():
    """Every public function/class defined in a public module must have a docstring.

    This is the "documented" half of the API contract: a name we promise is a name we
    explain. Checked from source so re-exports aren't double-counted.
    """
    undocumented = []
    for mod, _fn, tree in _iter_public_modules():
        for node in tree.body:
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and not node.name.startswith("_"):
                if not ast.get_docstring(node):
                    undocumented.append(f"{mod}::{node.name}")
    assert not undocumented, f"public defs missing a docstring: {undocumented}"


def test_all_matches_public_defs():
    """Each regular module's ``__all__`` must list exactly its public definitions.

    This keeps ``__all__`` honest: it can't under-report (a public def missing from ``__all__``)
    or over-report (a stale/typo name). Package ``__init__`` files are exempt — they re-export
    names defined elsewhere, so their ``__all__`` is checked by ``test_declared_all_names_resolve``.
    """
    mismatches = {}
    for mod, fn, tree in _iter_public_modules():
        if fn == "__init__.py":
            continue
        declared = _all_literal(tree)
        if declared is None:
            continue  # no __all__ on this module — allowed; surface is its defs
        defined = _defined_public_names(tree)
        if set(declared) != set(defined):
            mismatches[mod] = {
                "in_all_not_defined": sorted(set(declared) - set(defined)),
                "defined_not_in_all": sorted(set(defined) - set(declared)),
            }
    assert not mismatches, (
        f"__all__ out of sync with public defs: {json.dumps(mismatches, indent=2)}"
    )


def test_deprecation_decorator_warns():
    """Smoke test the deprecation machinery the policy relies on."""
    import warnings

    from camber._deprecation import deprecated, warn_deprecated

    @deprecated(since="1.2", remove_in="2.0", use="camber.new.thing")
    def old(x):
        """Docstring."""
        return x

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert old(41) == 41  # still callable
        warn_deprecated("legacy_path()", since="1.2", remove_in="2.0")
    assert len(caught) == 2
    assert all(issubclass(w.category, DeprecationWarning) for w in caught)
    assert old.__deprecated__ == {"since": "1.2", "remove_in": "2.0", "use": "camber.new.thing"}


if __name__ == "__main__":
    if "--update" in sys.argv:
        with open(_SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump(compute_public_surface(), f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {_SNAPSHOT}")
    else:
        print(json.dumps(compute_public_surface(), indent=2, sort_keys=True))
