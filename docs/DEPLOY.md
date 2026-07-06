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

## conda-forge (skeleton)

[`deploy/conda/meta.yaml`](../deploy/conda/meta.yaml) is a **recipe skeleton**, not a submission —
`noarch: python`, the runtime deps, the `camber` entry point. If a conda-forge feedstock is pursued,
fill `url`/`sha256` from the published PyPI sdist and submit via `staged-recipes`. Until then, install
from PyPI: `pip install camber-toolkit`.

## Hosted demo

Not included (would need infra + a public dataset). The runnable examples on public CC-BY datasets
(`examples/lbnl_fdd`, `examples/bdg2`) are the reproducible stand-in.
