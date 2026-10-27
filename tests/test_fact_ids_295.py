"""0.111 (#295): a fact id's token depends on the server's run config, trust floor and store.

The same call on the same server and unchanged data keeps its ids (also after a restart, in a
fresh process); a different config, trust floor or stored data gives different ids, so the facts
of two servers can be merged for ``check_citations`` without a clash. Every store is a small
synthetic one in its own tmp directory; nothing reaches the network.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, call_token, default_registry  # noqa: E402

FID = fx.FID
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, config) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return str(path)


@pytest.fixture
def site(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.write_role_frame(fx.ahu_frame(seed=3), facility_id="annex", equip="AHU-9", equip_class="AHU")
    return {"root": tmp_path, "store": st}


def _ctx(site, **kw):
    return CallContext(store=site["store"].root, allowed_facilities=frozenset({FID, "annex"}), **kw)


def _ids(ctx, name="points", arguments=None) -> list:
    res = default_registry().call(
        name, {"facility_id": FID} if arguments is None else arguments, ctx
    )
    return [f["id"] for f in res.facts]


def test_same_call_same_server_same_ids(site):
    cfg = _write(site["root"] / "a.json", fx.config())
    assert _ids(_ctx(site, config=cfg)) == _ids(_ctx(site, config=cfg))
    assert _ids(_ctx(site, min_trust=0.7)) == _ids(_ctx(site, min_trust=0.7))
    # the config's content counts, not its path: the same config elsewhere gives the same ids
    other = site["root"] / "elsewhere"
    other.mkdir()
    os.symlink(site["store"].root, other / "store")  # its relative store resolves the same
    same = _write(other / "b.json", fx.config())
    assert _ids(_ctx(site, config=cfg)) == _ids(_ctx(site, config=same))


def test_config_changes_the_ids(site):
    a = _write(site["root"] / "a.json", fx.config())
    b = _write(site["root"] / "b.json", {**fx.config(), "rules": ["simultaneous_heat_cool"]})
    ids = [_ids(_ctx(site)), _ids(_ctx(site, config=a)), _ids(_ctx(site, config=b))]
    assert len({tuple(i) for i in ids}) == 3
    assert not set(ids[0]) & set(ids[1]) and not set(ids[1]) & set(ids[2])
    # an edited config file changes them too
    before = _ids(_ctx(site, config=a))
    _write(a, {**fx.config(), "site": "Renamed"})
    assert not set(before) & set(_ids(_ctx(site, config=a)))


def test_several_configs_each_count(site):
    """``--config`` is repeatable (#296): ``ctx.config`` is then a tuple, and each file counts."""
    a = _write(site["root"] / "a.json", fx.config())
    b = _write(site["root"] / "b.json", {**fx.config(), "site": "Other"})
    pair = _ids(_ctx(site, config=(a, b)))
    assert pair == _ids(_ctx(site, config=(a, b)))
    assert not set(pair) & set(_ids(_ctx(site, config=a)))
    _write(b, {**fx.config(), "site": "Renamed"})
    assert not set(pair) & set(_ids(_ctx(site, config=(a, b))))


def test_trust_floor_changes_the_ids(site):
    low = default_registry().call("rule_run", {"facility_id": FID}, _ctx(site, min_trust=0.0))
    high = default_registry().call("rule_run", {"facility_id": FID}, _ctx(site, min_trust=1.0))
    assert not {f["id"] for f in low.facts} & {f["id"] for f in high.facts}
    # the facts of both servers merge for check_citations without a clash
    res = default_registry().call(
        "check_citations",
        {"text": f"{low.text} {high.text}", "facts": low.facts + high.facts},
        CallContext(store=site["store"].root, allowed_facilities=frozenset()),
    )
    assert res.data["grounded"] is True


def test_stored_data_changes_the_ids(site):
    ctx = _ctx(site)
    before = _ids(ctx)
    annex = _ids(ctx, arguments={"facility_id": "annex"})
    site["store"].write_role_frame(
        fx.ahu_frame(seed=7, weeks=4), facility_id=FID, equip="DemoAHU3", equip_class="AHU"
    )
    after = _ids(ctx)
    assert not set(before) & set(after)
    assert _ids(ctx) == after  # and stable again while the data stays as it is
    # a store write changes the point catalog every call may read (#312) ...
    annex_after = _ids(ctx, arguments={"facility_id": "annex"})
    assert annex_after != annex
    # ... but a file of a facility the call does not touch is not its data
    part = next(
        os.path.join(d, f)
        for d, _dirs, files in os.walk(os.path.join(site["store"].root, f"facility_id={FID}"))
        for f in files
        if f.endswith(".parquet")
    )
    os.utime(part, ns=(1, 1))
    assert _ids(ctx, arguments={"facility_id": "annex"}) == annex_after
    assert _ids(ctx) != after
    # a listing touches every allowed facility
    listing = _ids(ctx, "facilities", {})
    site["store"].write_role_frame(
        fx.ahu_frame(seed=8), facility_id="annex", equip="AHU-10", equip_class="AHU"
    )
    assert not set(listing) & set(_ids(ctx, "facilities", {}))


def test_two_stores_with_different_data_give_different_ids(tmp_path):
    a = fx.make_store(tmp_path / "a", meta=None)
    b = fx.make_store(tmp_path / "b", meta=None, fault_week=2)
    ids = [_ids(CallContext(store=st.root, allowed_facilities=frozenset({FID}))) for st in (a, b)]
    assert not set(ids[0]) & set(ids[1])


_RESTART = """
import json, sys
from camber.capabilities import CallContext, default_registry
store, config = sys.argv[1], sys.argv[2]
ctx = CallContext(store=store, allowed_facilities=frozenset({"demo-fac", "annex"}),
                  config=config, min_trust=0.6)
res = default_registry().call("points", {"facility_id": "demo-fac"}, ctx)
print(json.dumps([f["id"] for f in res.facts]))
"""


def test_restart_gives_the_same_ids(site):
    cfg = _write(site["root"] / "a.json", fx.config())
    here = _ids(_ctx(site, config=cfg, min_trust=0.6))
    env = {**os.environ, "PYTHONPATH": ROOT}
    runs = [
        subprocess.run(
            [sys.executable, "-c", _RESTART, site["store"].root, cfg],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=str(site["root"]),
        ).stdout
        for _ in range(2)
    ]
    assert json.loads(runs[0]) == json.loads(runs[1]) == here


def test_token_keeps_the_citation_format(site):
    from camber.agent.verify import _CITE

    token = call_token("points", {"facility_id": FID}, _ctx(site))
    assert len(token) == 6 and set(token) <= set("0123456789abcdef")
    assert _CITE.fullmatch(f"[F1@points-{token}]")
