# MCP server: `camber mcp`

`camber mcp` lets an AI client read CAMBER's analyses. It is a
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server: a client such as Claude
Desktop or the `claude` command line starts it as a subprocess and talks to it over stdin and
stdout. The client sees CAMBER's capabilities as **tools** (list the facilities, run the rules,
read trends, and so on) and a few **prompts** (ready-made starting points such as a facility
triage). Every tool reads; none writes.

> `camber mcp` and its Python API (`camber.mcp`) are **provisional** (0.110): names, options and
> the result layout may still change in a minor release. See [API-STABILITY.md](API-STABILITY.md).

## What it serves

The tools and prompts come from CAMBER's capability registry (`camber.capabilities`). Each tool
has a JSON Schema for its arguments and for its result, and the same rules apply to every call:

- **Read-only.** No tool changes the store, a config or a baseline; runs are not saved.
- **The facility allowlist.** A tool reads only the facilities named when the server was started
  (see below). Any other facility is refused, even if it is in the same store.
- **Research-only data is refused.** Data under a non-commercial or no-derivatives licence
  (the catalog's research-only tier) is never served to an AI client, with no override. The
  check fails closed: a facility named for a research-only catalog dataset is refused whatever
  the store's facility registry says, and while that registry (`_facilities.json`) is missing,
  unreadable or malformed no facility is served at all, with a notice on every result.
- **A trust floor.** `--min-trust` sets the minimum data-trust score the tools apply.
- **Size and time caps.** A large result is truncated, with a notice, or refused as too large; a
  call has a time budget.
- **Citations.** A tool's summary cites fact ids such as `[F1@ctx]`, which point at the values in
  the result.

Nothing else is offered: no resources, subscriptions, roots, sampling or logging.

## Starting it

```
camber mcp --store PATH (--allow FACILITY [--allow FACILITY ...] | --allow-all-active)
           [--config PATH] [--min-trust 0.5]
```

- `--store PATH` is the ParquetStore directory the tools read. A
  [portfolio workspace](PORTFOLIO.md) root works too: its store is used.
- `--allow FACILITY` names a facility the tools may read; repeat it for more. An id that is not in
  the store (yet) is accepted with a warning on stderr.
- `--allow-all-active` allows every facility that is **active** in the store when the server
  starts (an unregistered facility counts as active; suspended, provisioning, offboarding and
  archived ones are left out). A facility activated later needs a restart. A store with no active
  facility is refused. A facility named for a catalog dataset (`ds-...`) that has no registry
  entry is not counted as active. If the facility registry is missing, unreadable or malformed,
  no facility counts as active: the server starts with nothing to serve and says so on stderr and
  in every result. Repair the registry (restore it, or re-ingest) and restart.
- A warning on stderr names each `--allow` facility that will be refused: research-only data, a
  catalog-dataset facility with no registry entry, or every facility while the registry is
  damaged.
- One of `--allow` and `--allow-all-active` is required: the server does not start without an
  allowlist (exit 2).
- `--config PATH` is a run or report config (JSON, or YAML with the `yaml` extra) for the tools
  that run rules or reports.
- `--min-trust` is a number from 0 to 1 (default 0.5).

The configuration is fixed for the life of the process. The client cannot change the store, the
allowlist or the trust floor; to serve something else, start another server.

The server writes nothing but MCP messages to stdout; its log (startup, one line per tool call
with the outcome and the time taken) goes to stderr. It exits when the client closes stdin.
Anything a tool prints by mistake is sent to stderr as well, so it cannot corrupt the stream.

No extra is needed: the server uses the standard library only. `pip install "camber-toolkit[mcp]"`
is accepted and installs nothing more; it marks the intent.

## Connecting a client

A client needs the command and its arguments. Use absolute paths: a client does not start the
server in your project directory.

**The `claude` command line**, from a terminal:

```
claude mcp add camber -- camber mcp --store /path/to/store --allow north-campus
```

or, to share it with a project, in the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "camber": {
      "command": "camber",
      "args": ["mcp", "--store", "/path/to/store", "--allow", "north-campus"]
    }
  }
}
```

**Claude Desktop**: the same `mcpServers` entry in `claude_desktop_config.json` (Settings,
Developer, Edit Config), then restart the app. If `camber` is not on the app's `PATH`, give the
full path of the `camber` script in your virtual environment as `command`, or use that
environment's Python with `"args": ["-m", "camber.cli", "mcp", ...]`.

**Other clients**: any MCP client that launches a stdio server takes the same command and
arguments.

## Protocol

`camber mcp` speaks two revisions of the protocol, so newer and older clients both connect:

- **2026-07-28.** Stateless: each request carries its protocol version and client capabilities in
  `params._meta`. Two keys are required, `io.modelcontextprotocol/protocolVersion` (the string
  `"2026-07-28"`) and `io.modelcontextprotocol/clientCapabilities` (an object, `{}` if the client
  has none); `io.modelcontextprotocol/clientInfo` is optional and, when sent, an object. The server implements `server/discover`, marks every result `resultType: "complete"`
  with its identity in `_meta`, and puts `ttlMs` and `cacheScope` on `server/discover`,
  `tools/list` and `prompts/list`. An unknown version gets `UnsupportedProtocolVersion` (-32022)
  with the list of supported ones.
- **2025-11-25.** The `initialize` handshake, then `notifications/initialized` and `ping`.
  2025-06-18 is accepted as well; a client asking for an older version is offered 2025-11-25.
  `initialize` needs `protocolVersion` and a `capabilities` object (`clientInfo`, when sent, is an
  object), and is accepted once per process: a second one is refused (-32600) and the session
  keeps the version it negotiated.

A request with the 2026-07-28 `_meta` is always served statelessly; `initialize` starts a
2025-11-25 session for the rest of the process. A request with neither is refused (-32602).
Messages are one JSON object per line in UTF-8; batches are refused, and so is a line over 1 MiB,
an object that repeats a key, and a string holding a lone UTF-16 surrogate escape such as
`"\ud800"` (valid JSON syntax, but not a character).

**Errors.** Following the specification, a problem the model can fix by changing its call is a
tool result with `isError: true` and a `code: message` text (`invalid_params`, `not_found`,
`not_allowed`, `refused_licence`, `too_large`, `timeout`); the code is also in
`_meta["camber/error"]`. Everything else is a JSON-RPC error:

| Code | When |
|---|---|
| -32700 | the line is not JSON or not UTF-8 |
| -32600 | not a JSON-RPC 2.0 request object: a batch, a line over 1 MiB, a duplicate key, a lone surrogate, an integer too long to read, `jsonrpc` not `"2.0"`, a missing `method`, an `id` that is not a string or integer, a second `initialize` |
| -32601 | an unknown method, or one the request's protocol revision does not have |
| -32602 | bad `params`: missing or malformed `_meta` keys, no handshake and no `_meta`, an unknown tool or prompt, a prompt argument missing, unknown or not a string, an unknown cursor |
| -32022 | `UnsupportedProtocolVersion`, with the supported versions in `data` |
| -32603 | an internal failure, including a reply that could not be serialised; names no local path |

When the request's id cannot be read, the error carries `"id": null`. No message, however
malformed, ends the server: every failure is logged on stderr and answered.

**Results.** A tool result has two text blocks, the summary (with any notices first, as lines
starting with `NOTICE:`) and the result as JSON, and the same result as `structuredContent`. The
fact index is in `_meta["camber/facts"]`, the notices in `_meta["camber/banners"]` and the
truncation flag in `_meta["camber/truncated"]`.

## From Python

<!-- fence: skip (blocks on stdin; needs a store) -->
```python
from camber.capabilities import default_registry
from camber.mcp import McpServer, build_context, serve_stdio

ctx = build_context("store", allow=["north-campus"])
serve_stdio(McpServer(default_registry(), ctx))
```

`McpServer.handle(message)` answers one decoded message without any I/O, for embedding the server
in another transport.
