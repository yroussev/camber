# MCP server: `camber mcp`

`camber mcp` lets an AI client read CAMBER's analyses. It is a
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server: a client such as Claude
Desktop or the `claude` command line starts it as a subprocess and talks to it over stdin and
stdout. The client sees CAMBER's capabilities as **tools** (list the facilities and their points,
check data coverage and sensor trust, run the rules, read the RCx report, and so on) and a few
**prompts** (ready-made starting points such as a facility triage). Every tool reads the store;
none writes. No tool returns raw time-series values: results are analyses, counts and time spans,
so to look at the samples behind a span, plot the trend in your own tools.

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
  unreadable or malformed no facility is served at all, with a notice on every result. A
  facility directory that was renamed, moved or copied is caught too (0.111, #312): while the
  registry records a research-only facility whose directory is missing, no facility is served,
  and a facility whose part files are those of a research-only facility in the same store is
  refused as research-only. These checks guard against accidents and casual relabelling, not
  against someone who edits the registry or the stored data on purpose: the store's integrity
  is the operator's responsibility. Keep the store writable only by the people who ingest into
  it, and change it with CAMBER's own commands (`camber datasets remove`, `camber facility ...`)
  rather than by hand.
- **A trust floor.** `--min-trust` sets the minimum data-trust score the tools apply. A check
  that declines because an input scores below it is announced in a NOTICE line, with the count,
  the rules and the inputs with their scores; `sensor_trust` reports the same scores and marks
  exactly those inputs `below_floor`. A finding resting on an input whose configured sensor
  reference disagrees is not declined but marked `conditional_on` that sensor.
- **Model-sized results.** A tool returns one page of each list by default, so a typical result
  stays under about 100 KB (25k tokens); `limit` and `offset` ask for other pages, and a notice
  says what was left out and how to get it. A reply over 2 MB is refused as too large, and a
  call has a time budget (see **Results** below).
- **Citations.** A tool's summary cites fact ids such as `[F1@ctx-1a2b3c]`, which point at the
  values in the result. The token after the context depends on the call, the allowlist, the run
  config, the trust floor and the stored data, so two servers (or one server before and after an
  ingest) never give one id to different values (0.111, #295). The stored data counts by the
  size and modification time of each file the call reads and of the point catalog, so a part
  file rewritten in place gives new ids too (#312); a restart, or a config at another path,
  does not.

**Prompt-argument completion (0.111, #304).** The server declares the `completions` capability and
answers `completion/complete` for a prompt's arguments in both revisions: `facility_id` from the
allowed facilities a call would read (never a research-only one), `equip` and `equips` (its last
comma-separated id) from the stored equipment of the facility already chosen
(`context.arguments.facility_id`), at most 100 values, those starting with what was typed first.
A name that is not one clean line of at most 200 characters is never offered, nor one that the
summaries' sanitiser would change (HTML or markdown markup, a URL, a fact-id bracket) or that
starts with `NOTICE` (0.111, #307), and any other argument completes to nothing. A host shows these to the person filling in a prompt. The model
itself cannot list or start a prompt in most hosts, only a person can.

Nothing else is offered: no resources, subscriptions, roots, sampling or logging.

## Starting it

```
camber mcp --store PATH (--allow FACILITY [--allow FACILITY ...] | --allow-all-active)
           [--config PATH ...] [--min-trust 0.5] [--max-concurrent 2] [--max-queued 8]
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
  in every result. The same holds while it records a research-only facility whose directory is
  missing. Repair the registry (restore it, or re-ingest) and restart.
- A warning on stderr names each `--allow` facility that will be refused: research-only data, a
  catalog-dataset facility with no registry entry, or every facility while the registry is
  damaged.
- One of `--allow` and `--allow-all-active` is required: the server does not start without an
  allowlist (exit 2).
- `--config PATH` is a run or report config (`.json`, or `.yaml` / `.yml` with the `yaml` extra)
  for one facility, used by the tools that run rules or reports. Repeat it for more facilities,
  one config each (two configs for the same facility are refused), so `--allow-all-active` can
  serve tuned results for every facility. Each config is checked at startup the way `camber run`
  checks it: a file that cannot be parsed (a trailing comma, an empty file, YAML without the
  extra), a config that is not an object or repeats a key, a wrong shape, an unknown rule or
  rule parameter, a parameter value that is not a finite number or is outside its documented
  range without a `"basis"` for it, a bad `units`, `ventilation` (a zones file that cannot be
  read included) or `report.rcx` block, a facility the store does not hold, a config that names
  no facility or one that is not allowed, or any other suffix stops the server (exit 2) with one
  line on stderr naming the file and the problem. Each config is read once, at startup: the
  tools use that copy, so editing the file while the server runs changes nothing until a
  restart (0.111, #308).
- A store-backed config's `source.store` must be the `--store` directory (a relative path is
  resolved from the config's own directory; any spelling of the same directory, a symlink
  included, is accepted). A config that points at another store is refused at startup: the
  tools read only `--store`. `camber datasets config ID --store PATH --out FILE` writes a config
  that points at the store you name.
- Without a `--config` for it, a facility ingested from a catalog dataset (`ds-...`) runs with
  that dataset's packaged config template (the one `camber datasets config` writes), so its
  results and rankings are the ones the template was tuned for, not every rule at its defaults.
  The template is read from the installed package: nothing is fetched or written. A line on
  stderr at startup, and a banner on every result that used it, names the dataset. Pass a
  `--config` for the facility to use your own. Any other facility without a config runs every
  stored equipment class with every built-in rule at its defaults, with a banner saying so.
- `--min-trust` is a number from 0 to 1 (default 0.5).
- `--max-concurrent N` is how many tool calls run at once (1 to 64, default 2), each in its own
  worker process; `--max-queued N` is how many more may wait for a free worker (0 to 1024,
  default 8). Both take plain digits; anything else, or a value out of range, exits 2. A call
  beyond that is refused at once with the tool error `busy` (retry shortly). Each worker holds
  the memory of the call it runs, so raise these with care on a large store.

Any other launch mistake (a missing or unreadable store, `--min-trust` out of range, an empty
`--allow`) also exits 2 with one line on stderr.

The configuration is fixed for the life of the process. The client cannot change the store, the
allowlist or the trust floor; to serve something else, start another server.

Matplotlib (which `rcx_report` loads) keeps its font cache in a temporary directory
(`camber-mcp-mpl-<server pid>-*`) removed when the server exits, unless `MPLCONFIGDIR` is set, so
a call writes nothing under your home directory (0.111, #304). `SIGTERM` and `SIGHUP` stop the
server like Ctrl-C: a log line, every call stopped, the directory removed, exit status 143 or 129.
No process can clean up after `SIGKILL`; the next `camber mcp` to start removes the directories of
servers that are no longer running (#309).

The server writes nothing but MCP messages to stdout; its log (startup, one line per tool call
with the outcome and the time taken) goes to stderr. Anything a tool prints by mistake is sent to
stderr as well, so it cannot corrupt the stream.

**Concurrency, cancellation and shutdown.** Each tool call runs in a worker process (a fresh
Python that loads the tools once and is reused, about half a second to start and 110 MB idle;
one is started with the server and a replacement is started as soon as one is killed). Every
other message -- `ping`, `server/discover`, `initialize`, the lists, `prompts/get`,
`completion/complete` and `notifications/cancelled` -- is answered at once, even while long calls run, so replies to
concurrent requests may come in a different order from the requests; each carries its own id.
The worker of a call is killed with every process it started (matplotlib's font scan runs some),
and its CPU and memory released, when:

- the call runs past its time cap (120 s, answered within about a second of it): the reply is
  `timeout` ("... and was stopped");
- the client sends `notifications/cancelled` for it: there is no reply, as the specification
  asks (a call still waiting for a worker is simply dropped);
- the client closes stdin, or closes its end of stdout: every running call is stopped and the
  server exits within about a second. Read the replies you want before closing stdin.

A worker is also replaced after a call during which it grew past 1 GiB, so an idle server does not
hold the memory of its largest call. A worker that ends on its own (a crash, or the system stopping
it for lack of memory) is replaced: when it was idle, before any call is given to it; when it was
running a call, that call answers `unavailable` with `"retryable": true` (retry it, and narrow it
if it fails again).

**Waiting for a worker.** The time cap counts from when a call reaches a worker, not from when it
was sent. While `--max-concurrent` calls are running, every further tool call waits in the queue,
even a quick one such as `rule_params`, `check_citations` or `dataset_search`: behind two long
calls it can wait up to the time cap, and the last of a full queue up to (`--max-queued` /
`--max-concurrent` + 1) times the cap (5 × 120 s with the defaults). A wait over a second is logged
on stderr. Give quick calls room with a higher `--max-concurrent`, or set the client's request
timeout above the longest wait you allow.

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
`"\ud800"` (valid JSON syntax, but not a character). The refusal of a line over 1 MiB carries the
request's `id` when the line's first bytes name it (top level, once, a complete string or
integer), else `null`. An integer argument may be sent as a whole-number float (`5.0` is the
integer 5, as JSON Schema says); `5.5` is `invalid_params`.

**Errors.** Following the specification, a problem the model can fix by changing its call is a
tool result with `isError: true` and a `code: message` text (`invalid_params`, `not_found`,
`not_allowed`, `refused_licence`, `too_large`, `timeout`, `unavailable`, `busy`); the code is also
in `_meta["camber/error"]`. `busy` means the server is already running and queueing as many calls
as it was started for; its `data` is `{"retryable": true, "max_concurrent": N, "max_queued": M}`. `unavailable` means the stored data could not be read: its `data` says
whether a retry will likely work (`"retryable": true`, e.g. a part file a concurrent
`camber datasets ingest` replaced) or not (a damaged or unreadable file), and names the facility.
It is also the answer when the worker running the call ended under it (`"retryable": true`).
A date the calendar does not have (`2025-02-30`) is `invalid_params`; a window outside the
stored data is an ordinary, empty result with a notice. Everything else is a JSON-RPC error:

| Code | When |
|---|---|
| -32700 | the line is not JSON or not UTF-8 |
| -32600 | not a JSON-RPC 2.0 request object: a batch, a line over 1 MiB, a duplicate key, a lone surrogate, an integer too long to read, `jsonrpc` not `"2.0"`, a missing `method`, an `id` that is not a string or integer, a second `initialize`, a `tools/call` with the id of a call still in progress (#309) |
| -32601 | an unknown method, or one the request's protocol revision does not have |
| -32602 | bad `params`: missing or malformed `_meta` keys, no handshake and no `_meta`, an unknown tool or prompt, a prompt argument missing, unknown or not a string, an unknown cursor, a completion `ref` that is not a known prompt (`ref/resource` included) or an unknown argument |
| -32022 | `UnsupportedProtocolVersion`, with the supported versions in `data` |
| -32603 | an internal failure, including a reply that could not be serialised; names no local path |

When the request's id cannot be read, the error carries `"id": null`. No message, however
malformed, ends the server: every failure is logged on stderr and answered.

**Results.** A tool result has two text blocks: the summary (with any notices first, as lines
starting with `NOTICE:`) and the result as compact JSON; the same result is in
`structuredContent`. Only a notice starts a line with `NOTICE:`: the summary is one line, and
names from the data in it are quoted and stripped of control, bidi and invisible characters
(0.111, #291; see [CAPABILITY-REGISTRY.md](CAPABILITY-REGISTRY.md)). The fact index is in
`_meta["camber/facts"]`, the notices in `_meta["camber/banners"]` and the truncation flag in
`_meta["camber/truncated"]`.

**Sizes** (0.111, #299). Both revisions say a tool that returns `structuredContent` SHOULD also
return it serialized as JSON in a text block, for clients that pass only `content` to the model,
so the JSON goes out twice: once, compact, in `content` and once in `structuredContent`. Rather
than leave one out, the server keeps results small:

- **Pages.** Every list comes a page at a time: `rule_run` 25 findings, `sensor_trust` 100
  points, `coverage` 50 rows per list, `points` 250, `facilities` 100, `rule_params` 25 rules (10
  with `detail`), `check_citations` the failing sentences only (every sentence and
  `verified_text` with `detail: true`), `rcx_report` its `top_n` ranked issues (default 8) with
  their facts and the first 20 appendix entries of its fact index. Pass `limit` (up to 1,000)
  and `offset` for more; `rcx_report` takes `top_n` and `offset` for issues and `limit` for the
  appendix. Counts such as `count`, `n_points` and the RCx KPIs are of the whole list.
- **`truncated` means something was left out.** Whenever a list in the result is not the whole
  list, `_meta["camber/truncated"]` is `true` and a notice says which list, the rows shown, the
  total and the call that gets the rest.
- **The cap counts the whole reply.** A reply larger than 2 MB, counted as sent (the summary,
  the JSON text block, `structuredContent` and `_meta` together), is the tool error
  `too_large`, with `{"bytes": ..., "max_bytes": ...}`; ask for a smaller page.
- **A shorter tool list.** `tools/list` leaves out what a client does not need to pick or call a
  tool: the `$schema` of JSON Schema 2020-12 (the dialect both revisions assume), the
  `annotations.title` that repeats the tool's `title`, and the `description` annotations inside
  each `outputSchema` (it validates the same). The server still checks every result against the
  full schema.

On a 650-point fleet, typical calls now return 50 to 100 KB (they were 0.3 to 1.2 MB), and
`tools/list` is about 43 KB.

## From Python

<!-- fence: skip (blocks on stdin; needs a store) -->
```python
from camber.capabilities import default_registry
from camber.mcp import McpServer, build_context, serve_stdio

ctx = build_context("store", allow=["north-campus"])
serve_stdio(McpServer(default_registry(), ctx))
```

`McpServer.handle(message)` answers one decoded message without any I/O, for embedding the server
in another transport. Used like this, `serve` and `serve_stdio` answer one message at a time in
the calling process, as before; pass `workers=WorkerPool("camber.capabilities:default_registry")`
for what `camber mcp` does (tool calls in killable worker processes, built from that registry
factory). `handle(message, defer=True)` returns a tool call as a `ToolCall` instead of running
it, and `McpServer.finish(call, outcome)` turns the outcome into the reply.
