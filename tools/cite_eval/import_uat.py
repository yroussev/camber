"""Turn the acceptance-test repros into regression-corpus entries.

    python -m tools.cite_eval.import_uat --uat UAT_DIR --scratch DIR [--out CORPUS_DIR]

Reads the repros the CITE, BLIND and CLIENT testers left in ``UAT_DIR/work/u112r*/`` (the last
round's copies hold every earlier round's: ``CITE/p5``-``p9`` probe specs, ``CITE/{repro,n,q}``
sessions with their labelled cases, ``BLIND/repro/repro_r5``-``r9c``, ``CLIENT/repro/s_*``), and
replays each one in an in-process MCP server (``camber.mcp``'s own, with a session ledger) over
APFS clones of the UAT stores made under ``--scratch`` (the stores themselves are only read). For
each probe it keeps the draft, the facts it cites exactly as the server issued them, the
tester's label (true / false; "any" and "gap" labels are left out), and what this checkout's
checker says. Every round-9 finding (``reports/u112r9``) is mapped to its probes in
:data:`FINDINGS`; a probe whose label the checker does not meet becomes an ``xfail`` entry tagged
``gap:<name>``.

An entry (one JSON line)::

    {"id", "source", "draft", "facts": [...], "allowed": [...], "names": {...},
     "expected": {"draft": "supported" | "fail", "sentences": [{"text", "verdict"}]?},
     "tags": {"form", "operator", "region", "capability", "source", "round", "role",
              "finding"?}, "xfail": bool, "gap": "gap:<name>"?, "observed": {...}}

Absolute local paths in fact texts and drafts are replaced by ``<local>/<name>``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import types

# ----------------------------------------------------------------------------- findings

#: every round-9 finding, its gap name and the probes that repro it: ``(source, labels)``,
#: a source being ``CITE/<spec>``, ``BLIND/<script>:<function>`` or ``CLIENT/<s file>``, and
#: labels a list of probe labels or row numbers (``None``: every mismatching probe there)
FINDINGS = {
    "CITE-U112R9-1": (
        "table-header-check-word",
        [
            ("CITE/sa2", [f"H{i:02d}" for i in range(10)]),
            ("CITE/sa", ["C00", "C01", "C03"]),
            ("CITE/si", ["X05"]),
        ],
    ),
    "CITE-U112R9-2": (
        "html-comment-unjudged",
        [("CITE/sa2", ["M00", "M01", "M02"]), ("CITE/sa", ["C06", "C07"])],
    ),
    "CITE-U112R9-3": (
        "contrast-but-b-does",
        [
            ("CITE/sc", ["E00", "E02", "E03", "E04", "E05", "E07", "E08"]),
            ("CITE/sb", ["V24"]),
            ("CITE/si", ["X00"]),
        ],
    ),
    "CITE-U112R9-4": (
        "two-unit-contrast-or-compact-list",
        [
            (
                "CITE/sb",
                "V01 V02 V06 V07 V09 V11 V14 V15 V31 V32 V33 V34 V39 V43 V44".split(),
            ),
            ("CITE/si", ["X06", "X07"]),
        ],
    ),
    "CITE-U112R9-5": (
        "site-claim-from-unit-or-site-count",
        [
            ("CITE/sc", ["S00", "S01", "S05", "S06", "S09", "S10", "S14"]),
            ("CITE/sb", ["SS3", "SS8", "SS9"]),
            ("CITE/si", ["X03", "X04"]),
        ],
    ),
    "CITE-U112R9-6": (
        "warn-from-sensor-comparison",
        [
            ("CITE/sc", ["W00", "W01", "W02", "W03", "W06", "W08", "W09"]),
            ("CITE/sb", ["R08", "R09", "R10", "R11", "R15"]),
            ("CITE/si", ["X01", "X02"]),
        ],
    ),
    "CITE-U112R9-7": ("point-x-not-y", [("CITE/sb", ["P11"]), ("CITE/si", ["X08"])]),
    "CITE-U112R9-8": (
        "unit-id-unicode-hyphen-or-word",
        [("CITE/sb", ["U00", "U01", "U05", "U08", "U10", "U19"])],
    ),
    "CITE-U112R9-9": (
        "rule-name-spaced-or-short",
        [("CITE/sb", ["R02", "R03", "R04", "R05"]), ("CITE/si", ["X09"])],
    ),
    "CITE-U112R9-10": (
        "month-range-or-from-today",
        [("CITE/sd", ["T05", "T07", "T08", "T14", "T20", "T22", "T23", "T24", "X02"])],
    ),
    "CITE-U112R9-11": (
        "coverage-or-hour-words-variant",
        [("CITE/sd", ["C07", "C08", "C09", "C17", "C20", "H05", "H09", "H11"])],
    ),
    "CITE-U112R9-12": (
        "span-by-place",
        [("CITE/sb", ["D00", "D01", "D02", "D03", "D07", "D09", "D11"])],
    ),
    "CITE-U112R9-13": ("checked-by-the-rules-stripped", [("CITE/sf", ["S02", "S04", "H12"])]),
    "CITE-U112R9-14": (
        "heading-with-check-noun-stripped",
        [("CITE/sf", ["H00", "H02", "H04", "H09"])],
    ),
    "CITE-U112R9-sg": ("combined-r9-forms-draft", [("CITE/sg", None)]),
    "CITE-U112R8-9": ("only-a-of-the-two-stripped", [("CITE/rg", ["T09"])]),
    "BLIND-U112R9-1": (
        "site-figure-from-one-meter",
        [
            ("BLIND/repro_r9b:fox", [1, 2, 3, 4, 5, 6, 7, 8, 11]),
            ("BLIND/repro_r9c:fox", [1, 2, 3, 4, 5, 7]),
            ("BLIND/repro_r9c:blind", [3]),
        ],
    ),
    "BLIND-U112R9-2": (
        "savings-claim-verdict-verbs",
        [("BLIND/repro_r9:fox", [22]), ("BLIND/repro_r9b:fox", [22, 27, 28, 29, 30])],
    ),
    "BLIND-U112R9-3": (
        "saving-period-from-baseline-year",
        [("BLIND/repro_r9:fox", [7, 10]), ("BLIND/repro_r9b:fox", [12, 13, 16, 17, 18, 19])],
    ),
    "BLIND-U112R9-4": (
        "checked-by-the-rules-stripped",
        [("BLIND/repro_r9:blind", [37]), ("BLIND/repro_r9b:blind", [7, 8, 9])],
    ),
    "BLIND-U112R9-5": (
        "reference-confirmed-stripped",
        [("BLIND/repro_r9:blind", [7, 8, 9, 11, 12])],
    ),
    "BLIND-U112R9-6": (
        "heading-unit-spelling-stripped",
        [("BLIND/repro_r9:blind", [2, 3, 4]), ("BLIND/repro_r9b:blind", [1, 2, 3])],
    ),
    "CLIENT-U112R9-1": (
        "status-list-swap",
        [("CLIENT/s_r9b", None), ("CLIENT/s_r9c", None), ("CLIENT/s_r9d", None)],
    ),
    "CLIENT-U112R9-2": ("advice-check-word-stripped", [("CLIENT/s_r9a", None)]),
}


def _finding_index() -> dict:
    """``{(source, label): (finding, gap)}`` and ``{source: (finding, gap)}`` for ``None``."""
    by_label, by_source = {}, {}
    for fid, (gap, probes) in FINDINGS.items():
        for src, labels in probes:
            if labels is None:
                by_source[src] = (fid, gap)
            else:
                for lab in labels:
                    by_label[(src, str(lab))] = (fid, gap)
    return by_label, by_source


# ----------------------------------------------------------------------------- environment

_LOCAL = re.compile(r"/(?:Users|home|private|tmp|var)/[^\s\"'\])|,;]+")


def scrub(value):
    """``value`` with every absolute local path replaced by ``<local>/<last part>``."""
    if isinstance(value, str):
        return _LOCAL.sub(lambda m: "<local>/" + m.group(0).rstrip("/").rsplit("/", 1)[-1], value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


class Env:
    """APFS clones of the UAT stores under ``scratch`` (made once), and config copies whose
    ``source.store`` names the clone."""

    def __init__(self, uat: str, scratch: str):
        self.uat = os.path.abspath(uat)
        self.scratch = os.path.abspath(scratch)
        os.makedirs(self.scratch, exist_ok=True)
        self._stores: dict = {}
        self._configs: dict = {}

    def store(self, path: str) -> str:
        """The clone standing for the tester's store ``path`` (their clones are gone)."""
        base = os.path.basename(os.path.normpath(path))
        kind = "blind" if "blind" in base else ("injstore" if "inj" in base else "main")
        if kind not in self._stores:
            src = {
                "blind": os.path.join(self.uat, "stores", "blind"),
                "main": os.path.join(self.uat, "stores", "main"),
                "injstore": os.path.join(self.uat, "work", "u112r2", "CITE", "injstore"),
            }[kind]
            dst = os.path.join(self.scratch, "stores", kind)
            if not os.path.isdir(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                # -c: an APFS clone (no data copied); the source is only read
                subprocess.run(["cp", "-c", "-R", src, dst], check=True)
            self._stores[kind] = dst
        return self._stores[kind]

    def config(self, path: str, store: str) -> str:
        k = (os.path.abspath(path), store)
        if k not in self._configs:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            base = os.path.dirname(os.path.abspath(path))

            def fix(v):
                if isinstance(v, dict):
                    return {kk: fix(vv) for kk, vv in v.items()}
                if isinstance(v, list):
                    return [fix(vv) for vv in v]
                if (
                    isinstance(v, str)
                    and v.lower().endswith((".csv", ".json", ".parquet"))
                    and not os.path.isabs(v)
                    and os.path.exists(os.path.join(base, v))
                ):
                    return os.path.join(base, v)
                return v

            cfg = fix(cfg)
            if isinstance(cfg.get("source"), dict) and cfg["source"].get("kind") == "store":
                cfg["source"]["store"] = store
            h = hashlib.sha1(json.dumps(k).encode()).hexdigest()[:8]
            dst = os.path.join(self.scratch, "cfg", f"{h}-{os.path.basename(path)}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
            self._configs[k] = dst
        return self._configs[k]


class Session:
    """One MCP session: ``camber.mcp``'s server in this process, launched from a tester's
    ``camber mcp`` arguments (store and configs mapped to the clones)."""

    def __init__(self, env: Env, argv: list):
        from camber.capabilities import default_registry
        from camber.mcp._launch import build_context
        from camber.mcp._server import McpServer

        args = list(argv)
        if args and args[0] == "mcp":
            args = args[1:]
        store, allow, configs, min_trust = None, [], [], 0.5
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--store":
                store, i = args[i + 1], i + 2
            elif a == "--allow":
                allow.append(args[i + 1])
                i += 2
            elif a == "--config":
                configs.append(args[i + 1])
                i += 2
            elif a == "--min-trust":
                min_trust, i = float(args[i + 1]), i + 2
            else:
                i += 1
        self.store = env.store(store)
        cfgs = [env.config(c, self.store) for c in configs]
        ctx = build_context(
            self.store,
            allow=allow,
            config=(cfgs[0] if len(cfgs) == 1 else cfgs) or None,
            min_trust=min_trust,
        )
        self.allowed = sorted(ctx.allowed_facilities)
        self.server = McpServer(default_registry(), ctx)
        self._id = 0
        self._rpc(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "uat"}},
        )
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.names = self._names()

    def _names(self) -> dict:
        try:
            from camber._servable import read_registry

            entries = read_registry(self.store)[0]
        except Exception:  # noqa: BLE001
            return {}
        out = {}
        for fid, meta in sorted(entries.items()):  # the checker reads every registry name
            name = (meta or {}).get("display_name") or (meta or {}).get("name") or ""
            out[fid] = name if isinstance(name, str) else ""
        return out

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        resp = self.server.handle(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        )
        return (resp or {}).get("result") or {}

    def call(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def texts(self, result: dict) -> dict:
        sc = result.get("structuredContent") or {}
        return dict(((sc.get("camber") or {}).get("facts")) or {})

    def facts_for(self, text: str) -> list:
        """The issued facts ``text`` cites (unknown ids left out), as the server holds them."""
        from camber.agent.verify import _visible, extract_cites
        from camber.capabilities._ledger import ISSUED

        out = []
        for cid in extract_cites(_visible(text)):
            state, fact = self.server.ledger.lookup(cid)
            if state == ISSUED:
                out.append(fact)
        return out

    def check(self, text: str) -> dict:
        r = self.call("check_citations", {"text": text, "detail": True})
        return r.get("structuredContent") or {}


# ----------------------------------------------------------------------------- probes


class Probe:
    __slots__ = ("source", "label", "exp", "draft", "session", "role", "round")

    def __init__(self, source, label, exp, draft, session, role, rnd):
        self.source, self.label, self.exp, self.draft = source, str(label), exp, draft
        self.session, self.role, self.round = session, role, rnd


def _exec_spec(path: str) -> dict:
    ns: dict = {"__file__": path}
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    exec(compile(src, path, "exec"), ns)  # noqa: S102 - the testers' own spec files
    return ns


def cite_specs(env: Env, cite_dir: str, skip=("sweep9",)) -> list:
    """The CITE probe specs ``p5``-``p9`` (``ARGV``, ``CALLS``, ``IDS``, ``PROBES``)."""
    out = []
    specs = {}
    for d in sorted(glob.glob(os.path.join(cite_dir, "p[0-9]"))):
        for p in sorted(glob.glob(os.path.join(d, "*.py"))):
            name = os.path.splitext(os.path.basename(p))[0]
            if name.endswith("_old") or name in skip or name in ("base", "dump"):
                continue
            specs[name] = p  # the later round's spec of a name wins
    for name, path in sorted(specs.items()):
        try:
            spec = _exec_spec(path)
        except Exception as e:  # noqa: BLE001
            print(f"skip spec {path}: {e}", file=sys.stderr)
            continue
        if "PROBES" not in spec or "ARGV" not in spec:
            continue
        try:
            sess = Session(env, spec["ARGV"])
        except Exception as e:  # noqa: BLE001
            print(f"skip spec {path}: session: {e}", file=sys.stderr)
            continue
        F: dict = {}
        for cname, args in spec.get("CALLS", []):
            if callable(args):
                args = args(F)
            F.update(sess.texts(sess.call(cname, args)))
        ids = {}
        for k, (pre, sub) in spec.get("IDS", {}).items():
            m = [i for i, v in F.items() if i.startswith(pre + "-") and sub in v]
            ids[k] = m[0] if m else f"MISSING-{k}"
        rnd = os.path.basename(os.path.dirname(path))
        for lab, exp, text in spec["PROBES"]:
            try:
                draft = text.format(**ids)
            except (KeyError, IndexError, ValueError):
                continue
            if "MISSING-" in draft:
                print(f"{name} {lab}: a fact id is missing on this checkout", file=sys.stderr)
                continue
            out.append(Probe(f"CITE/{name}", lab, exp, draft, sess, "CITE", rnd))
    return out


def cite_sessions(env: Env, cite_dir: str) -> list:
    """``<name>.session.json`` (argv + call/cite steps) with ``<name>.cases.json`` labels.

    The cite texts carry the fact ids of the tester's run; each call's id token is mapped to
    this run's: from the run's own result file (``<name>.json``, with its ``smap``) when there
    is one, else by trying each order of the calls of one capability and keeping the one under
    which the most drafts name what their cited facts name."""
    out = []
    for cases_path in sorted(glob.glob(os.path.join(cite_dir, "*", "*.cases.json"))):
        stem = cases_path[: -len(".cases.json")]
        if not os.path.exists(stem + ".session.json"):
            continue
        with open(stem + ".session.json", encoding="utf-8") as fh:
            sj = json.load(fh)
        with open(cases_path, encoding="utf-8") as fh:
            cases = {c[0]: c[1] for c in json.load(fh)}
        try:
            sess = Session(env, sj["argv"])
        except Exception as e:  # noqa: BLE001
            print(f"skip session {stem}: {e}", file=sys.stderr)
            continue
        new_tokens = []  # per call step: (capability, token)
        for step in sj["steps"]:
            if step["op"] == "call":
                ids = sess.texts(sess.call(step["name"], step.get("args") or {}))
                toks = {i.split("@", 1)[1] for i in ids if "@" in i}
                new_tokens.append((step["name"], sorted(toks)[0] if toks else None))
        cites = [st for st in sj["steps"] if st["op"] == "cite" and st.get("label") in cases]
        m = _session_map(stem, sj, new_tokens, cites, sess)
        sub = os.path.basename(os.path.dirname(cases_path))
        name = os.path.basename(stem)
        for st in cites:
            draft = _rewrite(st["text"], m)
            out.append(
                Probe(
                    f"CITE/{sub}/{name}", st["label"], cases[st["label"]], draft, sess, "CITE", sub
                )
            )
    return out


_NAMES = re.compile(r"[A-Za-z0-9]+__\w+|\bAHU-\d+\b|\bds-[\w-]+|\bLOGGER-\d+\b")


def _session_map(stem, sj, new_tokens, cites, sess) -> dict:
    """``{old "cap-token": new "cap-token"}`` for one session (see :func:`cite_sessions`)."""
    import itertools

    calls = [st for st in sj["steps"] if st["op"] == "call"]
    old_per_call = None
    if os.path.exists(stem + ".json"):
        with open(stem + ".json", encoding="utf-8") as fh:
            run = json.load(fh)
        inv = {v: k for k, v in (run.get("smap") or {}).items()}
        old_per_call = []
        for r in run.get("results", []):
            if (r.get("req") or {}).get("op") != "call":
                continue
            sc = ((r.get("res") or {}).get("result") or {}).get("structuredContent") or {}
            toks = sorted({k.split("@", 1)[1] for k in (sc.get("camber") or {}).get("facts", {})})
            t = toks[0] if toks else None
            old_per_call.append(inv.get(t, t))
        if len(old_per_call) != len(calls):
            old_per_call = None
    if old_per_call is not None:
        return {
            o: f"{cap}-{n.rsplit('-', 1)[-1]}"
            for o, (cap, n) in zip(old_per_call, new_tokens)
            if o and n
        }
    # no result file: the cited tokens of each capability, tried in each order
    cited: dict = {}
    for st in cites:
        for t in re.findall(r"@([a-z_]+-[0-9a-f]{6})\b", st["text"]):
            cap = t.rsplit("-", 1)[0]
            cited.setdefault(cap, [])
            if t not in cited[cap]:
                cited[cap].append(t)
    per_cap: dict = {}
    for cap, tok in new_tokens:
        if tok:
            per_cap.setdefault(cap, []).append(tok)
    options = []
    for cap, olds in cited.items():
        news = per_cap.get(cap, [])
        if not news:
            continue
        options.append(
            [
                dict(zip(olds, perm))
                for perm in itertools.permutations(news, min(len(olds), len(news)))
            ]
        )
    best, best_score = {}, -1
    for combo in itertools.product(*options) if options else [()]:
        m = {}
        for part in combo:
            m.update(part)
        score = 0
        for st in cites:
            draft = _rewrite(st["text"], m)
            facts = sess.facts_for(draft)
            blob = " ".join(f.get("text", "") for f in facts)
            score += len(facts) + sum(1 for n in set(_NAMES.findall(draft)) if n in blob)
        if score > best_score:
            best, best_score = m, score
    return best


def _map_suffixes(m: dict, old_ids, new_texts: dict) -> None:
    """Map the old call's id suffix (``rule_run-abc123``) to this run's, when each is one."""
    if not old_ids or not new_texts:
        return
    os_ = {k.split("@", 1)[1] for k in old_ids if "@" in k}
    ns = {k.split("@", 1)[1] for k in new_texts if "@" in k}
    oh = {x.rsplit("-", 1)[-1] for x in os_}
    nh = {x.rsplit("-", 1)[-1] for x in ns}
    if len(oh) == 1 and len(nh) == 1:
        o1, n1 = oh.pop(), nh.pop()
        for x in os_:
            m[x] = x[: -len(o1)] + n1


def _rewrite(text: str, m: dict) -> str:
    return re.sub(r"@([\w]+-[0-9a-f]+)", lambda g: "@" + m.get(g.group(1), g.group(1)), text)


# --- BLIND: the testers' async MCP client scripts, run in this process against the shim


def _shim(env: Env, sessions: list):
    """A stand-in ``mcp`` package whose stdio client talks to an in-process :class:`Session`."""

    class _Text:
        def __init__(self, t):
            self.type, self.text = "text", t

    class _Result:
        def __init__(self, r):
            self.structured_content = r.get("structuredContent")
            self.structuredContent = self.structured_content
            self.content = [_Text(c.get("text", "")) for c in r.get("content") or []]
            self.isError = bool(r.get("isError"))

    class StdioServerParameters:
        def __init__(self, command=None, args=None, **kw):
            self.command, self.args = command, list(args or [])

    class ClientSession:
        def __init__(self, r, w):
            self.sess = r

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def initialize(self):
            return None

        async def call_tool(self, name, arguments=None):
            return _Result(self.sess.call(name, arguments or {}))

    @contextlib.asynccontextmanager
    async def stdio_client(params, errlog=None):
        s = Session(env, params.args)
        sessions.append(s)
        yield s, None

    mcp = types.ModuleType("mcp")
    mcp.ClientSession, mcp.StdioServerParameters = ClientSession, StdioServerParameters
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    stdio.stdio_client = stdio_client
    mcp.client, client.stdio = client, stdio
    return {"mcp": mcp, "mcp.client": client, "mcp.client.stdio": stdio}


def blind_scripts(env: Env, repro_dir: str, names=None) -> list:
    """``repro_r5``-``r9c``: each ``check(s, probes)`` call's ``(want, text)`` probes, recorded
    by a stand-in ``check`` and numbered as the script prints them (per function, from 1)."""
    out: list = []
    paths = sorted(glob.glob(os.path.join(repro_dir, "repro_r*.py")))
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        if names and name not in names:
            continue
        rnd = re.sub(r"repro_(r\d+).*", r"\1", name)
        if rnd in ("r2", "r3", "r4"):
            continue  # unlabelled (the round's report judged each output by hand)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        runs = re.findall(r"^asyncio\.run\((\w+)\(\)\)\s*$", src, re.M)
        body = re.sub(r"^asyncio\.run\(\w+\(\)\)\s*$", "", src, flags=re.M)
        sessions: list = []
        mods = _shim(env, sessions)
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        argv = sys.argv
        try:
            sys.argv = [path, "venv"]
            ns: dict = {"__name__": "uat_repro", "__file__": path}
            exec(compile(body, path, "exec"), ns)  # noqa: S102 - the testers' own scripts
            for fn in runs:
                counter = {"i": 0}

                async def check(s, probes, _fn=fn, _c=counter, _src=f"BLIND/{name}", _r=rnd):
                    for want, t in probes:
                        _c["i"] += 1
                        out.append(Probe(f"{_src}:{_fn}", _c["i"], want, t, s.sess, "BLIND", _r))

                ns["check"] = check
                with contextlib.redirect_stdout(io.StringIO()):
                    asyncio.run(ns[fn]())
        except Exception as e:  # noqa: BLE001
            print(f"skip {path}: {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            sys.argv = argv
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
    return out


# --- CLIENT: s_*.txt sentences over a host transcript's tool calls (probe2v.py's replay)


def _runall_map(repro_dir: str) -> dict:
    """``{s name: (mcp json, transcript)}`` from ``runall.sh``."""
    path = os.path.join(repro_dir, "runall.sh")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        sh = fh.read()
    mcp = {
        "B": os.path.join(repro_dir, "..", "mcp-blind.json"),
        "M": os.path.join(repro_dir, "..", "mcp-main.json"),
    }
    out = {}
    for m in re.finditer(r"for s in ([^;]+); do run \$(\w) (\S+) \$s", sh):
        for s in m.group(1).split():
            out.setdefault(s, (mcp[m.group(2)], m.group(3)))
    for m in re.finditer(r"(?<![\w$])run \$(\w) (\S+) (\w+) \$1", sh):
        out.setdefault(m.group(3), (mcp[m.group(1)], m.group(2)))
    for m in re.finditer(r"probe2v?\.py \$(\w) tx/(\S+)\.jsonl s_(\w+)\.txt", sh):
        out.setdefault(m.group(3), (mcp[m.group(1)], m.group(2)))
    # round 9 (s_r9a-d) is in the round's report, not in runall: X13r8, blind
    for s in ("r9a", "r9b", "r9c", "r9d"):
        out.setdefault(s, (mcp["B"], "X13r8"))
    return out


def _transcript_calls(path: str) -> tuple:
    calls, uses, old = [], {}, {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("type") == "assistant":
                for c in e["message"]["content"]:
                    if c.get("type") == "tool_use" and not c["name"].endswith("check_citations"):
                        uses[c["id"]] = len(calls)
                        calls.append((c["name"].split("__")[-1], c["input"]))
            if e.get("type") == "user" and not isinstance(e["message"]["content"], str):
                for c in e["message"]["content"]:
                    if c.get("type") != "tool_result" or c.get("tool_use_id") not in uses:
                        continue
                    parts = (
                        c["content"] if isinstance(c["content"], list) else [{"text": c["content"]}]
                    )
                    for p in parts:
                        try:
                            d = json.loads(p.get("text", ""))
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(d, dict):
                            fs = (d.get("camber") or {}).get("facts", {})
                            old.setdefault(uses[c["tool_use_id"]], set()).update(fs)
    return calls, old


def client_probes(env: Env, repro_dir: str) -> list:
    out = []
    for s, (mcp_json, tx) in sorted(_runall_map(repro_dir).items()):
        sfile = os.path.join(repro_dir, f"s_{s}.txt")
        txp = os.path.join(repro_dir, "tx", f"{tx}.jsonl")
        if not (os.path.exists(sfile) and os.path.exists(txp) and os.path.exists(mcp_json)):
            continue
        with open(mcp_json, encoding="utf-8") as fh:
            argv = json.load(fh)["mcpServers"]["camber"]["args"]
        try:
            sess = Session(env, argv)
        except Exception as e:  # noqa: BLE001
            print(f"skip CLIENT {s}: {e}", file=sys.stderr)
            continue
        calls, old = _transcript_calls(txp)
        m: dict = {}
        for i, (n, a) in enumerate(calls):
            _map_suffixes(m, old.get(i), sess.texts(sess.call(n, a)))
        with open(sfile, encoding="utf-8") as fh:
            lines = [x.rstrip("\n") for x in fh if x.strip() and not x.startswith("#")]
        for k, t in enumerate(lines, 1):
            if t[:2] not in ("T|", "F|"):
                continue
            exp = "true" if t[0] == "T" else "false"
            draft = _rewrite(t[2:].replace("\\n", "\n"), m)
            rnd = re.match(r"r(\d+)", s)
            out.append(
                Probe(
                    f"CLIENT/s_{s}",
                    k,
                    exp,
                    draft,
                    sess,
                    "CLIENT",
                    f"r{rnd.group(1)}" if rnd else "r5",
                )
            )
    return out


# ----------------------------------------------------------------------------- entries

_LABEL = {"S": "supported", "X": "fail", "true": "supported", "false": "fail"}


def classify(draft: str) -> tuple:
    """``(form, region)`` tags for a draft, by its shape."""
    t = draft
    if "<!--" in t:
        region = "comment"
    elif re.search(r"^\s*\|", t, re.M):
        region = "table"
    elif re.search(r"^\s*#", t, re.M):
        region = "heading"
    elif re.search(r"^\s*[-*•]\s", t, re.M):
        region = "bullet"
    elif "**" in t:
        region = "bold"
    elif re.search(r"^\s*>", t, re.M):
        region = "blockquote"
    else:
        region = "plain"
    low = t.lower()
    checks = [
        ("claim_of_checking", r"\b(verif|check|support|confirm|ground|validat|fact-check|review)"),
        ("respectively", r"respectively"),
        ("contrast", r"\b(but|unlike|whereas|while|though|not \w+'s|, not )\b"),
        ("status_list", r"\w: [^;]+;"),
        ("set", r"\b(both|each|every|all|none|neither|most)\b"),
        ("time_span", r"\b(throughout|since|from \S+ to|between|year|month|week|may|june)\b"),
        ("coverage_words", r"\b(coverage|covered|logged|recorded|gap)"),
        ("numbers_in_words", r"\b(one|two|three|four|five|six|seven|eight|nine|ten|half)\b"),
        ("conditional", r"\b(if|unless|until)\b"),
        ("existential", r"\bthere (is|are|was|were)\b"),
    ]
    form = "plain"
    if re.search("[‐‑‒–—−０-９]", t):
        form = "unicode"
    else:
        for name, rx in checks:
            if re.search(rx, low):
                form = name
                break
    return form, region


def _capability(fid: str) -> str:
    m = re.search(r"@([a-z_]+)", fid or "")
    return m.group(1) if m else ""


def build_entries(probes: list) -> list:
    """Corpus entries for ``probes`` with this checkout's verdicts (session and inline)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tools.cite_eval._worker import Judge

    by_label, by_source = _finding_index()
    out, seen = [], set()
    tmp = os.path.join(os.path.dirname(probes[0].session.store), "judge") if probes else "."
    os.makedirs(tmp, exist_ok=True)
    judge = Judge(tmp)
    for p in probes:
        exp = _LABEL.get(p.exp)
        if exp is None:
            continue  # "any" / "gap": either verdict acceptable
        facts = p.session.facts_for(p.draft)
        dkey = (p.draft, json.dumps(facts, sort_keys=True))
        if dkey in seen:
            continue
        seen.add(dkey)
        live = p.session.check(p.draft)  # in the session (ledger), as the tester ran it
        low = p.draft.lower()
        names = {
            fid: n
            for fid, n in p.session.names.items()
            if fid in p.session.allowed
            or fid.lower() in low
            or (n and re.sub(r"\s*\(.*$", "", n).strip().lower() in low)
        }  # a registry name the draft does not contain cannot be read in it
        inline = judge(
            {
                "key": "x",
                "draft": p.draft,
                "facts": facts,
                "allowed": p.session.allowed,
                "names": names,
            }
        )
        sents = live.get("sentences", [])
        grounded = bool(live.get("grounded"))
        agree = (
            "error" not in inline
            and inline["grounded"] == grounded
            and [s[1] for s in inline["sentences"]] == [s["verdict"] for s in sents]
        )
        expected = {"draft": exp}
        if len(sents) == 1:
            expected["sentences"] = [{"text": sents[0]["text"], "verdict": exp}]
        elif exp == "supported":
            expected["sentences"] = [{"text": s["text"], "verdict": "supported"} for s in sents]
        met = grounded if exp == "supported" else not grounded
        form, region = classify(p.draft)
        caps = sorted({_capability(f["id"]) for f in facts} - {""})
        tags = {
            "form": form,
            "operator": "uat",
            "region": region,
            "capability": "+".join(caps) or "none",
            "source": f"{p.source}#{p.label}",
            "round": p.round,
            "role": p.role,
        }
        hit = by_label.get((p.source, p.label)) or by_label.get(
            (p.source.split("/")[0] + "/" + p.source.split("/")[-1], p.label)
        )
        if hit is None and p.source in by_source and not met:
            hit = by_source[p.source]
        entry = {
            "id": hashlib.sha1(f"{p.source}#{p.label}#{p.draft}".encode()).hexdigest()[:12],
            "source": f"{p.source}#{p.label}",
            "draft": p.draft,
            "facts": facts,
            "allowed": p.session.allowed,
            "names": names,
            "expected": expected,
            "tags": tags,
            "xfail": not met,
            "observed": {
                "grounded": grounded,
                "verdicts": [s["verdict"] for s in sents],
                "inline_agrees": agree,
            },
        }
        if hit:
            entry["tags"]["finding"] = hit[0]
        if not met:
            if hit:
                entry["gap"] = f"gap:{hit[1]}"
            else:
                kind = "escape" if exp == "fail" else "false-strip"
                entry["gap"] = f"gap:uat-{p.role.lower()}-{kind}-unmapped"
        out.append(scrub(entry))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--uat", required=True, help="the UAT root (stores/, work/, reports/)")
    ap.add_argument("--round", default="u112r9", help="the work dir holding every repro")
    ap.add_argument("--scratch", required=True, help="where the store clones go")
    ap.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "tests",
            "citations_corpus",
        ),
    )
    ap.add_argument("--only", choices=["cite", "sessions", "blind", "client"], action="append")
    ap.add_argument("--keep-scratch", action="store_true")
    a = ap.parse_args(argv)
    env = Env(a.uat, a.scratch)
    work = os.path.join(env.uat, "work", a.round)
    groups = {
        "cite": lambda: cite_specs(env, os.path.join(work, "CITE")),
        "sessions": lambda: cite_sessions(env, os.path.join(work, "CITE")),
        "blind": lambda: blind_scripts(env, os.path.join(work, "BLIND", "repro")),
        "client": lambda: client_probes(env, os.path.join(work, "CLIENT", "repro")),
    }
    os.makedirs(a.out, exist_ok=True)
    try:
        for g in a.only or list(groups):
            probes = groups[g]()
            entries = build_entries(probes)
            path = os.path.join(a.out, f"uat_{g}.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
            nx = sum(e["xfail"] for e in entries)
            na = sum(not e["observed"]["inline_agrees"] for e in entries)
            print(
                f"{g}: {len(probes)} probes -> {len(entries)} entries, {nx} xfail, "
                f"{na} differ inline"
            )
    finally:
        if not a.keep_scratch:
            shutil.rmtree(os.path.join(env.scratch, "stores"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
