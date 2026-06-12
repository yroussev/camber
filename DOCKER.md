# Running CAMBER in containers

CAMBER ships a multi-stage [`Dockerfile`](Dockerfile) and a [`docker-compose.yml`](docker-compose.yml).
The default image is a **slim runtime**: only the installed `camber` package and its runtime
dependencies — no source tree, tests, examples, or docs — serving the read-only HTTP API.

## Pull the published image

Multi-arch images (linux/amd64 + linux/arm64) are published to GHCR on each release tag:

```sh
docker pull ghcr.io/yroussev/camber:latest      # or a pinned :0.1.0
```

## Serve the read-only API over a store

The runtime image runs the read-only HTTP API (`camber.api.server`) over a Parquet store
mounted at `/data/store`. It serves **GET only** — `/about`, `/health`, `/sites`, `/points`,
`/history`:

```sh
# with compose (mounts ./data/store read-only, publishes :8080)
docker compose up api
curl localhost:8080/about

# or directly
docker run --rm -p 8080:8080 -v "$PWD/data/store:/data/store:ro" ghcr.io/yroussev/camber:latest
```

Config is via environment variables (already set in the image): `CAMBER_STORE` (default
`/data/store`), `CAMBER_API_HOST` (`0.0.0.0` in the container), `CAMBER_API_PORT` (`8080`).
The container binds all interfaces *inside its own network namespace*; the API is read-only and
should still be reached through compose's port mapping / a reverse proxy, not exposed raw to an
untrusted network (see [docs/SECURITY.md](docs/SECURITY.md)).

## Run any command / a shell

```sh
docker compose run --rm tool camber --help
docker run --rm -it ghcr.io/yroussev/camber:latest python
```

## Prove the build (run the suite)

The `test` stage installs the built wheel plus `pytest` and runs the suite — a clean-room
check that the distributable package works outside the author's machine:

```sh
docker compose run --rm tests          # uses the `test` build target
# or
docker build --target test -t camber-test . && docker run --rm camber-test
```

## Build locally

```sh
docker build -t camber:dev .                       # default `runtime` target (slim)
docker build --target test -t camber:test .        # the test image
docker buildx build --platform linux/amd64,linux/arm64 -t camber:multi .   # multi-arch
```

## Contributor dev container

A [`.devcontainer`](.devcontainer/devcontainer.json) gives a one-click setup (VS Code "Reopen
in Container" / GitHub Codespaces): Python 3.11 with `pip install -e .[dev,brick]` and pytest
wired up.

See [RELEASING.md](RELEASING.md) for how tags drive the automated PyPI + GHCR publish.
