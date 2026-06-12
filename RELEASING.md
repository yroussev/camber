# Releasing CAMBER

A short, repeatable checklist. PyPI and GitHub are **independent**: publishing to
PyPI does not make the GitHub repo public, and a public repo does not require PyPI.
Note that a PyPI release publishes the **package source** (pure-Python wheels ship
readable `.py`); tests, examples, and `examples/_data/` are **not** included in the
distribution (only the `camber/` package + README + LICENSE).

## Pre-release checklist

1. `pytest -q` is green locally and in CI.
2. Version bumped in `pyproject.toml` and `camber/__init__.py` (`__version__`).
3. `CHANGELOG.md` updated: move items from *unreleased* to the new version, dated.
4. Provenance sweep: no proprietary/client material in the package
   (`git grep -niE "<predecessor tool, client, and site names>" camber/` returns nothing of concern).

## Build and inspect (no upload)

```sh
python -m pip install --upgrade build twine
python -m build                 # -> dist/camber-<ver>.tar.gz (sdist) + .whl
python -m twine check dist/*
unzip -l dist/*.whl             # confirm: only camber/ + metadata, no tests/data
```

## Publish to PyPI

```sh
# recommended: test on TestPyPI first
python -m twine upload --repository testpypi dist/*
# then the real index (needs a PyPI account + API token)
python -m twine upload dist/*
```

Prefer **PyPI Trusted Publishing** (GitHub Actions OIDC) over a long-lived token
once the repo is public — it avoids storing a secret.

## Automated path (recommended) — tag-driven

`.github/workflows/release.yml` runs on any `v*` tag and, **after the test suite passes**:
publishes to PyPI via **Trusted Publishing** (OIDC — no stored token), pushes a **multi-arch
image (amd64 + arm64) to GHCR** (`ghcr.io/<owner>/camber:<ver>` + `:latest`), and cuts a GitHub
Release. One-time setup before the first tag:

- Register CAMBER as a **PyPI trusted publisher** for this repo + workflow, and create a GitHub
  Actions environment named `pypi`.
- GHCR push uses the built-in `GITHUB_TOKEN` (no secret needed).

Then a release is just `git tag -a v<ver> -m "…" && git push origin v<ver>`.

## Manual path — tag and GitHub release

```sh
git tag -a v<ver> -m "CAMBER v<ver>" && git push origin v<ver>
gh release create v<ver> --notes-from-tag   # or paste the CHANGELOG section
```

## Going public (one-time)

- Flip the GitHub repo to public when ready (Settings → change visibility).
- The package is already clean-room and Apache-2.0; any sensitive client trend data
  and proprietary predecessor material live outside the package and are git-ignored, so
  they are not published by either GitHub or PyPI.
