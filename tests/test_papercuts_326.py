"""#326: papercuts from the 0.111 Linux check.

1. A fact id's token reflects the store and files a run config resolves to, not how it spells
   them (absolute, relative, through a symlink, with a trailing slash).
2. A second signal soon after the first still ends ``camber mcp`` with exit status 143/129/130.
4. The matplotlib directory of a SIGKILLed server that ran as pid 1 is swept by a later server,
   while a live server's directory in another pid namespace is kept.

Every store is a small synthetic one in its own tmp directory; nothing reaches the network.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, call_token  # noqa: E402

FID = fx.FID


def _write(path, config) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return str(path)


@pytest.fixture
def site(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.points()
    return {"root": tmp_path, "store": st}


def _token(site, config):
    ctx = CallContext(store=site["store"].root, allowed_facilities=frozenset({FID}), config=config)
    return call_token("rule_run", {"facility_id": FID}, ctx)


# --------------------------------------------------------------------------- 1. fact-id token


def test_the_spelling_of_source_store_does_not_change_the_token(site):
    root, store = site["root"], site["store"].root
    cfgdir = root / "configs"
    cfgdir.mkdir()
    os.symlink(store, root / "storelink")
    spellings = [
        store,  # absolute
        os.path.relpath(store, cfgdir),  # relative to the config
        str(root / "storelink"),  # through a symlink
        store + "/",  # a trailing slash
    ]
    tokens = set()
    for i, spelled in enumerate(spellings):
        config = {**fx.config(), "source": {**fx.config()["source"], "store": spelled}}
        tokens.add(_token(site, _write(cfgdir / f"c{i}.json", config)))
    assert len(tokens) == 1, tokens


def test_the_same_store_mounted_elsewhere_gives_the_same_token(site, tmp_path_factory):
    # another "machine": the store at another path, the config in another directory
    elsewhere = tmp_path_factory.mktemp("mount point")
    os.symlink(site["store"].root, elsewhere / "main")
    here = _write(site["root"] / "here.json", fx.config())
    there = {**fx.config(), "source": {**fx.config()["source"], "store": str(elsewhere / "main")}}
    there_ctx = CallContext(
        store=str(elsewhere / "main"),
        allowed_facilities=frozenset({FID}),
        config=_write(elsewhere / "there.json", there),
    )
    assert _token(site, here) == call_token("rule_run", {"facility_id": FID}, there_ctx)


def test_what_the_config_reads_still_counts(site):
    cfg = site["root"] / "c.json"
    mapping = site["root"] / "mapping.json"
    mapping.write_text("{}", encoding="utf-8")
    with_mapping = {**fx.config(), "mapping": {"path": "mapping.json"}}
    before = _token(site, _write(cfg, with_mapping))
    # the same file spelled absolutely: the same token
    absolute = {**with_mapping, "mapping": {"path": str(mapping)}}
    assert _token(site, _write(cfg, absolute)) == before
    # the file changed: another token
    mapping.write_text('{"a": 1}', encoding="utf-8")
    assert _token(site, _write(cfg, with_mapping)) != before
    # another store's data: another token
    other = fx.make_store(site["root"] / "other", meta=None, fault_week=2)
    moved = {**fx.config(), "source": {**fx.config()["source"], "store": other.root}}
    assert _token(site, _write(cfg, moved)) != _token(site, _write(cfg, fx.config()))


# --------------------------------------------------------------------------- 2. second signal


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig,status", [("SIGTERM", 143), ("SIGHUP", 129), ("SIGINT", 130)])
def test_a_second_signal_after_the_session_ended_keeps_the_exit_status(site, sig, status):
    # the session ends on the first signal; the second comes after its cleanup, when launch()
    # used to restore the default handlers, which then ended the process by the signal
    code = (
        "import os, signal, sys, time\n"
        "from camber.mcp import _launch\n"
        "from camber.__main__ import main\n"
        f"SIG = signal.{sig}\n"
        "def session(*a, **k):\n"
        "    os.kill(os.getpid(), SIG)\n"
        "    time.sleep(30)\n"
        "_launch._launch = session\n"
        f"rc = main(['mcp', '--store', {site['store'].root!r}, '--allow', {FID!r}])\n"
        "os.kill(os.getpid(), SIG)\n"
        "time.sleep(0.3)\n"
        "sys.exit(rc)\n"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == status, r.stderr
    assert "Traceback" not in r.stderr


# --------------------------------------------------------------------------- 4. pid-1 sweep


def _mpl_dir(tmp_path, pid, tag="abc123"):
    from camber.mcp import _launch

    d = tmp_path / f"{_launch._MPL_PREFIX}{pid}-{tag}"
    d.mkdir()
    (d / _launch._MPL_OWNER).write_text("", encoding="utf-8")
    return d


def _holder(path):
    """Another process that holds ``path``'s owner file locked, as its live server would (it
    may sit in another pid namespace: only the lock counts)."""
    from camber.mcp import _launch

    code = (
        "import fcntl, os, sys, time\n"
        f"fd = os.open({str(path / _launch._MPL_OWNER)!r}, os.O_RDWR)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('locked', flush=True)\n"
        "time.sleep(60)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "locked"
    return p


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_a_dead_servers_directory_named_with_a_live_pid_is_swept(tmp_path):
    from camber.mcp import _launch

    # a SIGKILLed pid-1 server's directory, seen by the next pid-1 server: its pid is "alive"
    dead = _mpl_dir(tmp_path, os.getpid(), "dead01")
    # a live server's, named with a pid that is no process here (another pid namespace)
    live = _mpl_dir(tmp_path, 2**31 - 2, "live01")
    holder = _holder(live)
    try:
        assert _launch.sweep_matplotlib_dirs(str(tmp_path)) == 1
        assert not dead.exists() and live.exists()
    finally:
        holder.kill()
        holder.wait()
    # once its server is gone, the next sweep removes it too
    assert _launch.sweep_matplotlib_dirs(str(tmp_path)) == 1
    assert not live.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_a_server_holds_its_own_directory(tmp_path, monkeypatch):
    from camber.mcp import _launch

    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    path = _launch.isolate_matplotlib()
    try:
        assert path is not None and os.path.isfile(os.path.join(path, _launch._MPL_OWNER))
        assert _launch._owner_gone(path) is False
        assert _launch.sweep_matplotlib_dirs(str(tmp_path)) == 0 and os.path.isdir(path)
    finally:
        while _launch._MPL_LOCKS:
            os.close(_launch._MPL_LOCKS.pop())
    assert _launch._owner_gone(path) is True


# --------------------------------------------------------------------------- 3. healthcheck


def test_the_image_healthcheck_probes_the_api_only_when_the_container_serves_it(monkeypatch):
    from camber import _healthcheck as hc

    api = ["python", "-m", "camber.api.server"]
    serve = ["/usr/local/bin/python", "/usr/local/bin/camber", "serve", "/data/store"]
    mcp = ["/usr/local/bin/python", "/usr/local/bin/camber", "mcp", "--store", "/data/store"]
    shell = ["/bin/bash"]
    assert hc.serves_http(api) and hc.serves_http(serve) and hc.serves_http(None)
    assert not hc.serves_http(mcp) and not hc.serves_http(shell)
    # camber mcp: healthy without any HTTP request
    monkeypatch.setattr(hc, "_command", lambda pid=1: mcp)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: pytest.fail("probed the API under camber mcp")
    )
    assert hc.main() == 0
    # the API: probed, and unhealthy when it does not answer
    monkeypatch.setattr(hc, "_command", lambda pid=1: api)

    def refused(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    assert hc.main() == 1


def test_the_dockerfile_uses_the_command_aware_healthcheck():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
        text = fh.read()
    assert 'CMD ["python", "-m", "camber._healthcheck"]' in text
