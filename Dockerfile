# syntax=docker/dockerfile:1
#
# Multi-stage build for CAMBER.
#
#   - `runtime` (default target): a slim, library-only image — just the installed
#     `camber` package plus its runtime deps (no source tree, tests, examples, or
#     docs). It serves the read-only HTTP API over a mounted Parquet store.
#   - `test`: adds the test suite + pytest; running it is a clean-room proof that the
#     built wheel works outside the author's machine. Used by `docker compose run tests`.
#
# Python is pinned to 3.10-slim (multi-arch: amd64 + arm64); the published wheels need
# no compiler in the image. See DOCKER.md.

############################  build: produce the wheel  ############################
FROM python:3.10-slim AS build
WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# README/LICENSE/NOTICE are referenced by the package metadata, so they must be present.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY camber ./camber
RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

############################  runtime: slim, default  ############################
FROM python:3.10-slim AS runtime
LABEL org.opencontainers.image.title="CAMBER" \
      org.opencontainers.image.description="Vendor-neutral BAS trend analysis: FDD, M&V, RCx" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/yroussev/camber"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CAMBER_STORE=/data/store \
    CAMBER_API_HOST=0.0.0.0 \
    CAMBER_API_PORT=8080
WORKDIR /app
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl
# Non-root; /data/store is the mount point for the Parquet store the read API serves.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/store \
    && chown -R appuser:appuser /data
USER appuser
EXPOSE 8080
# Liveness: the read API answers GET /health.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:'+os.environ.get('CAMBER_API_PORT','8080')+'/health',timeout=2).status==200 else 1)"
# Default: serve the read-only API over the mounted store (host/port/store from env).
CMD ["python", "-m", "camber.api.server"]

############################  test: proves the built wheel  ############################
FROM build AS test
ENV PIP_NO_CACHE_DIR=1
COPY tests ./tests
COPY examples ./examples
RUN pip install --no-cache-dir /dist/*.whl pytest
CMD ["pytest", "-q"]
