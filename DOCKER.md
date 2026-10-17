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
`/data/store`), `CAMBER_API_HOST` (`0.0.0.0` in the container), `CAMBER_API_PORT` (`8080`),
`CAMBER_API_ALLOWED_HOSTS` (`localhost,127.0.0.1`).
The container binds all interfaces *inside its own network namespace*; the API is read-only and
should still be reached through compose's port mapping / a reverse proxy, not exposed raw to an
untrusted network (see [docs/SECURITY.md](docs/SECURITY.md)).

Since 0.103 the API refuses a request whose `Host` header is not on its allowlist (403, a
DNS-rebinding defence), and binding `0.0.0.0` without an allowlist refuses to start. The image's
default answers `localhost` and `127.0.0.1` on any published port. If clients reach it by another
name (a proxy, a DNS alias, the host's LAN address), add it:

```sh
docker run --rm -p 8080:8080 -v "$PWD/data/store:/data/store:ro" \
  -e CAMBER_API_ALLOWED_HOSTS=localhost,127.0.0.1,camber.example.org ghcr.io/yroussev/camber:latest
```

`CAMBER_API_ALLOWED_HOSTS='*'` turns the check off; do that only behind a proxy that checks
`Host` itself. The container has no authentication by default and logs a warning saying so;
`CAMBER_API_AUTH=token` with a `CAMBER_API_TOKEN` of 16 or more characters requires that token
(`Authorization: Bearer`, or `/ui?token=...` once for a session cookie) on every route except a
bare `GET /health`.

Since 0.104 the API also refuses cross-site browser requests (403): a `Sec-Fetch-Site:
cross-site` header, or an `Origin` whose host is not on the allowlist. The `HEALTHCHECK` and
`curl` send neither header and pass. Behind a proxy that rewrites `Host`, still list the public
name the browser uses, since it arrives in `Origin`. Token auth became the default in 0.107 for
loopback binds only, so the image (bound to `0.0.0.0`) keeps no authentication by default, with
the warning; a container started with `CAMBER_API_HOST=127.0.0.1` gets token auth.

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
