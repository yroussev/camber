"""The site-neutrality guard's per-rule path exemptions (``exempt_paths``).

Only three rules may exempt a path, each in one file: the third-party dataset-host rule and its
DOI-prefix rule in the dataset catalog (``camber/datasets/catalog.json``), which must link the data
as published, and the building-type label rule in the ENERGY STAR national median EUI reference,
which transcribes every Portfolio Manager property type as printed. The licence-encumbered dataset
rules are never exempt. The pre-commit hook applies the exemption per rule (for the committed rules
and the local denylist alike); the commit-msg hook ignores it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, ".github", "scripts", "site_neutrality_patterns.py")
_CATALOG = "camber/datasets/catalog.json"
_EUI_REF = "camber/energy_factors/energy_star_us_median_eui_2024.json"
_LABEL_RULE = "specific-site building-type label"
# every rule allowed an exemption, and exactly the paths it may exempt
_ALLOWED = {
    "third-party dataset DOI prefix": (_CATALOG,),
    "third-party dataset host (licence status unreliable)": (_CATALOG,),
    _LABEL_RULE: (_EUI_REF,),
}


def _mod():
    spec = importlib.util.spec_from_file_location("snp_guard", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exempt_rules():
    return [(rx, why, ex) for rx, why, ex in _mod().rules() if ex]


def test_only_the_allowed_rules_are_exempt_and_only_for_their_one_file():
    exempt = {why: ex for _rx, why, ex in _exempt_rules()}
    assert exempt == _ALLOWED
    # the label rule is the only one exempt in the EUI reference
    assert [why for why, ex in exempt.items() if _EUI_REF in ex] == [_LABEL_RULE]


def test_the_exempt_files_exist_and_the_eui_reference_needs_its_exemption():
    for paths in _ALLOWED.values():
        for p in paths:
            assert os.path.isfile(os.path.join(_ROOT, p)), p
    rx = next(rx for rx, why, _ex in _mod().rules() if why == _LABEL_RULE)
    with open(os.path.join(_ROOT, _EUI_REF), encoding="utf-8") as fh:
        text = fh.read()
    # the exemption is not dead: the reference really carries the label (the printed type row)
    assert re.search(rx, text, flags=re.IGNORECASE)


def test_licence_encumbered_rules_are_never_exempt():
    enc = [r for r in _mod().rules() if "encumbered" in r[1]]
    assert len(enc) >= 2
    assert all(ex == () for _rx, _why, ex in enc)
    # nor are the local-path, account-name and private-site rules
    for _rx, why, ex in _mod().rules():
        if why not in _ALLOWED:
            assert ex == (), why


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
        assert fields[2] in ("", _CATALOG, _EUI_REF)
    assert sum(1 for ln in lines if ln.split("\t")[2]) == 3


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


def _hook_repo(tmp_path, denylist=None):
    repo = str(tmp_path / "r")
    os.makedirs(os.path.join(repo, ".github", "scripts"))
    os.makedirs(os.path.join(repo, ".githooks"))
    shutil.copy(_SCRIPT, os.path.join(repo, ".github", "scripts"))
    hook = os.path.join(repo, ".githooks", "pre-commit")
    shutil.copy(os.path.join(_ROOT, ".githooks", "pre-commit"), hook)
    if denylist is not None:
        with open(os.path.join(repo, ".githooks", "denylist.local"), "w", encoding="utf-8") as fh:
            fh.write(denylist)
    assert _git(repo, "init", "-q").returncode == 0

    def staged(path, text):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        _git(repo, "add", path)
        r = subprocess.run(["bash", hook], cwd=repo, capture_output=True, text=True)
        _git(repo, "rm", "-q", "--cached", path)
        return r

    return staged


@pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("bash") and shutil.which("python3")),
    reason="needs git+bash",
)
def test_building_type_label_is_refused_everywhere_but_the_eui_reference(tmp_path):
    staged = _hook_repo(tmp_path)
    word = next(rx for rx, why, _ex in _mod().rules() if why == _LABEL_RULE)  # a plain word
    assert re.fullmatch(r"[a-z]+", word)
    row = f'{{"key": "{word}", "name": "{word.title()}", "site_eui": 1.0}}'
    # the property-type row in the EUI reference passes...
    assert staged(_EUI_REF, row).returncode == 0
    # ...and the same text anywhere else is refused: docs, code, another factor set, the catalog
    for path in (
        "notes.md",
        "docs/UNITS.md",
        "CHANGELOG.md",
        "camber/unit_scale.py",
        "camber/energy_factors/energy_star_thermal_2015.json",
        "camber/energy_factors/energy_star_us_median_eui_2024.json.bak",
        "other/camber/energy_factors/energy_star_us_median_eui_2024.json",
        _CATALOG,
    ):
        r = staged(path, row)
        assert r.returncode != 0 and _LABEL_RULE in r.stderr, path
    # and the exemption covers only that rule: an encumbered name is refused even in the reference
    enc = next(
        rx for rx, why, _ex in _mod().rules() if why == "licence-encumbered third-party dataset"
    )
    r = staged(_EUI_REF, f'{{"note": "{enc}"}}')
    assert r.returncode != 0 and "licence-encumbered" in r.stderr


@pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("bash") and shutil.which("python3")),
    reason="needs git+bash",
)
def test_local_denylist_line_may_exempt_paths(tmp_path):
    staged = _hook_repo(
        tmp_path,
        "# comment\nzorblatt\tref/table.json,docs/table.md\nquuxfoo\n",
    )
    # an exempt path passes the one pattern that exempts it...
    assert staged("ref/table.json", "zorblatt row").returncode == 0
    assert staged("docs/table.md", "| Zorblatt |").returncode == 0
    # ...but not any other path
    r = staged("notes.md", "a zorblatt")
    assert r.returncode != 0 and "/zorblatt/i" in r.stderr
    # and every other pattern still applies in the exempt path
    r = staged("ref/table.json", "quuxfoo")
    assert r.returncode != 0 and "/quuxfoo/i" in r.stderr
    # a line with no TAB is a plain pattern, as before
    r = staged("docs/table.md", "QUUXFOO")
    assert r.returncode != 0 and "local denylist" in r.stderr


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("python3")), reason="needs bash")
def test_commit_msg_hook_ignores_the_label_exemption(tmp_path):
    repo = str(tmp_path / "r")
    os.makedirs(os.path.join(repo, ".github", "scripts"))
    shutil.copy(_SCRIPT, os.path.join(repo, ".github", "scripts"))
    assert _git(repo, "init", "-q").returncode == 0
    word = next(rx for rx, why, _ex in _mod().rules() if why == _LABEL_RULE)
    msg = tmp_path / "msg"
    msg.write_text(f"energy factors: restore the {word} row\n", encoding="utf-8")
    r = subprocess.run(
        ["bash", os.path.join(_ROOT, ".githooks", "commit-msg"), str(msg)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 and "not site-neutral" in r.stderr


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
