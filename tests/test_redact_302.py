"""0.111 (#302): path redaction of client-visible messages.

Known roots (the store, the config and its directory, the stores a config reads, the dataset
data directory, home, temp, the working directory and the Python prefix) go by exact prefix, in
quoted and repr'd forms, before a conservative pattern; " / " between words is not a path, and
adjacent placeholders collapse. Every test builds its own tiny store in a tmp directory.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Registry,
    default_registry,
)
from camber.capabilities._policy import known_roots, redact  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = fx.FID
#: directory names that a leak would show: a space, a comma, quotes, Unicode
ODD = ("John Smith", "My Drive", "a,b", "it's", 'say "hi"', "Jöhn Ünïcode", "日本")
REFUSED = (
    f"facility {'nc-lab'!r} holds research-only (non-commercial / no-derivatives) data, "
    "which is never served here"
)


def _odd_dir(base) -> str:
    path = os.path.join(str(base), *ODD)
    os.makedirs(path, exist_ok=True)
    return path


def _leaks(text) -> list:
    return [n for n in (*ODD, "Smith", "Drive", "Ünïcode", "日本") if n in str(text)]


def _cap(name, handler):
    obj = {"type": "object"}
    return Capability(name, name.title(), "a test capability", obj, obj, handler)


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)


# --------------------------------------------------------------------------- the pattern


def test_words_either_side_of_a_slash_are_not_a_path():
    for text in (
        "research-only (non-commercial / no-derivatives) data",
        "1.2 kBtu/ft2 and 3 W/m2 at 50/50, on/off, and/or",
        "see https://example.org/a/b for details",
        "a / b / c",
        "<path> / <path>",
    ):
        assert redact(text) == text


def test_quoted_paths_are_taken_whole_with_spaces_commas_and_unicode():
    for path in (
        "/w/spaces/John Smith/My Drive/archive store",
        "/w/a,dir/store",
        "/h/Jöhn Ünïcode/日本/x.parquet",
        "~/My Drive/store",
        "C:\\Users\\John Smith\\store",
        "\\\\server\\share\\John Smith",
    ):
        for text in (f"in store '{path}'", f'in store "{path}".', f"x {path!r}, y"):
            out = redact(text)
            assert "<path>" in out and not _leaks(out), (text, out)
    # an apostrophe inside a quoted path does not end it
    assert redact("""open "/a/it's here/b" now""") == 'open "<path>" now'
    assert redact("open '/a/it's here/b' now") == "open '<path>' now"
    assert redact("PosixPath('/x/a b, c/d')") == "PosixPath('<path>')"


def test_unquoted_paths_and_adjacent_placeholders():
    assert redact("see /etc/x, ~/y; and D:\\z") == "see <path>, <path>; and <path>"
    assert redact("in some/rel/dir", "some/rel/dir") == "in <path>"
    assert redact("<path><path> and <path>/rest/of/it") == "<path> and <path>"
    msg = "[Errno 2] No such file or directory: '/st ore/facilities//etc/passwd/x.parquet'"
    assert redact(msg, "/st ore") == "[Errno 2] No such file or directory: '<path>'"
    assert redact(msg) == "[Errno 2] No such file or directory: '<path>'"
    # a relative root with no separator is not a path: the word stays
    assert redact("the store at /s", "store") == "the store at <path>"


@pytest.mark.parametrize(
    "root",
    [
        "/srv/John Smith/My Drive/a,b/store",
        "/Users/Jöhn Ünïcode/日本/it's",
        "C:\\Users\\John Smith\\My Drive\\a,b",
        "\\\\server\\share\\John Smith\\a,b",
    ],
)
def test_a_known_root_goes_in_every_spelling(root):
    for text in (
        f"cannot read {root}",
        f"cannot read {root}/sub/f.csv here",
        f"cannot read '{root}/sub dir/f.csv'",
        f"cannot read {os.path.join(root, 'sub dir')!r}",
        f"cannot read {json.dumps(root + '/sub')}",
        f"cannot read {root}{root}",
    ):
        out = redact(text, root)
        assert out.startswith("cannot read ") and "<path><path>" not in out, (text, out)
        assert not _leaks(out), (text, out)


def test_a_home_relative_root(tmp_path, monkeypatch):
    home = _odd_dir(tmp_path / "home")
    monkeypatch.setenv("HOME", home)
    store = os.path.join(home, "My Drive", "store")
    for text in (f"at '{store}/x'", "at '~/My Drive/store/x'", f"at {store}"):
        out = redact(text, "~/My Drive/store")
        assert not _leaks(out) and "<path>" in out, (text, out)


# --------------------------------------------------------------------------- every root


ROOTS = ("store", "config", "config_dir", "config_store", "data_dir", "home", "temp", "cwd")


@pytest.mark.parametrize("which", ROOTS)
def test_every_known_root_is_redacted_in_messages_data_text_and_banners(
    which, store, tmp_path, monkeypatch
):
    odd = _odd_dir(tmp_path / "odd")
    st_root = store.root
    config = str(tmp_path / "cfg" / "run.json")
    os.makedirs(os.path.dirname(config))
    source = {"kind": "store", "store": "store", "facility_id": FID}
    if which == "store":
        st_root = os.path.join(odd, "store")
        os.rename(store.root, st_root)
    elif which in ("config", "config_dir"):
        config = os.path.join(odd, "run.json")
    elif which == "config_store":
        source["store"] = os.path.join(odd, "other store")
    elif which == "data_dir":
        monkeypatch.setenv("CAMBER_DATA_DIR", odd)
    elif which == "home":
        monkeypatch.setenv("HOME", odd)
    elif which == "temp":
        monkeypatch.setattr(tempfile, "tempdir", odd)
    elif which == "cwd":
        monkeypatch.chdir(odd)
    with open(config, "w", encoding="utf-8") as fh:
        json.dump({**fx.config(), "source": source}, fh)
    root = {"store": st_root, "config": config, "config_store": source["store"]}.get(which, odd)
    assert os.path.abspath(root) in {os.path.abspath(r) for r in known_roots(_ctx(st_root, config))}
    leaf = os.path.join(root, "sub dir", "f.csv")

    def err(ctx, a):
        raise CapabilityError("not_found", f"nothing at '{leaf}'", data={"tried": [leaf]})

    def internal(ctx, a):
        raise RuntimeError(f"cannot open {leaf!r}")

    def unavailable(ctx, a):  # an OSError is `unavailable` (#301), which names no path at all
        raise FileNotFoundError(2, "No such file or directory", leaf)

    def ok(ctx, a):
        return CapabilityResult({}, f"read '{leaf}' and {root}/x", [], banners=[f"at {leaf!r}"])

    reg = Registry()
    for name, h in (("err", err), ("internal", internal), ("unavailable", unavailable), ("ok", ok)):
        reg.register(_cap(name, h))
    for name in ("err", "internal"):
        with pytest.raises(CapabilityError) as e:
            reg.call(name, {}, _ctx(st_root, config))
        seen = e.value.message + json.dumps(e.value.data, ensure_ascii=False)
        assert "<path>" in seen and "<path><path>" not in seen and not _leaks(seen), seen
    with pytest.raises(CapabilityError) as e:
        reg.call("unavailable", {}, _ctx(st_root, config))
    seen = e.value.message + json.dumps(e.value.data, ensure_ascii=False)
    assert e.value.code == "unavailable" and not _leaks(seen), seen
    res = reg.call("ok", {}, _ctx(st_root, config))
    assert res.text == "read '<path>' and <path>" and res.banners == ["at '<path>'"]


def _ctx(store_root, config=None, allowed=(FID, "nc-lab")):
    return CallContext(store=store_root, allowed_facilities=frozenset(allowed), config=config)


# --------------------------------------------------------------------------- the capabilities


def test_refused_licence_reads_verbatim(store):
    store.write_role_frame(
        fx.ahu_frame(seed=4), facility_id="nc-lab", equip="AHU-1", equip_class="AHU"
    )
    store.register_facility("nc-lab", name="NC Lab", dataset=fx.NC)
    with pytest.raises(CapabilityError) as e:
        default_registry().call("points", {"facility_id": "nc-lab"}, _ctx(store.root))
    assert e.value.code == "refused_licence" and e.value.message == REFUSED


def test_schema_pointers_are_not_paths(store):
    with pytest.raises(CapabilityError) as e:
        default_registry().call("points", {"facility_id": 3}, _ctx(store.root))
    assert e.value.code == "invalid_params" and e.value.message.startswith("/facility_id")


@pytest.mark.parametrize("name", ["rule_run", "rcx_report", "finding_detail"])
def test_a_config_store_other_than_the_callers_names_no_folder(name, store, tmp_path):
    """The config reads a store in a folder with a space and a comma (not --store): the
    refusal names no folder of it."""
    other = os.path.join(_odd_dir(tmp_path / "spaces"), "archive, store")
    ParquetStore(other).write_role_frame(
        fx.ahu_frame(seed=5), facility_id="elsewhere", equip="AHU-5", equip_class="AHU"
    )
    config = str(tmp_path / "space.json")
    with open(config, "w", encoding="utf-8") as fh:
        json.dump(
            {**fx.config(), "source": {"kind": "store", "store": other, "facility_id": FID}}, fh
        )
    args = {"facility_id": FID}
    if name == "finding_detail":
        args["finding_id"] = "simultaneous_heat_cool@DemoAHU2"
    with pytest.raises(CapabilityError) as e:
        default_registry().call(name, args, _ctx(store.root, config))
    seen = e.value.message + json.dumps(e.value.data, ensure_ascii=False)
    assert "<path>" in seen and not _leaks(seen) and "archive" not in seen, seen
