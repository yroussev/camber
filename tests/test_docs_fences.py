"""The ``python`` fences in README.md and docs/ run (#219).

Each page's python fences run in order in one namespace, the way a reader pastes them into one
session, in a fresh interpreter whose working directory is a temporary folder and whose network
is switched off. A fence that cannot run on its own (a fragment that uses names from the
surrounding text, or one that needs a dataset, a store, a file, an optional extra or the network)
carries an explicit opt-out on the line just above it::

    <!-- fence: skip (needs a store) -->
    ```python
    ...
    ```

An HTML comment renders on neither GitHub nor the docs site. The text in parentheses is the
reason, for the reader of the source. Internal working docs (docs/dev/, docs/proposals/) are not
checked.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<tick>`{3,}|~{3,})[ \t]*(?P<info>[^`\n]*)$")
PYTHON_INFO = re.compile(r"^\{?\s*\.?(python|py)\b")
SKIP = re.compile(r"^<!--\s*fence:\s*skip\b[^>]*-->$")
MARKER = re.compile(r"<!--\s*fence:")

RUNNER = r"""
import json, socket, sys, traceback

def _no_network(*args, **kwargs):
    raise OSError("network access is disabled in the docs fence test")

socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
socket.create_connection = _no_network
fences = json.loads(sys.stdin.read())
ns = {"__name__": "__main__"}
for line, body in fences:
    try:
        exec(compile(body, f"<fence at line {line}>", "exec"), ns)
    except BaseException:
        print(json.dumps({"line": line, "error": traceback.format_exc(limit=-3)}))
        sys.exit(1)
"""


@dataclass(frozen=True)
class Fence:
    line: int  # 1-based line of the opening fence
    info: str
    body: str
    skip: bool

    @property
    def is_python(self) -> bool:
        return bool(PYTHON_INFO.match(self.info))


def doc_pages() -> list[Path]:
    pages = [ROOT / "README.md"]
    for page in sorted((ROOT / "docs").rglob("*.md")):
        rel = page.relative_to(ROOT / "docs").parts
        if rel[0] in ("dev", "proposals"):
            continue
        pages.append(page)
    return pages


def fences(page: Path) -> list[Fence]:
    lines = page.read_text(encoding="utf-8").splitlines()
    out: list[Fence] = []
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, tick = m["indent"], m["tick"]
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith(tick):
            j += 1
        inner = lines[i + 1 : j]
        body = "\n".join(ln[len(indent) :] if ln.startswith(indent) else ln for ln in inner)
        above = lines[i - 1].strip() if i else ""
        out.append(Fence(i + 1, m["info"].strip(), body, bool(SKIP.match(above))))
        i = j + 1
    return out


def _rel(page: Path) -> str:
    return page.relative_to(ROOT).as_posix()


RUNNABLE = {_rel(p): [f for f in fences(p) if f.is_python and not f.skip] for p in doc_pages()}
RUNNABLE = {k: v for k, v in RUNNABLE.items() if v}


def test_the_scan_finds_runnable_fences():
    # a stale pattern would quietly check nothing
    assert sum(len(v) for v in RUNNABLE.values()) >= 10
    assert "docs/MANDV.md" in RUNNABLE


def test_every_skip_marker_sits_on_a_python_fence():
    stray = []
    for page in doc_pages():
        lines = page.read_text(encoding="utf-8").splitlines()
        python_at = {f.line for f in fences(page) if f.is_python}
        for n, text in enumerate(lines, 1):
            if MARKER.search(text) and (not SKIP.match(text.strip()) or n + 1 not in python_at):
                stray.append(f"{_rel(page)}:{n}")
    assert not stray, f"fence markers not directly above a python fence: {stray}"


@pytest.mark.parametrize("page", sorted(RUNNABLE))
def test_python_fences_run(page, tmp_path):
    payload = json.dumps([[f.line, f.body] for f in RUNNABLE[page]])
    env = dict(os.environ, MPLBACKEND="Agg", PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=300,
    )
    if proc.returncode:
        failure = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if failure.startswith("{"):
            detail = json.loads(failure)
        else:
            detail = {"line": "?", "error": proc.stderr}
        pytest.fail(
            f"{page}:{detail['line']}: the python fence fails\n{detail['error']}\n"
            "Fix the example, or, when it cannot run on its own, put "
            "'<!-- fence: skip (reason) -->' on the line above it."
        )
