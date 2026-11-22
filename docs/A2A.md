# A2A server: `camber a2a`

`camber a2a` (provisional, 0.113, #280) lets another AI agent ask CAMBER for work over the
[Agent2Agent (A2A) protocol](https://a2a-protocol.org/latest/specification/). Where
[`camber mcp`](MCP.md) gives one AI client CAMBER's tools, `camber a2a` makes CAMBER a peer agent:
the other agent sends a message naming a skill, and gets back a task whose artifact is the result.

It is a thin adapter over the same [capability registry](CAPABILITY-REGISTRY.md) as `camber mcp`,
so the two expose the same checks: the facility allowlist, research-only data always refused,
input validated against the capability's schema, no-persist runs, time and size caps, and cited
facts on every answer. It is a separate process: `camber serve` stays GET-only and does not
advertise an agent card.

Standard library only. The `a2a` extra is an empty marker, like `mcp`.

## What it serves

| Skill id | Runs capability | What it returns |
|---|---|---|
| `rcx-report` | `rcx_report` | The retro-commissioning report of one facility as data: KPIs, the ranked issues and the report's fact index, with a cited summary. A full rule run: the slow one. |
| `findings` | `rule_run` | The fault-detection findings of one facility, worst first, the counts per severity and the rules skipped for missing inputs, with a cited summary. |

The input of each skill is the input of its capability, as
[CAPABILITY-REGISTRY.md](CAPABILITY-REGISTRY.md) and the `camber mcp` tool list describe it:
`facility_id` is required; `findings` also takes `rules`, `equips`, `start`, `end`, `params`,
`min_severity`, `limit` and `offset`, and `rcx-report` takes `equips`, `start`, `end`, `top_n`,
`offset` and `limit`. Unknown keys are refused.

Every task is **read-only**: nothing a skill does writes fault history, a report file, a
manifest entry or a cache.

## Starting it

```
camber a2a --store PATH (--allow FACILITY [--allow FACILITY ...] | --allow-all-active)
           [--config PATH ...] [--min-trust 0.5] [--host 127.0.0.1] [--port 8770]
           [--allow-host NAME ...] [--max-concurrent 2] [--max-queued 8]
```

The launch model is `camber mcp`'s ([CLI.md](CLI.md#mcp-server-camber-mcp-provisional-0110)):

- `--store`, `--allow` / `--allow-all-active`, `--config` and `--min-trust` mean what they mean
  for `camber mcp`. Only the allowed facilities are served; a research-only facility is never
  served, even when allowed (a warning at startup names it).
- `--max-concurrent N` (1 to 64, default 2) tasks run at once, each in its own worker process;
  `--max-queued N` (0 to 1024, default 8) more may wait. A task beyond that is rejected as
  `busy`. A task past its time cap (120 s) is stopped and fails with `timeout`.
- `--host` (default `127.0.0.1`) and `--port` (default 8770; 0 picks a free port) set the bind.
  Binding every interface (`0.0.0.0`, `::`) needs `--allow-host NAME` with the names peers use to
  reach the server. `--allow-host` (repeatable) also adds a proxy or DNS name to the `Host`
  allowlist.
- Every launch mistake (a missing store, a bad config, a value out of range, a wildcard bind with
  no `--allow-host`, a short `CAMBER_A2A_TOKEN`, a port in use) exits 2 with one line on stderr.
- Ctrl-C, `SIGTERM` and `SIGHUP` stop it (exit status 130, 143 and 129). Running tasks are
  stopped and marked cancelled.

At startup it prints the agent card URL and the JSON-RPC endpoint on stdout, and logs (one line
per task) on stderr.

## Authentication

Every request needs `Authorization: Bearer <token>`: 401 without one, 403 for a wrong one.

- The token is `$CAMBER_A2A_TOKEN` when that is set (at least 16 characters, no space). Otherwise
  `camber a2a` makes a fresh 256-bit token per run and saves it in a launch file only you can
  read, `a2a-<port>.url`, beside the `serve` and `lab` launch files (`$XDG_RUNTIME_DIR/camber`,
  else `~/.config/camber`). The file holds the endpoint URL with the token as its `token=`
  query value; a client sends that value as the bearer token. The file is removed when the
  server stops.
- The **agent card** at `/.well-known/agent-card.json` needs no token when the server is bound
  to loopback (`127.0.0.1`, `::1`, `localhost`). On any other bind the card needs the token
  too.
- Every request's `Host` must be on the allowlist (DNS rebinding defence), and a browser request
  from another site (`Sec-Fetch-Site: cross-site`, or an `Origin` not on the allowlist) is
  refused. A peer agent sends neither header, so it passes.
- Beyond loopback the traffic is plain HTTP: put an authenticating TLS reverse proxy in front, as
  for `camber serve` ([SECURITY.md](SECURITY.md)).

Every token holder sees every allowed facility; there are no per-peer scopes yet.

## The agent card

`GET /.well-known/agent-card.json` returns the card: name, description, the one interface
(`protocolBinding: JSONRPC`, `protocolVersion: 1.0`, the URL `http://<Host>/a2a`), the version
(CAMBER's), the bearer `securitySchemes` and `securityRequirements`, the two skills, and
`capabilities`:

- `streaming: false` and `pushNotifications: false`: poll `GetTask` for a long task.
- `extendedAgentCard: true`: `GetExtendedAgentCard` (authenticated) returns the card with each
  skill's examples naming the served facilities. The public card names none.
- `extensions`: [`grounded-facts`](a2a/grounded-facts.md), not required.

## Sending a task

`POST /a2a` with `Content-Type: application/json`, `A2A-Version: 1.0` and a JSON-RPC 2.0 request.
The message carries **one data part** naming the skill and its input:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "m-1",
      "role": "ROLE_USER",
      "parts": [{"data": {"skill": "findings", "input": {"facility_id": "hq"}}}]
    }
  }
}
```

By default `SendMessage` waits for the task to finish (up to its time cap plus 30 s) and answers
`{"task": {...}}`. With `"configuration": {"returnImmediately": true}` it answers at once with the
task `TASK_STATE_SUBMITTED` or `TASK_STATE_WORKING`; poll `GetTask` with its `id`.

| Method | Does |
|---|---|
| `SendMessage` | Start a task. |
| `GetTask` | A task by `id` (`historyLength` limits the history). |
| `ListTasks` | This server's tasks, newest first: `contextId`, `status`, `statusTimestampAfter`, `pageSize` (1 to 100, default 50), `pageToken`, `historyLength`, `includeArtifacts`. |
| `CancelTask` | Cancel a submitted or working task (its worker is stopped). A finished task is `TaskNotCancelableError` (-32002). |
| `GetExtendedAgentCard` | The extended card. |
| `SendStreamingMessage`, `SubscribeToTask` | `UnsupportedOperationError` (-32004). |
| The four push-notification config methods | `PushNotificationNotSupportedError` (-32003). |

A task ends `TASK_STATE_COMPLETED` (one artifact), `TASK_STATE_FAILED`, `TASK_STATE_REJECTED`
(refused before it ran: a facility outside the allowlist, research-only data, every worker busy)
or `TASK_STATE_CANCELED`. A task takes no follow-up message: send a new message without `taskId`.
The server keeps the last 256 finished tasks, in memory only.

### What comes back

A completed task's artifact has three parts, each marked by `metadata["camber/part"]`:

- `summary` (text): the notices, one `NOTICE:` line each, then a summary whose sentences cite
  fact ids such as `[F1@rule_run-1a2b3c]`;
- `result` (data): the capability's result;
- `grounded-facts` (data): the facts the summary cites, the notices and, for a failure, the
  error code. See [the extension](a2a/grounded-facts.md).

A failed, rejected or cancelled task carries its error code the same way, in its status
message's `grounded-facts` part.

### Refusals

| JSON-RPC error | When |
|---|---|
| `-32009` VersionNotSupportedError | `A2A-Version` is missing (which means 0.3) or is not `1.0`. It may also come as the query parameter `?A2A-Version=1.0`. |
| `-32602` Invalid params | A malformed message or configuration, an unknown field, no data part or more than one, an unknown skill, or input that fails the capability's schema (the `data` lists each field). |
| `-32005` ContentTypeNotSupportedError | A `raw` or `url` part (no file or URL is ever fetched), or `acceptedOutputModes` without `application/json`. |
| `-32003` PushNotificationNotSupportedError | A push notification config. |
| `-32001` TaskNotFoundError | An unknown task id. |
| `-32601` Method not found | Any other method, including the 0.3 names such as `message/send`. |

Each A2A error carries a `google.rpc.ErrorInfo` detail in `error.data`. HTTP errors: 401 / 403
(token), 403 (Host or cross-site), 404, 405, 411 (no `Content-Length`), 413 (a body over
64 KiB), 415 (not JSON).

## Untrusted input

- A peer's message is data. **Text parts are not read**: no model sees them and they never become
  a config, a path or a command. Only the one data part's `skill` and `input` are used, and the
  input is validated against the capability's schema before any work starts.
- The store, the allowlist, the run configs and the trust floor come from the launch, never from
  a peer.
- Answers are data too: equipment and point names in a result come from the building data. The
  card's description says so, and labels travel in data parts, not in prose a peer would read
  as instructions.

## Limits

- A2A 1.0 and the JSON-RPC binding only: no gRPC or HTTP+JSON binding, no 0.3 compatibility, no
  streaming, no push notifications, no signed card.
- One token for all peers, and no per-peer facility scopes.
- Tasks live in memory: a restart forgets them.
- HTTP only; TLS is a reverse proxy's job.

## From Python

```python
from camber.a2a import A2AServer, LocalRunner
from camber.capabilities import CallContext, default_registry

reg = default_registry()
ctx = CallContext(store="store", allowed_facilities=frozenset({"hq"}))
agent = A2AServer(reg, ctx, LocalRunner(reg), token="a-token-of-16-or-more")
status, headers, body = agent.handle(
    "GET", "/.well-known/agent-card.json", {"Host": "127.0.0.1:0"}
)
```

`LocalRunner` runs tasks on threads of the calling process; `camber a2a` uses the killable worker
processes of `camber mcp` (`camber.mcp.WorkerPool`). The API is provisional
([API-STABILITY.md](API-STABILITY.md)).
