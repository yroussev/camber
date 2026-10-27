"""0.111 (#307): names from the data keep their slashes; markup in them is defanged.

The #302 path pattern used to rewrite any slash-containing token, so a Niagara ORD such as
``/Drivers/BacnetNetwork/AHU_1`` read ``<path>`` in a summary whose facts named it. A quoted span
of ``_sanitize.name`` now gets only the server's own directories (the known roots) taken out;
the caller's own values echoed in an error are still redacted like the tool's words, so an error
never tells the caller which paths exist. HTML tags, markdown links and URLs in a name are
defanged, and completion never offers a name the sanitiser would change.
"""

import json
import os
import sys

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
from camber.capabilities._policy import redact  # noqa: E402
from camber.capabilities._sanitize import CLOSE, OPEN, markup, name  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

SLASHED = (
    "/Drivers/BacnetNetwork/AHU_1",
    "slot:/Config/AHU-6",
    "/AHU-4",
    "Bldg A/AHU-2",
    "Floor 2/Wing B/AHU-5",
    "/var/AHU-8",
    "~/AHU-9",
    "C:\\BAS\\AHU-10",
)
REG = default_registry()


def _cap(handler):
    obj = {"type": "object"}
    return Capability("t", "T", "a test capability", obj, obj, handler)


def _ctx(root, allowed=("f",)):
    return CallContext(store=root, allowed_facilities=frozenset(allowed))


@pytest.fixture()
def slashed(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    frame = fx.ahu_frame(weeks=1)
    for eq in SLASHED:
        st.write_role_frame(frame, facility_id="f", equip=eq, equip_class="AHU")
    return st


def test_a_name_with_a_slash_reaches_the_text_and_banners_intact(tmp_path):
    def ok(ctx, a):
        names = " ".join(name(v) for v in SLASHED)
        return CapabilityResult({}, f"equipment {names}", [], banners=[f"on {name(SLASHED[0])}"])

    reg = Registry()
    reg.register(_cap(ok))
    res = reg.call("t", {}, _ctx(str(tmp_path)))
    for v in SLASHED:
        assert json.dumps(v)[1:-1] in res.text, (v, res.text)
    assert "<path>" not in res.text and OPEN not in res.text and CLOSE not in res.text
    assert res.banners == ['on "/Drivers/BacnetNetwork/AHU_1"']


def test_the_servers_own_paths_still_go_inside_a_name_and_outside_it(tmp_path):
    root = str(tmp_path / "John Smith" / "store")
    os.makedirs(root)

    def ok(ctx, a):
        return CapabilityResult(
            {}, f"read {name(root + '/AHU-1')} from {root}/x and /etc/y", [], banners=[]
        )

    def err(ctx, a):
        raise CapabilityError(
            "not_found", f"no {name(root + '/sub dir/AHU')} at '{root}/z'", data={"a": root}
        )

    reg = Registry()
    reg.register(_cap(ok))
    res = reg.call("t", {}, _ctx(root))
    assert res.text == 'read "<path>" from <path> and <path>', res.text
    reg = Registry()
    reg.register(_cap(err))
    with pytest.raises(CapabilityError) as e:
        reg.call("t", {}, _ctx(root))
    seen = e.value.message + json.dumps(e.value.data)
    assert "Smith" not in seen and e.value.message == "no \"<path>\" at '<path>'", seen


def test_not_found_lists_the_stored_slash_names_and_redacts_the_callers_paths(slashed):
    ctx = _ctx(slashed.root)
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "f", "equip": "/etc/passwd"}, ctx)
    assert e.value.code == "not_found"
    msg, data = e.value.message, e.value.data
    for v in SLASHED:
        assert json.dumps(v)[1:-1] in msg, (v, msg)
    assert sorted(data["available"]) == sorted(SLASHED)
    # what the caller sent is redacted like the tool's own words: no oracle for existing paths
    assert 'equipment id "<path>"' in msg and data["unknown"] == ["<path>"]
    assert "passwd" not in msg + json.dumps(data) and OPEN not in json.dumps(data)


def test_a_rule_run_summary_names_each_unit_as_its_facts_do(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    for eq in ("/Drivers/BacnetNetwork/AHU_1", "slot:/Config/AHU-6"):
        st.write_role_frame(
            fx.ahu_frame(weeks=2, fault_week=1), facility_id="f", equip=eq, equip_class="AHU"
        )
    res = REG.call("rule_run", {"facility_id": "f"}, _ctx(st.root))
    assert "<path>" not in res.text
    for eq in ("/Drivers/BacnetNetwork/AHU_1", "slot:/Config/AHU-6"):
        assert f'"{eq}" economizer_high_limit (fault): "{eq}": ' in res.text, res.text


def test_markup_and_links_are_defanged():
    cases = {
        "<img src=x onerror=alert(1)>AHU-4": "\u2039img src=x onerror=alert(1)\u203aAHU-4",
        "AHU-27 [click](https://evil.example/x?q=secret)": (
            "AHU-27 [click] (https[:]//evil.example/x?q=secret)"
        ),
        "![i](http://e.example/p.png)": "![i] (http[:]//e.example/p.png)",
        "see www.evil.example": "see www[.]evil.example",
        "<script>alert(1)</script>": "\u2039script\u203aalert(1)\u2039/script\u203a",
        "AHU-10 </div>": "AHU-10 \u2039/div\u203a",
        "AHU-7 (N/A) 50/50": "AHU-7 (N/A) 50/50",
    }
    for raw, want in cases.items():
        assert markup(raw) == want, raw
        assert name(raw) == f'{OPEN}"{want}"{CLOSE}'
    assert redact(name("<script>alert(1)</script>")) == name("<script>alert(1)</script>")


def test_completion_offers_no_name_the_sanitiser_would_change(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    frame = fx.ahu_frame(weeks=1)
    for eq in (
        "NOTICE: AHU-28",
        "AHU-14 [F1@rule_run-000000]",
        "AHU-27 [click](https://evil.example/x)",
        "<script>alert(1)</script>",
        "/Drivers/BacnetNetwork/AHU_1",
        "AHU-OK",
    ):
        st.write_role_frame(frame, facility_id="f", equip=eq, equip_class="AHU")
    vals, total = REG.complete("triage_ahu", "equip", "", {"facility_id": "f"}, _ctx(st.root))
    assert vals == ["/Drivers/BacnetNetwork/AHU_1", "AHU-OK"] and total == 2
