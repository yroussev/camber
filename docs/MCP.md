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
  registry records a research-only facility whose directory is missing (and that is not archived
  or purged, which removes the directory by design, #329), no facility is served,
  and a facility whose part files are those of a research-only facility in the same store is
  refused as research-only. Since 0.112 (#319) the provenance travels in the data: `camber
  datasets ingest` writes the dataset id, its licence and a research-only flag into every part
  file, and a part marked research-only is refused wherever it sits -- in a test facility it was
  copied into (one month or the whole partition, renamed, merged or mixed with your own data), or
  in another store. **What is enforced:** every part file ingested since 0.112 that is marked
  research-only, or whose mark cannot be read, is refused, whatever it is named (any file in a
  facility directory that the store reads, `x.parq` or no suffix included, #329), before the call and again when a tool
  reads it. **What is not:** a store ingested before 0.112 has no marks, so only the registry
  checks above apply to it and a partial copy of it is still served (`camber datasets provenance
  --store DIR --missing` lists such facilities; `camber datasets ingest --force` adds the marks);
  and nothing stops someone with write access to the store from rewriting the files to strip the
  mark, or from exporting the data out of Parquet and ingesting it as their own. Keep the store
  writable only by the people who ingest into it, and change it with CAMBER's own commands
  (`camber datasets remove`, `camber facility ...`) rather than by hand. The `facilities`
  listing leaves a refused facility out, and its notice names each one it withholds (its id,
  never a path) and why (0.112, #333), so a model does not tell a user that a right id is wrong.
- **A trust floor.** `--min-trust` sets the minimum data-trust score the tools apply. A check
  that declines because a required input scores below it is announced in a NOTICE line, with
  the count, the rules and every input below it with its score; `sensor_trust` reports the same
  scores and marks those inputs `below_floor`. An optional input below the floor does not
  decline a check on its own, unless the rule cannot judge without it (the outdoor-air signal
  of `dcv_verification`, for one; see CAPABILITY-REGISTRY.md). A finding resting on an input whose configured sensor
  reference disagrees is not declined but marked `conditional_on` that sensor.
- **Model-sized results.** A tool returns one page of each list by default, so its default
  result fits a host that caps a tool result at 25,000 tokens: at most 25,000 characters of
  `structuredContent` (see **Sizes** below); `offset` asks for the next page, and a notice says
  what was left out and the exact `offset` to pass. A reply over 2 MB is refused as too large,
  and a call has a time budget (see **Results** below).
- **Citations.** A tool's summary cites fact ids such as `[F1@ctx-1a2b3c]`, which point at the
  values in the result. The token after the context depends on the call, the allowlist, the run
  config, the trust floor and the stored data, so two servers (or one server before and after an
  ingest) never give one id to different values (0.111, #295). The stored data counts by the
  size and modification time of each file the call reads and of the point catalog, so a part
  file rewritten in place gives new ids too (#312); a restart, or a config at another path,
  does not, and neither does how the config spells a path it reads (absolute or relative,
  through a symlink, with a trailing slash), nor the store or file mounted at another path, as on
  a host and in a container (0.112, #326).
- **Checking a draft.** `check_citations` judges each sentence of a draft against the facts it
  cites (0.112, #294, #328): every figure must be one a cited fact states, with the same sign,
  unit, counted thing and role (a threshold, target or setpoint is not a result, and a result
  is not one; a derived number is not supported), every date or year one a cited fact carries,
  and every equipment, meter, facility (the caller's facility ids and their registry names
  included), point role, verdict ("passes", "meets", "ok", "fault"), assurance word
  ("verified", "clean", "real", "confident"), direction ("rose", "saved", "high" / "low"),
  negation ("not", "never", "no", "none", "0 points"), quantifier ("all", "every", "most") and
  issue rank one a cited fact states. Each figure, zero and verdict is read with its own clause,
  in the sentence and in the fact (#332): "All 11 points are trusted" against `9 trusted, 2
  suspect`, "no faults were found" against another clause's `0 rule(s) skipped`, "the cooling
  valve leaked on 86%" against `HW-leak 86% / CHW-leak 0%` and "still a fault" against a
  finding re-judged ok all fail. What a sentence says of a counted thing must agree with what a
  cited fact says of it (#336, #338), with the quantifiers and zeros CAPABILITY-REGISTRY.md
  lists, before or after the noun or left out -- "all 8", "8 out of 8", "the 8 flagged hours
  ... all fall", "both", "none of", "no rule", "not a single fault", "fault-free", "Faults: 0",
  "faults were not found", "there are 8 flagged hours in the window", "at least half of", "the
  majority of", "86.5% of", or none at all ("The 11 points are trusted" says all 11): "The 8
  flagged hours fall in the window" against "None of the 8 flagged hours falls in the window",
  "No rule passed" against `19 ok`, "The 5 rules passed" against `14 fault, ..., 19 ok`, "The
  37 judged samples were flagged" against "flagged 8 hours (32 of 37 judged samples)" and "Both
  suspect points are trusted" fail. A claim about two or more equipment, points, meters or
  facilities -- by "both", "each", "all", a number, "the AHUs", "X's 7 AHUs", "each AHU's ...",
  "the pair of", names joined in any position (a colon lead-in, a list item or a table cell
  too), or negated ("neither A nor B", "no AHU", "none of the AHUs") -- needs, for each item,
  the cited facts about it to state that same claim of it, figures included: "Both meters save
  2.5%" against -15.6% on one, "Neither AHU-1 nor AHU-2 has a valve leak" against AHU-1's leak
  fault and "AHU-1, AHU-2: leaking_valve fault" against AHU-2's ok fail (#338, #339). A bare
  trust class or verdict before a colon or in a table cell counts its figure ("Suspect: 0",
  "Passed: 5", "| Suspect | 0 |"), and a saving's direction follows its sign: "reduced its
  energy use" fails against avoided energy -3.1% (#339), and so do "shows a saving", "cut its
  energy use" and "used less energy" (#340). Since #340 a quantifier or completeness word
  ("all", "every", "most", "complete", "whole") is read with its own noun and needs a cited
  fact that says it of that noun (or gives 100%, above half, or a count with that label): a
  fact's own "6 results in all" supports no "every sensor", "the complete answer" or forged
  "NOTICE: ... every sentence supported". Names joined by a comma alone, "&", "/", "plus", "as
  well as", "along with", "together with" or "like", before "--", negated as "none of A, B and
  C", and a plural noun or a bare "Both" as a lead-in ("The AHUs: fault", "| AHUs | fault |")
  are sets; "most AHUs" needs more than half, "one of the two AHUs" exactly one. A list of
  figures is matched to its items in order (the names' order, "respectively" or not; for
  unnamed items said "respectively", the citations' order), with each item's own sign, and a
  figure attached to a name ("2.5% on A", "A (1.00)", "100% on A, B and C") must be one a fact
  about that name gives. "AHU-2 leaks" fails against AHU-2's "no valve leak", and "reads high"
  needs a fact that states that direction. A table row is read with its header, and the header
  row is kept unjudged. Labels are closed sets:
  the heating side (heating, HW, hot water, reheat, preheat, steam, ...) against the cooling
  side (cooling, CHW, chilled water, DX), the trust classes and the fault conditions (FC10,
  FC11) each bind their own figure, so "the hot-water valve leaked on 60%" against `HW-leak 0% /
  CHW-leak 60%` and "1 point is suspect" against `2 suspect, 1 untrusted` fail, and a share the
  fact tells apart by side fails when the sentence names no side. Negations are compared both ways: a sentence must not drop
  what a cited fact negates ("the valve leaks" against "no valve leak"); "it is false that ..."
  and a cause or effect ("accounts for", "will clear") need a fact that says so. Headings, bold
  lines and lead-ins ending in a colon are judged when they state something. A failing sentence
  comes with `why`. **Supported means matched, not proven true**: only the words listed there
  are read; an inference worded otherwise, a verdict of the model's own or advice is not
  recognised, and passes when its figures and names match, so give it after the checked text,
  labelled as judgment. The rules and
  their limits are in CAPABILITY-REGISTRY.md, "Where a cited fact comes from". `verified_text`
  keeps the draft's lines, headings, list markers and blank lines and removes only the failing
  sentences (#322); when one of those gave a verdict, a negation or a caveat, it opens with a
  `NOTICE:` line saying the text says less than the draft, and a banner says the same (#328): do
  not show such a `verified_text` as the answer. The prompts ask for `check_citations` on the
  whole revised draft each time, not only on the sentences changed (#336). Every result says
  that it covers only the text passed in that call: a text reworded, shortened or extended
  after a passing check is not checked until it is checked again (#338).
- **What it does not check** (the full list is in CAPABILITY-REGISTRY.md). These pass when
  their figures and names match, so they can be wrong and still be marked supported:
  a counted thing named after its verb or later in its clause with no quantifier ("the window
  covers the 8 flagged hours" is not read as all 8); a statement about a counted thing in words
  other than the cited fact's ("the 8 hours precede the window"); a cause after a bare
  "because" ("declined because the input was untrusted"); a label outside the closed sets
  where the cited fact gives only one figure; a claim about all of several facts that are not
  equipment, points, meters or facilities ("both baselines"); a set said with no number ("each
  AHU", "the AHUs", "no AHU") is read as the items the cited facts are about, so an item no
  cited fact is about is not known to the check; a claim of every item that each has a fact
  about, beyond the listed words ("Every AHU has a new actuator"); a list said "respectively" of
  groups ("the AHUs and the loggers ... respectively") or, in a later clause, of a set named
  only by a pronoun ("their biases are +0.0 and +3.0 respectively"); a range given for a set;
  a set across sentences ("AHU-1 fails it. So does AHU-2."); a plural noun after its verb ("a
  fault on the AHUs"); a comparative with no figure ("more points are suspect than trusted");
  words outside the lists ("survives the re-judge", "lowest trust score", "Both AHUs drift").
  And these fail closed although they can be true: "all", "every" or a completeness word said
  of a noun no cited fact quantifies ("Every rule returned a result" against "5 of them
  returned a result") unless a cited fact counts that label with its total; a list said
  "respectively" with no order or a different number of figures; verbs listed "respectively";
  "they are all trusted" or a cell "all trusted" unless a cited fact counts all of them as
  trusted; "half of" a thing no cited fact counts; a set of items backed by one cited fact; a
  negated set that does not open its clause ("fails on neither AHU"); a set's figure with no
  cited fact about its items; "over 9" or "well over 9" against a fact's 9 or 10; "all 8 ... fall outside the window" (a whole
  count needs a fact that says all of that many); "86 of the 106 fall outside" (a derived
  number); a share the fact tells apart by side, trust class or fault condition said without
  one ("the valve leaked on 60%"); a cause a cited fact does not state with "because", "due
  to", "caused by" or "explained by".

**Prompt-argument completion (0.111, #304).** The server declares the `completions` capability and
answers `completion/complete` for a prompt's arguments in both revisions: `facility_id` from the
allowed facilities the store holds and a call would read (never a research-only one), `equip` and
`equips` (its last comma-separated id) from the stored equipment of the facility already chosen
(`context.arguments.facility_id`), at most 100 values, those starting with what was typed first.
`equips` does not offer an id it already lists, and `triage_ahu`'s `equip` offers no equipment
of a recognised class other than an air handler (no meters, no weather).
A name that is not one clean line of at most 200 characters is never offered, nor one that the
summaries' sanitiser would change (HTML or markdown markup, a URL, a fact-id bracket) or that
starts with `NOTICE` (0.111, #307), and any other argument completes to nothing. A host shows these to the person filling in a prompt. The model
itself cannot list or start a prompt in most hosts, only a person can.

**A multi-word prompt argument** (0.112, #333). A host that starts a prompt from a slash command
may split what follows on spaces, quoted or not: in testing, the `claude` command line gave
`check_savings_claim` only the first word of a quoted `claim`. Every other prompt argument is one
word (an id, or `equips` as comma-separated ids with no spaces). For a claim, start the prompt
with its facility only (`claim` is optional) and give the claim in your next message, or skip
the slash command and ask in the conversation ("Check this savings claim for ds-site-1: the
2017 retrofit cut ... by 12% against the 2016 baseline"); the model passes the whole claim to
`mv_summary` itself. Prefer the first: a question asked in the conversation does not carry the
prompt's instructions, and in testing the model reworded its answer after a passing check and
told the user the check had passed for text that was never checked (#338). If you ask in the
conversation, ask for the same discipline ("cite the fact ids, check the whole final answer with
check_citations, give only the text it marked supported and label your own judgment"); every
`check_citations` result also says that it covers only the text passed in that call.

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
  damaged. Under `--allow-all-active` one warning names the active facilities that are refused
  the same way, and the startup line counts them apart ("33 facilities allowed, 3 of them not
  served"); a `--config` for such a facility is warned about too (its calls are refused).
- One of `--allow` and `--allow-all-active` is required: the server does not start without an
  allowlist (exit 2).
- `--config PATH` is a run or report config (`.json`, or `.yaml` / `.yml` with the `yaml` extra)
  for one facility, used by the tools that run rules or reports. Repeat it for more facilities,
  one config each (two configs for the same facility are refused), so `--allow-all-active` can
  serve tuned results for every facility. Each config is checked at startup the way `camber run`
  checks it: a file that cannot be parsed (a trailing comma, an empty file, YAML without the
  extra), a config that is not an object or repeats a key, a wrong shape, an unknown rule or
  rule parameter, a parameter value that is not a finite number or is outside its documented
  range without a `"basis"` for it, a value of the wrong type (text or true / false for a number,
  a fraction for a whole-number parameter such as `start_hour`, anything but true / false for a
  flag, a word not listed for a choice; since #320 also null where the parameter has a value by
  default, a list or an object where it takes one value, and one value where it takes a list), a
  `"basis"` that is not a non-empty text, a bad `units`,
  `ventilation` (a zones file that cannot be read included) or `report.rcx` block (in
  `report.rcx.references`: a value that is not an object, a threshold that is not a finite
  number or not above 0, a warn level above its fault level, a sensor used
  as its own reference, or a unit, reference equipment or reference file the config's source
  does not have), a facility the store does not hold, a config that names
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
  stored equipment class with every built-in rule at its defaults, with a banner saying so; a
  class CAMBER does not recognise (a `LOGGER`) is left out when the facility has a recognised
  one, so no rule grades it on its roles alone (name its class in a `--config` to run it).
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
server like Ctrl-C: a log line, every call stopped, the directory removed, exit status 143 or 129
(130 for Ctrl-C), also while the server is blocked writing to a client that stopped reading; a
second signal does not interrupt that cleanup (#316), nor change that exit status (0.112, #326). No
process can clean up after `SIGKILL`; the next `camber mcp` to start removes the directories of
servers that are no longer running (#309). A server holds a lock on a file in its directory while
it runs, so a later one tells a dead server from a live one also where the process id cannot, as
for servers that each run as pid 1 in containers sharing a `/tmp` volume (0.112, #326). That file
also records the server (pid, kernel, pid namespace, start time) and the server touches it every
10 seconds (#330). A server of the same kernel and pid namespace is judged by its pid and start
time. Another container's server is judged by its lock where `flock` works, as on a named volume.
On a Docker Desktop bind mount `flock` excludes nothing, so there its heartbeat decides: its
directory is removed once the file has not been touched for 2 minutes. A live server's
directory is kept there. A killed server's directory is removed by the next server to start: at once
where its lock already shows it gone, and at the latest by any server that starts 2 minutes or
more after the kill. A 0.111
server's directory is judged by its process id, and those named without a process id (0.111
development builds) are removed once they are a day old. A name
it cannot read as a process id is left alone, and nothing the sweep meets stops the start (#316).

The server writes nothing but MCP messages to stdout; its log (startup, one line per tool call
with the outcome and the time taken) goes to stderr. Anything a tool prints by mistake is sent to
stderr as well, so it cannot corrupt the stream.

**Concurrency, cancellation and shutdown.** Each tool call runs in a worker process (a fresh
Python that loads the tools once and is reused, about half a second to start and 110 MB idle;
one is started with the server and a replacement is started as soon as one is killed). Every
other message -- `ping`, `server/discover`, `initialize`, the lists, `prompts/get`,
`completion/complete` and `notifications/cancelled` -- is answered at once, even while long calls run, so replies to
concurrent requests may come in a different order from the requests; each carries its own id.
The worker of a call is killed with every process it started (matplotlib's font scan runs some;
also when the worker itself was killed from outside, #316), and its CPU and memory released, when:

- the call runs past its time cap (120 s, answered within about a second of it): the reply is
  `timeout` ("... and was stopped");
- the client sends `notifications/cancelled` for it: there is no reply, as the specification
  asks (a call still waiting for a worker is simply dropped);
- the client closes stdin, or closes its end of stdout: every running call is stopped and the
  server exits within about a second. Read the replies you want before closing stdin.

A worker is also replaced after an unusually large call: one that took its memory past 1 GiB and
past twice what it held after its first call (#316). So an idle server does not hold the memory
of a rare large call, while a worker whose every call needs more than 1 GiB (a large facility, or
`coverage` on a multi-year building) keeps it and does not load the data again for each call.
The first call a worker serves sets its level, so one may stay at what that call needed. A
worker that ends on its own (a crash, or the system stopping it for lack of memory) is replaced:
when it was idle, before any call is given to it; when it was running a call, or died while
starting for one (after one more try on a fresh worker, #316), that call answers `unavailable`
with `"retryable": true` (retry it, and narrow it if it fails again). Every call the server
accepts gets exactly one reply, unless the client cancels it.

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

**From the container image**: `docker run --rm -i --entrypoint camber -v <store>:/data/store:ro
ghcr.io/yroussev/camber:<version> mcp --store /data/store --allow <id>` as the command. The image's
health check probes the read API only when the container serves it (0.112); with a 0.111 or
older image add `--no-healthcheck`, or the container turns `unhealthy` (#326). See
[DOCKER.md](https://github.com/yroussev/camber/blob/main/DOCKER.md).

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
request's `id` when the line's first bytes name it (top level, a complete string or integer)
and the whole line names no other top-level `id`, else `null`. An integer argument may be sent as a whole-number float (`5.0` is the
integer 5, as JSON Schema says); `5.5` is `invalid_params`.

**Errors.** Following the specification, a problem the model can fix by changing its call is a
tool result with `isError: true` and a `code: message` text (`invalid_params`, `not_found`,
`not_allowed`, `refused_licence`, `too_large`, `timeout`, `unavailable`, `busy`); the code is also
in `_meta["camber/error"]`. `busy` means the server is already running and queueing as many calls
as it was started for; its `data` is `{"retryable": true, "max_concurrent": N, "max_queued": M}`. `unavailable` means the stored data could not be read: its `data` says
whether a retry will likely work (`"retryable": true`, e.g. a part file a concurrent
`camber datasets ingest` replaced, or a call that a re-ingest of its facility overlapped: a
call never answers from a half-swapped facility) or not (a damaged or unreadable file), and names the facility.
A store facility registry that cannot be read just now (a writer is replacing it) is `unavailable` with
`"retryable": true, "reason": "registry_unreadable"` (0.112, #324), also when it is broken again
before the retries end (#329). One that stays a directory, a file you may not read or a symlink
loop is the registry problem (`refused_licence`, not retryable) until it is repaired.
It is also the answer when the worker running the call ended under it (`"retryable": true`).
A date the calendar does not have (`2025-02-30`) is `invalid_params`. A window outside the
stored data is `not_found` for every tool that runs rules or reports and for `sensor_trust`;
only `coverage` answers it, with an empty result and a notice. Everything else is a JSON-RPC error:

| Code | When |
|---|---|
| -32700 | the line is not JSON or not UTF-8 |
| -32600 | not a JSON-RPC 2.0 request object: a batch, a line over 1 MiB, a duplicate key, a lone surrogate, an integer too long to read, `jsonrpc` not `"2.0"`, a missing `method`, an `id` that is not a string or integer, a second `initialize`, a `tools/call` with the id of a call still in progress (#309) |
| -32601 | an unknown method, or one the request's protocol revision does not have |
| -32602 | bad `params`: missing or malformed `_meta` keys, no handshake and no `_meta`, an unknown tool or prompt, a prompt argument missing, unknown, not a string or too long (an id over 200 characters, a `claim` over 1,000, more than 20 `equips`: refused, never cut), an unknown cursor, a completion `ref` that is not a known prompt (`ref/resource` included) or an unknown argument |
| -32022 | `UnsupportedProtocolVersion`, with the supported versions in `data` |
| -32603 | an internal failure, including a reply that could not be serialised; names no local path |

When the request's id cannot be read, the error carries `"id": null`. No message, however
malformed, ends the server: every message refused as a whole (-32700, -32600) is answered, a
notification too, and logged on stderr. A message with a `method` member, even `null`, is a
request, never taken for a client's response.

**Results.** A tool result has up to three text blocks: the summary (with any notices first, as
lines starting with `NOTICE:`), the result as compact JSON (the same result is in
`structuredContent`) and, when the result has facts, the facts (0.112, #294): a header line, then
one `[id] text` line per fact. Hosts such as Claude Desktop show the model `content` but not
`_meta`, so the facts block is what lets a model cite and check facts. Only a notice starts a
line with `NOTICE:`: the summary is one line, and names from the data in it are quoted and
stripped of control, bidi and invisible characters (0.111, #291; see
[CAPABILITY-REGISTRY.md](CAPABILITY-REGISTRY.md)). For programmatic clients the fact index (every
field of every fact) is also in `_meta["camber/facts"]`, the notices in `_meta["camber/banners"]`
and the truncation flag in `_meta["camber/truncated"]`.

**The facts and notices are in `structuredContent` too** (0.112, #331). Some hosts, command-line
coding assistants among them, show the model a result's `structuredContent` when it has one and drop
`content`, so every tool's `structuredContent` starts with a `camber` object
(`camber.mcp.RESULT_KEY`):

```json
{"camber": {"facts": {"F1@rule_run-1a2b3c": "2 rules were run on ...",
                      "N1@rule_run-1a2b3c": "1 finding is conditional on ..."},
            "notices": ["1 finding is conditional on ..."]},
 "facility_id": "hq", "findings": [...]}
```

`facts` maps each fact id to its text, in the order of the facts block (the same ids and text,
notices' own `N` facts included); `notices` are the `NOTICE:` lines, in order: research-only and
registry banners, sensor-reference conditions, declined checks, removed sentences, the
stripped-caveat notice of `check_citations`, pages and cuts. Every `outputSchema` declares the key
(and requires it), and the official MCP Python SDK (2.3.0) validates every result against it. The
rest of `structuredContent` is the capability's own result, which is also the JSON text block of
`content` (that block leaves `camber` out: its facts and notices are the other blocks). The copy
is made from the facts the server records for the session, never from what a caller sends, so it
cannot add a fact: `check_citations` still resolves every id from the session's ledger, and a
`facts` argument built from the compact copy (id and text only) is not the issued fact and is
refused. A `camber` key in a call's arguments is refused like any unknown argument.

**Citations are checked against what this server sent** (0.112, #294). The server records the
facts of every tool result it answers, in both protocol revisions, in one ledger per process
(stdio is one session). `check_citations` looks every cited id up there, so a model passes only
its draft; an id this process never sent (from another server, config or store, from before a
restart, or recomputed) is `unknown_citation`, one it sent but has since dropped (the ledger
keeps about 8 MB of the most recent facts) is `forgotten_citation`, and a `facts` argument, still
accepted, must repeat facts this session was sent, unchanged, or the call is refused
(`invalid_params`). A worker process gets only the facts its call cites; the ledger stays in the
server process, so a worker killed or recycled loses nothing. "Supported" means: every cited id
is a fact this session was sent, and every figure, name, point role, verdict, assurance word,
direction, negation, quantifier and rank the sentence states matches a fact it cites (#328); it
does not mean the wording is otherwise true of those facts, and a word outside those lists is
not read (see [CAPABILITY-REGISTRY.md](CAPABILITY-REGISTRY.md#where-a-cited-fact-comes-from)).

**Sizes** (0.111, #299). Both revisions say a tool that returns `structuredContent` SHOULD also
return it serialized as JSON in a text block, for clients that pass only `content` to the model,
so the JSON goes out twice: once, compact, in `content` and once in `structuredContent`. Rather
than leave one out, the server keeps results small:

- **Pages.** Every list comes a page at a time: `rule_run` 12 findings, `sensor_trust` 40
  points, `coverage` 20 rows per list, `points` 200, `facilities` 40, `drift` 10 per list,
  `rule_params` 15 rules (7 with `detail`), `dataset_search` 20 datasets, `mv_summary` 5 meters
  with all their findings, `check_citations` the failing sentences only (every sentence and
  `verified_text` with `detail: true`), `rcx_report` as many of its best-ranked issues as fit
  (at most 8) with their facts and the first 5 appendix entries of its fact index. Pass
  `offset` for the next page (the notice gives it); `limit` (up to 1,000) sets the page size,
  and a larger page makes a larger result. `rcx_report` takes `top_n` and `offset` for issues
  and `limit` for the appendix. Counts such as `count`, `n_points` and the RCx KPIs are of the
  whole list.
- **The default fits a host's limit** (0.112, #333). Some hosts cap what a tool result may put
  in front of the model: the `claude` command line, for one, sets a result over 25,000 tokens
  aside in a file and shows the model an error instead (its `MAX_MCP_OUTPUT_TOKENS` environment
  variable sets the limit), and a model with no file tool never sees the result. A
  host that gives the model only `structuredContent` counts that JSON, and this JSON (hex fact
  ids, short keys, numbers) runs at about two characters a token, so 25,000 tokens is about
  50,000 characters, not the 100 KB a prose rule of thumb gives. The default pages keep every
  default result at most 25,000 characters of `structuredContent` (the data and the `camber`
  facts and notices), half that budget; a test holds every tool to it. On the largest facilities
  tested (two VAV fleets of about 650 points) the defaults are 11,000 to 21,000 characters:
  `rcx_report` about 20,000 with 4 issues, `sensor_trust` 18,000, `points` 14,000, `coverage`
  13,000. A larger `limit` or `top_n` can pass a host's limit: page with `offset` instead.
- **`truncated` means something was left out.** Whenever a list in the result is not the whole
  list, `_meta["camber/truncated"]` is `true` and a notice says which list, the rows shown, the
  total and the call that gets the rest.
- **The cap counts the whole reply.** A reply larger than 2 MB, counted as sent (the summary,
  the JSON text block, the facts block, `structuredContent` and `_meta` together), is the tool error
  `too_large`, with `{"bytes": ..., "max_bytes": ...}`; ask for a smaller page.
- **A shorter tool list.** `tools/list` leaves out what a client does not need to pick or call a
  tool: the `$schema` of JSON Schema 2020-12 (the dialect both revisions assume), the
  `annotations.title` that repeats the tool's `title`, and the `description` annotations inside
  each `outputSchema` (it validates the same). The server still checks every result against the
  full schema.

On a 650-point fleet, a default call's whole reply (the summary, the JSON text block, the facts
block, `structuredContent` and `_meta`) is 30 to 65 KB (they were 0.3 to 1.2 MB in 0.110), and
`tools/list` is about 48 KB (the `camber` declaration adds about 130 bytes a tool: each
`outputSchema` is a document of its own, so a `$ref` cannot share one definition between tools).
The facts block (0.112) adds 10 to 15 % to a reply, and the compact copy of the facts in
`structuredContent` (#331) 5 to 17 % more.

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
it, and `McpServer.finish(call, outcome)` turns the outcome into the reply. `McpServer.ledger`
(0.112) is the session's `FactLedger`: the server makes one unless you pass `ledger=` (or a
context with one); a transport serving several sessions gives each session its own.
