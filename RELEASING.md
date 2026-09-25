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

### Stacked releases — push tags oldest first, one at a time

When several held versions go out together, push the tags **in version order and wait for each
release run's `image` job to finish before pushing the next**. Every run pushes the GHCR image's
`:latest` tag, and runs for different tags proceed in parallel, so if an older version's image
finishes last it silently takes `:latest`. The `release` workflow's concurrency group is per-tag
and never cancels, so waiting is the only ordering there is.

### After the release — conda-forge

`regro-cf-autotick-bot` opens a version-bump PR on
[`conda-forge/camber-toolkit-feedstock`](https://github.com/conda-forge/camber-toolkit-feedstock)
a few hours after the PyPI upload. Review it before merging: the bot bumps only version + `sha256`
and does not notice a changed dependency, so compare the recipe's `requirements.run` with
`pyproject.toml`. Then mirror the version + hash into `deploy/conda/recipe.yaml`. See
[docs/DEPLOY.md](docs/DEPLOY.md#conda-forge).

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
