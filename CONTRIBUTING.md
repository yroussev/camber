# Contributing to CAMBER

Thanks for your interest! CAMBER is a vendor-neutral toolkit for analyzing
Building Automation System (BAS) trend data — fault detection & diagnostics (FDD),
measurement & verification (M&V), and retro-commissioning (RCx). Contributions of
all kinds are welcome: new diagnostics, ingest adapters, M&V models, ontology
interop, documentation, and bug fixes.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Vendor-neutral.** Diagnostics are written against the `Role` vocabulary
  (`camber/model/roles.py`), never a specific BAS's tag names. Building-specific
  material (tag maps, scan scripts, data) belongs in `examples/`, not the package.
- **Clean-room.** Cite public standards (ASHRAE Guideline 36 / 14, Standard 55 /
  211, IPMVP, NIST APAR, PNNL Building Re-tuning, LBNL) for methods. Do **not**
  paste third-party or proprietary source code or copyrighted text.
- **Dependency-light.** The runtime depends only on numpy, pandas, pyarrow, and
  matplotlib. Open an issue to discuss before adding a dependency.
- **Honest results.** Diagnostics and M&V should report uncertainty and
  limitations rather than overstate (e.g. a weak model fit is reported as weak).

## Development setup

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest -q
```

Python 3.10+ is required. The full suite runs in seconds and needs no network or
real-building data.

## Project layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layered design. In short:
`ingest/` → `model/` + `resolve` → `rules/` (FDD) and `mandv/` (M&V) → `report/`,
with `store/` (persistence), `interop/` (Brick/Haystack), `integrate/` (tickets),
and `api/` (read API) around the edges.

## Adding a diagnostic (the common case)

1. Write the math in `camber/<name>.py` against role-named DataFrame columns.
2. Wrap it as a rule in `camber/rules/<name>_rule.py`, declaring `roles_required`
   and `roles_optional`.
3. Add `tests/test_<name>.py` with a **synthetic fixture** that exhibits the fault
   so detection is proven deterministically.
4. Cite the standard/method in the module docstring.

## Adding a building or BAS

No code needed — add a tag→role mapping (a JSON file like the ones under
`examples/`), or derive one automatically from a Brick model with
`camber.interop.brick`.

## Tests

- Keep `pytest -q` green; new behavior ships with tests.
- Tests must not hit the network or read real-building data. The public-dataset
  examples download on demand into the git-ignored `examples/_data/`.

## Style

- PEP 8. Clear module and function docstrings that explain the *why*, not just the
  *what*. Match the idioms of the surrounding code.

## Commits and pull requests

- Small, focused commits with descriptive messages.
- Open a PR against `main`; CI (pytest on Python 3.10 and 3.11) must pass.
- Fill in the PR template and link any related issue.
