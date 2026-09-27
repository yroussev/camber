"""The site-neutrality guard's per-rule path exemptions (``exempt_paths``).

Only the third-party dataset-host rule and its DOI-prefix rule may exempt a path, and only the
dataset catalog (``camber/datasets/catalog.json``), which must link the data as published. The
licence-encumbered dataset rules are never exempt. The pre-commit hook applies the exemption per
rule; the commit-msg hook ignores it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, ".github", "scripts", "site_neutrality_patterns.py")
_CATALOG = "camber/datasets/catalog.json"


def _mod():
    spec = importlib.util.spec_from_file_location("snp_guard", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exempt_rules():
    return [(rx, why, ex) for rx, why, ex in _mod().rules() if ex]


def test_only_the_host_and_doi_prefix_rules_are_exempt_and_only_for_the_catalog():
    exempt = _exempt_rules()
    assert len(exempt) == 2
    reasons = sorted(why for _rx, why, _ex in exempt)
    assert reasons == [
        "third-party dataset DOI prefix",
        "third-party dataset host (licence status unreliable)",
    ]
    for _rx, _why, ex in exempt:
        assert ex == (_CATALOG,)


def test_licence_encumbered_rules_are_never_exempt():
    enc = [r for r in _mod().rules() if "encumbered" in r[1]]
    assert len(enc) >= 2
    assert all(ex == () for _rx, _why, ex in enc)


def test_patterns_keeps_every_rule_as_pairs():
    mod = _mod()
    pairs = mod.patterns()
    assert len(pairs) == len(mod.rules())
    assert all(len(p) == 2 for p in pairs)


def test_output_has_three_tab_fields():
    out = subprocess.run(
        ["python3" if shutil.which("python3") else "python", _SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [ln for ln in out.splitlines() if ln]
    assert len(lines) == len(_mod().rules())
    for ln in lines:
        fields = ln.split("\t")
        assert len(fields) == 3
        assert fields[2] in ("", _CATALOG)
    assert sum(1 for ln in lines if ln.split("\t")[2]) == 2


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


@pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("bash") and shutil.which("python3")),
    reason="needs git+bash",
)
def test_pre_commit_hook_applies_exemptions_per_rule(tmp_path):
    repo = str(tmp_path / "r")
    os.makedirs(os.path.join(repo, ".github", "scripts"))
    os.makedirs(os.path.join(repo, ".githooks"))
    os.makedirs(os.path.join(repo, "camber", "datasets"))
    shutil.copy(_SCRIPT, os.path.join(repo, ".github", "scripts"))
    hook = os.path.join(repo, ".githooks", "pre-commit")
    shutil.copy(os.path.join(_ROOT, ".githooks", "pre-commit"), hook)
    assert _git(repo, "init", "-q").returncode == 0
    rules = {why: rx for rx, why, _ex in _mod().rules()}
    host = rules["third-party dataset host (licence status unreliable)"]  # a plain word regex
    enc = next(rx for why, rx in rules.items() if why == "licence-encumbered third-party dataset")

    def staged(path, text):
        full = os.path.join(repo, path)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        _git(repo, "add", path)
        return subprocess.run(["bash", hook], cwd=repo, capture_output=True, text=True)

    # the host in the catalog passes...
    assert staged(_CATALOG, f'{{"url": "https://{host}.example/x"}}').returncode == 0
    # ...but not anywhere else
    r = staged("notes.md", f"see {host}.example")
    assert r.returncode != 0 and "third-party dataset host" in r.stderr
    _git(repo, "rm", "-q", "--cached", "notes.md")
    # and an encumbered name is refused even in the catalog
    r = staged(_CATALOG, f'{{"note": "{enc}"}}')
    assert r.returncode != 0 and "licence-encumbered" in r.stderr


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("python3")), reason="needs bash")
def test_commit_msg_hook_ignores_exemptions(tmp_path):
    repo = str(tmp_path / "r")
    os.makedirs(os.path.join(repo, ".github", "scripts"))
    shutil.copy(_SCRIPT, os.path.join(repo, ".github", "scripts"))
    assert _git(repo, "init", "-q").returncode == 0
    host = next(rx for rx, why, ex in _mod().rules() if ex and "host" in why)
    msg = tmp_path / "msg"
    msg.write_text(f"catalog: add {host} links\n", encoding="utf-8")
    r = subprocess.run(
        ["bash", os.path.join(_ROOT, ".githooks", "commit-msg"), str(msg)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 and "not site-neutral" in r.stderr
