# Deployment

Reference deployment artifacts for running the CAMBER **read-only** API over a Parquet store.
These are starting points, not turnkey infra, and nothing here publishes anything.

The API serves GET only (`/about` `/health` `/sites` `/points` `/history`) and never writes to the
BAS/OT. Keep it behind the cluster boundary or an authenticating ingress — see
[SECURITY.md](SECURITY.md).

## Docker / Compose

The primary path. See [../DOCKER.md](../DOCKER.md): `docker compose up api` serves the API over
`./data/store`; the multi-arch image is at `ghcr.io/yroussev/camber`.

## Kubernetes

[`deploy/k8s/camber-api.yaml`](../deploy/k8s/camber-api.yaml) — a namespace, a read-only PVC for the
store, a 2-replica non-root Deployment (readiness/liveness on `/health`, resource limits, read-only
root FS), and a `ClusterIP` Service.

```sh
kubectl apply -f deploy/k8s/camber-api.yaml
# populate the PVC out-of-band (the API only reads); front the Service with an auth ingress.
```

The store is a `ReadOnlyMany` PVC: a separate writer (a batch job running the analysis + `ParquetStore`)
populates it; the API pods mount it read-only.

## conda-forge

[`deploy/conda/meta.yaml`](../deploy/conda/meta.yaml) is a submission-ready recipe (`noarch: python`,
the runtime deps, the `camber` entry point, and a `run_constrained` for the optional `[ml]` extra).
Pinned to the current version; the only field to fill is `sha256`, from the published sdist:

```bash
curl -sL https://pypi.org/pypi/camber-toolkit/0.4.0/json \
  | jq -r '.urls[] | select(.packagetype=="sdist") | .digests.sha256'
```

Then submit via [`conda-forge/staged-recipes`](https://github.com/conda-forge/staged-recipes) (a PR
that creates the feedstock — a **repo-owner action**). Once the feedstock exists, its bot opens
version-bump PRs automatically on each PyPI release. Until then, install from PyPI:
`pip install camber-toolkit`.

## Docs site (GitHub Pages)

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) builds the MkDocs site
(`mkdocs build --strict`) and deploys it to GitHub Pages on every push to `main` that touches `docs/`
or `mkdocs.yml`. Enabling it is a **one-time repo-owner action**: *Settings → Pages → Source: GitHub
Actions*. Build locally with `pip install -e .[docs] && mkdocs serve`.

## Hosted demo

The site can carry a self-contained demo — the runnable examples on public CC-BY datasets
(`examples/lbnl_fdd`, `examples/bdg2`) plus a rendered site report — as static Pages content, needing
no infrastructure beyond the Pages deploy above.

## Community (repo-owner actions)

- Enable **GitHub Discussions** (*Settings → Features → Discussions*).
- Confirm the issue/PR templates surface; set repo topics/description.
- Track the **PEP-541** request to reclaim the bare `camber` PyPI name (`camber-toolkit` remains the
  permanent distribution name regardless).
