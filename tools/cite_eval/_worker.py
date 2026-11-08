"""Judge cases with whichever ``camber`` this interpreter imports (stdlib + camber only).

``python _worker.py IN.jsonl OUT.jsonl`` reads one case per line, ``{"key", "draft", "facts":
[fact, ...], "allowed": [...], "names": {facility: registry name}}``, records the facts in a
session ledger as a server does when it issues them, calls the ``check_citations`` capability
as an MCP client does (the text alone, ``detail`` on; the cited ids resolve from the ledger) and
writes ``{"key", "grounded", "verified_text", "sentences": [[verdict, why],
...], "error"}`` per line. It is run with another venv's interpreter to baseline an installed
build, so it imports nothing from this repository.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace


def _names_store(root: str, names: dict) -> str:
    """A store holding only a facility registry with ``names`` (the checker reads facility
    names from it, as it does from a server's store)."""
    from camber.store import ParquetStore

    st = ParquetStore(os.path.join(root, "store"))
    for fid, name in sorted(names.items()):
        st.register_facility(fid, **({"name": name} if name else {}))
    return st.root


class Judge:
    """Calls ``check_citations`` for one case at a time; one names store per allowlist."""

    def __init__(self, tmp: str):
        from camber.capabilities._cap_check_citations import check_citations

        self._check = check_citations
        self._tmp = tmp
        self._stores: dict = {}

    def ctx(self, allowed, names):
        k = (tuple(sorted(allowed or ())), tuple(sorted((names or {}).items())))
        if k not in self._stores:
            root = tempfile.mkdtemp(dir=self._tmp)
            store = _names_store(root, dict(names or {})) if names else None
            self._stores[k] = store
        return SimpleNamespace(
            allowed_facilities=frozenset(allowed or ()), store=self._stores[k], ledger=None
        )

    def __call__(self, case: dict) -> dict:
        from camber.capabilities._ledger import FactLedger

        out = {"key": case.get("key")}
        try:
            ctx = self.ctx(case.get("allowed"), case.get("names"))
            ctx.ledger = FactLedger()
            ctx.ledger.record(case.get("facts") or ())
            args = {"text": case["draft"], "detail": True}
            if "per_sentence" in case:
                args["per_sentence"] = case["per_sentence"]
            data = self._check(ctx, args).data
            out["grounded"] = bool(data["grounded"])
            out["verified_text"] = data.get("verified_text", "")
            out["sentences"] = [
                [s["text"], s["verdict"], s.get("why", "")] for s in data["sentences"]
            ]
        except Exception as e:  # noqa: BLE001 - a refused call is a result too
            out["error"] = f"{type(e).__name__}: {e}"[:500]
        return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    src, dst = argv[0], argv[1]
    with tempfile.TemporaryDirectory(prefix="cite-judge-") as tmp:
        judge = Judge(tmp)
        with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
            for line in fin:
                if line.strip():
                    fout.write(json.dumps(judge(json.loads(line)), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
