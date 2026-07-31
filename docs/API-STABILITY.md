# API stability & deprecation policy

This document defines what CAMBER promises about its API: what counts as **public**, what
those version numbers mean, and how a public name is changed or removed. It is the contract
behind the `1.0` release.

Until `1.0`, CAMBER is in the `0.x` range, where [Semantic Versioning](https://semver.org)
explicitly allows anything to change between releases. This policy describes the guarantees
that **take effect at `1.0`** — the `0.9.x` hardening series exists to make them true before
they are promised.

## What is public

> **A name is public if and only if it does not start with an underscore, and it lives in a
> module whose name does not start with an underscore, under the `camber` package.**

Concretely, the public API is:

- Every non-underscore function, class, and constant in a non-underscore module — whether you
  reach it through a package (`from camber.rules import builtin_registry`) or by its module
  path (`from camber.mandv.towt import fit_towt`, `from camber.ingest.quality import assess`).
- Each package and module also defines `__all__`, its **curated surface**: the names you get
  from `from camber.x import *`, the names featured in the docs, and the recommended way to
  import. `__all__` is a convenience and a curation, **not** a narrowing — a non-underscore
  name reachable by its module path is public even if it is not lifted into a package `__all__`.

The top-level `camber` namespace is intentionally minimal: it exposes only `__version__`. Import
from the subpackages and modules, not from `camber` directly.

### What is **not** public (may change or vanish without notice)

- Anything whose name starts with `_` (functions, classes, constants, methods, attributes).
- Any module whose name starts with `_` (e.g. `camber._deprecation`).
- The individual `camber.rules.*_rule` diagnostic modules **as import targets**: they are
  discovered through `camber.rules.builtin_registry()`, which *is* public and stable. The set
  of shipped rules and their behavior is part of the product; the module path you'd import a
  rule class from is not a promised import surface.
- Internal structure of returned objects beyond their documented fields, private attributes,
  and the exact text of messages, logs, and reprs.
- The optional-integration bridges under `camber.interop.*` that wrap third-party libraries
  (PySAM, pvlib, psychrolib, better-lbnl, rdflib, …): CAMBER's own wrapper functions are
  public, but where they surface an upstream type or option, that part follows the upstream
  library's compatibility, not CAMBER's.

## What the version number means

From `1.0.0`, CAMBER follows Semantic Versioning `MAJOR.MINOR.PATCH`:

| Part | Bumped when | Your code |
|---|---|---|
| **PATCH** (`1.0.0` → `1.0.1`) | Backward-compatible bug fixes only. | Keeps working. |
| **MINOR** (`1.0.0` → `1.1.0`) | New public API added; existing public API unchanged. New deprecations may be *announced*. | Keeps working. |
| **MAJOR** (`1.0.0` → `2.0.0`) | A public name is removed or changed incompatibly. | May need changes — see the CHANGELOG and the deprecation warnings you'll have seen since the last major. |

"Backward-compatible" is judged against the public surface defined above. Bug fixes that change
a genuinely wrong result are allowed in a MINOR/PATCH even though output changes — correctness
is not a frozen contract. Documented numerical results validated against a standard (the FDD/M&V
benchmarks) are guarded separately by the benchmark CI gate.

### Recommended pin

```
camber-toolkit>=1.0,<2
```

Pin the major. Minor and patch upgrades within a major are safe by this policy.

## Deprecation policy

A public name is **never removed or changed incompatibly without a deprecation period.** The
lifecycle:

1. **Announce.** In some MINOR release `X.Y`, the name starts emitting a `DeprecationWarning`
   that says the version it will be removed in and what to use instead. It keeps working
   unchanged. This is wired with the `@deprecated` decorator / `warn_deprecated()` helper in
   the private `camber._deprecation` module.
2. **Window.** The name keeps working, warning, for **at least one full minor release** and
   until the **next MAJOR** — whichever is longer. In practice a name deprecated during a
   `1.x` line is not removed before `2.0`.
3. **Remove.** The name is removed only in a MAJOR release, listed under **Removed** in the
   CHANGELOG.

So: to stay current, run your test suite with deprecation warnings visible
(`python -W error::DeprecationWarning` to make them hard failures), and act on them before the
next major. You will never be surprised by a removal you weren't warned about a full release
line in advance.

A deprecated object carries a machine-readable `__deprecated__ = {"since", "remove_in", "use"}`
attribute for tooling, and its docstring gains a deprecation note.

## How this is enforced

- **`tests/test_public_api.py`** holds a committed **snapshot** of the entire public surface
  (`tests/public_api_snapshot.json`). Adding or removing any public name fails the test until
  the snapshot is regenerated — so a change to the promised surface is always a deliberate,
  reviewed act, never an accident. The test also asserts every `__all__` name resolves and that
  no underscore-prefixed name leaks into an `__all__`.
- The lint gate (ruff, since `0.9.1`) and type gate (mypy, from `0.9.3`) keep the surface clean.

## Support

- **Python:** the versions in `requires-python` and the CI matrix (currently 3.10–3.11; widening
  to 3.13 in the `0.9.x` series). Dropping a Python version is a MINOR-release change announced
  in the CHANGELOG.
- **Dependencies:** NumPy / pandas / pyarrow / matplotlib within the ranges in `pyproject.toml`.
