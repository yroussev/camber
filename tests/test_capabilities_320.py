"""0.111 (#320): the last acceptance-test gaps before the release.

1. ``check_citations`` returns the caller's whole ``verified_text`` (and sentences), never cut
   at the 2,000-character cap for values from the data; any value that is cut is said
   (``truncated`` and a notice).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityResult,
    Registry,
    default_registry,
)
from camber.capabilities._sanitize import MAX_VALUE  # noqa: E402

REG = default_registry()


def _ctx(root, allowed=("f",), **kw):
    return CallContext(store=str(root), allowed_facilities=frozenset(allowed), **kw)


# ------------------------------------------------------------- 1. check_citations' own text


def test_verified_text_is_the_whole_supported_draft(tmp_path):
    facts = [{"id": "F1@points-a1b2c3", "text": "AHU-2: 11 stored points on 1 equipment"}]
    sent = "AHU-2 has 11 stored points on 1 equipment [F1@points-a1b2c3]."
    text = " ".join([sent] * 60)
    assert len(text) > MAX_VALUE + 1000
    res = REG.call(
        "check_citations",
        {"text": text, "facts": facts, "detail": True, "limit": 100},
        _ctx(tmp_path, ()),
    )
    assert res.data["grounded"] is True and res.data["n_sentences"] == 60
    assert res.data["verified_text"] == text
    assert not res.truncated and res.banners == []


def test_a_long_sentence_comes_back_whole(tmp_path):
    facts = [{"id": "F1@points-a1b2c3", "text": "AHU-2: 11 stored points"}]
    long = "AHU-2 has 11 stored points " + "and more words " * 200 + "[F1@points-a1b2c3]."
    res = REG.call(
        "check_citations", {"text": long, "facts": facts, "detail": True}, _ctx(tmp_path, ())
    )
    assert res.data["sentences"][0]["text"] == long
    assert res.data["verified_text"] == long


def test_a_cut_value_from_the_data_is_said(tmp_path):
    reg = Registry()
    reg.register(
        Capability(
            name="probe",
            title="Probe",
            description="probe",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            handler=lambda ctx, a: CapabilityResult(
                data={"name": "B" * 3000, "ok": "short"},
                text="A probe.",
                facts=[],
            ),
        )
    )
    res = reg.call("probe", {}, _ctx(tmp_path, ()))
    assert len(res.data["name"]) == MAX_VALUE and res.data["ok"] == "short"
    assert res.truncated
    assert any("1 value(s) longer than 2,000 characters are cut" in b for b in res.banners)
