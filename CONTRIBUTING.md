# Contributing to CAMBER

Thanks for your interest! CAMBER is a vendor-neutral toolkit for analyzing
Building Automation System (BAS) trend data — fault detection & diagnostics (FDD),
measurement & verification (M&V), and retro-commissioning (RCx). Contributions of
all kinds are welcome: new diagnostics, ingest adapters, M&V models, ontology
interop, documentation, and bug fixes.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Vendor- and site-neutral.** Diagnostics are written against the `Role` vocabulary
  (`camber/model/roles.py`), never a specific BAS's tag names. Building-specific material
  (tag maps, scan scripts, data) belongs in `examples/`, not the package. **Never name or
  otherwise identify a real client site** in code, tests, docs, or the CHANGELOG — describe
  the scenario generically (e.g. "a high-outside-air VAV design"), never the site.
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

**Declare what you couldn't evaluate.** A rule must never assert a negative it did not
test. When an absent optional input makes a sub-check impossible, represent it as `None`
(tri-state), not a `nan`/`False`/`0` sentinel: exclude the `None` sub-check from severity
(test `is False`/`is None`, never `not x`), write the metric as `None`, keep the untested
claim out of the summary, and append a note to `Finding.caveats`. See the convention in
`camber/rules/base.py` and the `chw_plant_reset` reference implementation.

## Adding a building or BAS

No code needed — add a tag→role mapping (a JSON file like the ones under
`examples/`), or derive one automatically from a Brick model with
`camber.interop.brick`.

## Tests

- Keep `pytest -q` green; new behavior ships with tests.
- Tests must not hit the network or read real-building data. The public-dataset
  examples download on demand into the git-ignored `examples/_data/`.

## Style

- Style is enforced by **ruff** (lint + format, line length 100), configured in
  `pyproject.toml` and gated in CI. Run `ruff check .` and `ruff format .` before pushing,
  or install the hook once with `pip install pre-commit && pre-commit install`.
- Clear module and function docstrings that explain the *why*, not just the *what*. Match
  the idioms of the surrounding code.

## Commits and pull requests

- Small, focused commits with descriptive messages.
- Open a PR against `main`; CI (ruff lint + format check, and pytest on Python 3.10 and
  3.11) must pass.
- Fill in the PR template and link any related issue.

## No AI-assistant attribution

CAMBER is a human-authored, vendor-neutral project. Do **not** add
AI-assistant attribution anywhere — not in commit messages, PR bodies, or
tracked files. That means no `Co-Authored-By` trailers crediting an AI
assistant, no auto-generated "assistant" credit lines, no AI coding-tool
name-drops, no robot emoji, and no committed `CLAUDE.md` instructions file.

This is enforced two ways:

- **Local git hooks** (opt-in, but please enable them). They reject a commit
  whose message or staged content adds those patterns. Git does not share
  `.git/hooks` across clones, so the hooks are versioned under `.githooks/`.
  Enable them once per clone:

  ```sh
  bash scripts/install-hooks.sh
  ```

  This sets `git config core.hooksPath .githooks`.

  The pre-commit hook also consults an optional **local, gitignored** denylist —
  copy `.githooks/denylist.local.example` to `.githooks/denylist.local` and add
  patterns (one case-insensitive regex per line). Because that file is never
  committed, it can hold sensitive terms such as a real client site name — the
  right place for the site-neutral rule, since a *tracked* denylist would embed
  the very name it exists to keep out.

- **CI backstop.** The `attribution-guard` workflow
  (`.github/workflows/attribution-guard.yml`) scans tracked files and the PR's
  commit range on every pull request and fails if attribution is found. This
  runs regardless of whether contributors installed the local hooks.
