# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — use GitHub's "Report a vulnerability"
button on the repository's **Security** tab (private vulnerability reporting),
rather than opening a public issue. We aim to acknowledge reports within a few days.

## Scope and notes

CAMBER is an analysis library that reads data files and can serve a **read-only**
local HTTP API (`camber.api.server`). Please note:

- The read API (`camber serve`) is intended for trusted/local use. Bound to loopback (the
  default) it requires a per-run access token since 0.107 (`--auth none` turns that off); bound
  to any other address it has **no authentication by default** unless you pass `--auth token`.
  It refuses requests whose `Host` header is not on its allowlist (a DNS-rebinding defence). Do
  not expose it to untrusted networks; see [docs/SECURITY.md](docs/SECURITY.md) section 3.
- The Haystack ingest client issues outbound HTTP via an injectable transport you
  supply; treat credentials/tokens as you would any secret.
- CAMBER does not execute building-supplied data as code, but as with any tool,
  validate inputs from untrusted sources.

## Supported versions

Pre-1.0: only the latest `main` is supported. Once 1.0 is released, this section
will list supported release lines.
