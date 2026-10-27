"""0.111 (#318): trust-gate inputs, conditional counts, sub-threshold G36 conditions, the fleet
id, rcx fact names, redaction gaps, an oversized line's id and issue tags in parameter bases.

1. A check is declined when a gated input (required, or a present any-of role) is below the
   floor; the decline then names every input the rule reads that is below it, optional ones too.
   An optional input alone does not decline (gating on it would change labelled findings).
2. The conditional NOTICE counts the listed findings and names the conditional results the
   ``min_severity`` floor left out.
3. ``finding_detail`` states only the G36 conditions the rule reported.
4. The fleet pseudo-equipment is shown ``<fleet>``; the defanged spelling is accepted too.
5. ``rcx_report``'s facts name equipment as quoted, defanged names.
6. Known roots are redacted in any case and with either separator, in facts too, completion
   never offers a name holding one, and a defanged ``</b>`` is no path.
7. An oversized line's id is answered only when the whole line names one top-level id.
8. ``finding_detail``'s parameter bases carry no issue tags.
"""

import io
import json
import os
import re
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber import datasets  # noqa: E402
from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Registry,
    default_registry,
)
from camber.capabilities._cap_rule_run import _parameters, _reported_only  # noqa: E402
from camber.capabilities._common import conditional_banner  # noqa: E402
from camber.capabilities._policy import redact, redact_names  # noqa: E402
from camber.capabilities._sanitize import echo, name, unmark  # noqa: E402
from camber.charts.evidence import Evidence  # noqa: E402
from camber.mcp._stdio import _drop_oversized, _TopKeys  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules import base  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

REG = default_registry()
HOME = os.path.expanduser("~")


def _ctx(root, allowed=("f",), **kw):
    return CallContext(store=str(root), allowed_facilities=frozenset(allowed), **kw)


# ------------------------------------------------------------------------- 1. the trust gate


class _Rule:
    name = "probe"
    roles_required = (Role.CO2,)
    roles_optional = (Role.OA_AIRFLOW, Role.OCCUPANCY)


def _gate(monkeypatch, scores):
    health = {r: types.SimpleNamespace(trust=t) for r, t in scores.items()}
    monkeypatch.setattr(base, "_equip_health", lambda *a, **k: health)
    frame = pd.DataFrame({r: [1.0, 2.0] for r in scores})
    ref = types.SimpleNamespace(equip="ROOM-1")
    return base._untrusted(_Rule(), ref, frame, None, "1h", 0.5)


def test_an_optional_input_below_the_floor_alone_does_not_decline(monkeypatch):
    assert _gate(monkeypatch, {Role.CO2: 0.9, Role.OA_AIRFLOW: 0.1, Role.OCCUPANCY: 0.2}) == []


def test_a_decline_names_every_input_below_the_floor(monkeypatch):
    bad = _gate(monkeypatch, {Role.CO2: 0.02, Role.OA_AIRFLOW: 0.01, Role.OCCUPANCY: 0.9})
    assert [r for r, _t in bad] == [Role.CO2, Role.OA_AIRFLOW]
    f = base._decline(_Rule(), types.SimpleNamespace(equip="ROOM-1"), bad, 0.5)
    assert f.metrics["untrusted_roles"] == ["co2", "oa_airflow"]
    assert "co2 (trust 0.02), oa_airflow (trust 0.01)" in f.summary


# -------------------------------------------------------------------- 2. conditional counts


def test_the_conditional_notice_counts_what_is_listed():
    assert conditional_banner(0) is None
    text = conditional_banner(2, {"ok": 1})
    assert text.startswith("2 listed finding(s) rest on an input")
    assert "1 ok result(s) below min_severity rest on such an input too and are not listed" in text
    only = conditional_banner(0, {"ok": 1})
    assert only.startswith("No listed finding rests on") and "1 ok result(s)" in only


@pytest.fixture(scope="module")
def bias(tmp_path_factory):
    root = tmp_path_factory.mktemp("bias318")
    store = str(root / "store")
    datasets.ingest("synthetic-ahu-sensor-bias", store, data_dir=str(root / "cache"))
    cfg = datasets.config_template(
        "synthetic-ahu-sensor-bias", store, exercise="data-sensor-conditional"
    )
    path = root / "bias.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    fid = "ds-synthetic-ahu-sensor-bias"
    return CallContext(store=store, allowed_facilities=frozenset([fid]), config=str(path)), fid


def test_rule_run_counts_the_listed_conditional_findings(bias):
    ctx, fid = bias
    rr = REG.call("rule_run", {"facility_id": fid}, ctx)
    listed = [f for f in rr.data["findings"] if f.get("conditional_on")]
    every = REG.call("rule_run", {"facility_id": fid, "min_severity": "ok"}, ctx)
    hidden = [
        f for f in every.data["findings"] if f.get("conditional_on") and f["severity"] == "ok"
    ]
    assert listed and hidden  # the case the notice has to tell apart
    notice = next(b for b in rr.banners if "configured sensor reference" in b)
    assert notice.startswith(f"{len(listed)} listed finding(s)")
    assert f"{len(hidden)} ok result(s) below min_severity" in notice
    notice = next(b for b in every.banners if "configured sensor reference" in b)
    assert notice.startswith(f"{len(listed) + len(hidden)} listed finding(s)")
    assert "below min_severity" not in notice


# ---------------------------------------------------------- 3. sub-threshold G36 conditions


def test_finding_detail_keeps_only_the_reported_conditions():
    idx = pd.date_range("2026-01-05", periods=6, freq="h")
    fc6 = pd.Series([True, True, False, False, False, False], index=idx)
    fc10 = pd.Series([False, False, False, True, False, False], index=idx)
    app = pd.Series(True, index=idx)
    ev = Evidence(
        renderer="multitrend",
        mask=fc6 | fc10,
        masks={"FC6": fc6, "FC10": fc10},
        judged=app,
        masks_judged={"FC6": (app, "applicable samples"), "FC10": (app, "applicable samples")},
    )
    f = types.SimpleNamespace(metrics={"flagged_fcs": ["FC6"]})
    out = _reported_only(ev, f)
    assert list(out.masks) == ["FC6"] and list(out.masks_judged) == ["FC6"]
    assert out.mask.tolist() == fc6.tolist() and out.judged is app
    none = _reported_only(ev, types.SimpleNamespace(metrics={"flagged_fcs": []}))
    assert none.masks == {} and not none.mask.any()
    # a rule that names no reported conditions keeps its evidence as it drew it
    assert _reported_only(ev, types.SimpleNamespace(metrics={})) is ev


# ------------------------------------------------------------------------- 4. the fleet id


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    root = tmp_path_factory.mktemp("fleet318")
    store = str(root / "store")
    datasets.ingest("synthetic-g36-vav-fleet", store, data_dir=str(root / "cache"))
    fid = "ds-synthetic-g36-vav-fleet"
    return CallContext(store=store, allowed_facilities=frozenset([fid])), fid


def test_the_fleet_id_is_shown_as_the_tools_take_it(fleet):
    ctx, fid = fleet
    args = {"facility_id": fid, "rules": ["zones_heat_cool_census"]}
    rr = REG.call("rule_run", {**args, "min_severity": "ok"}, ctx)
    row = next(f for f in rr.data["findings"] if f["equip"] == "<fleet>")
    assert '"<fleet>" zones_heat_cool_census' in rr.text or any(
        '"<fleet>" zones_heat_cool_census' in f["text"] for f in rr.facts
    )
    assert "\u2039fleet\u203a" not in rr.text
    for want in (row["finding_id"], "zones_heat_cool_census@\u2039fleet\u203a"):
        fd = REG.call("finding_detail", {**args, "finding_id": want}, ctx)
        assert fd.data["finding"]["finding_id"] == row["finding_id"]
    with pytest.raises(CapabilityError) as e:
        REG.call("rule_run", {**args, "equips": ["\u2039fleet\u203a"]}, ctx)
    assert "is the equipment of a fleet finding" in e.value.message


# --------------------------------------------------------------------- 5. rcx fact names

MARKUP = ("<a href=x>AHU</a>", "AHU <script>x", "https://evil.example/zw")


def test_rcx_report_facts_name_equipment_as_quoted_defanged_names(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    for eq in MARKUP:
        st.write_role_frame(fx.ahu_frame(weeks=1), facility_id="f", equip=eq, equip_class="AHU")
    res = REG.call("rcx_report", {"facility_id": "f", "top_n": 20, "limit": 200}, _ctx(st.root))
    texts = [f["text"] for f in res.facts] + [f["text"] for f in res.data["fact_index"]]
    assert texts
    for t in texts:
        assert not any(eq in t for eq in MARKUP), t
        assert "<a " not in t and "</a>" not in t and "<script" not in t, t
        assert "https://" not in t, t
    named = [t for t in texts if "\u2039a href=x\u203aAHU\u2039/a\u203a" in t]
    assert named and all('"\u2039a href=x\u203aAHU\u2039/a\u203a"' in t for t in named)
    # a legacy store may hold an invisible character inside a tag: taken out before the defanging
    assert unmark(name("<\u200bscript>")) == '"\u2039script\u203a"'


# ------------------------------------------------------------------------- 6. redaction


def test_a_known_root_goes_in_any_case_and_with_either_separator():
    upper = HOME.upper() + "/AHU-10"
    back = HOME.replace("/", "\\") + "\\AHU-8"
    for v in (upper, back, HOME + "/AHU-1"):
        out = unmark(redact(f"ids {name(v)}", HOME))
        assert out == 'ids "<path>"', (v, out)
        assert HOME.lower() not in redact_names(v, HOME).lower()


def test_an_echoed_closing_tag_is_not_a_path():
    assert unmark(redact(f"no equipment {echo('AHU <b>bold</b>X')}")) == (
        'no equipment "AHU \u2039b\u203abold\u2039/b\u203aX"'
    )


def test_fact_text_loses_the_known_roots_and_completion_skips_them(tmp_path):
    def ok(ctx, a):
        fact = {"id": "F1@t", "kind": "k", "text": f"AHU on {HOME.upper()}/AHU-2; ok"}
        return CapabilityResult({}, "see [F1@t].", [fact], banners=[])

    obj = {"type": "object"}
    reg = Registry()
    reg.register(Capability("t", "T", "a test capability", obj, obj, ok))
    res = reg.call("t", {}, _ctx(tmp_path))
    assert res.facts[0]["text"] == "AHU on <path>; ok"
    st = ParquetStore(str(tmp_path / "store"))
    for eq in (HOME + "/AHU-home", "AHU-OK"):
        st.write_role_frame(fx.ahu_frame(weeks=1), facility_id="f", equip=eq, equip_class="AHU")
    vals, total = REG.complete("triage_ahu", "equip", "", {"facility_id": "f"}, _ctx(st.root))
    assert vals == ["AHU-OK"] and total == 1


# ------------------------------------------------------------------ 7. an oversized line's id

M = 1_048_576
PAD = "x" * (M + 100)


@pytest.mark.parametrize(
    "line, want",
    [
        ('{"jsonrpc":"2.0","id":"A7","method":"ping","params":{"p":"%s"}}', "A7"),
        ('{"jsonrpc":"2.0","id":"A7","method":"ping","params":{"p":"%s"},"id":"A7b"}', None),
        ('{"jsonrpc":"2.0","id":"A7","method":"ping","params":{"p":"%s"},"\\u0069d":1}', None),
        ('{"jsonrpc":"2.0","id":"A7","method":"ping","params":{"p":"%s","id":3}}', "A7"),
        ('{"jsonrpc":"2.0","id":"A7","method":"ping","params":{"p":"%s\\",\\"id\\":3"}}', "A7"),
    ],
)
def test_an_oversized_line_is_answered_with_its_id_only_when_it_names_one(line, want):
    data = (line % PAD + "\n").encode()
    stream = io.BytesIO(data)
    refusal, more = _drop_oversized(stream.readline(M + 1), stream, M)
    assert refusal["id"] == want and more and refusal["error"]["code"] == -32600


def test_the_top_level_key_count_holds_across_any_chunking():
    data = b'{"a":"x\\"}","b":{"id":1},"id":2,"c":["id"],"\\u0069d":3}'
    for size in (1, 2, 3, 7, len(data)):
        keys = _TopKeys()
        for i in range(0, len(data), size):
            keys.feed(data[i : i + size])
        assert keys.ids == 2, size


# ------------------------------------------------------------ 8. issue tags in parameter bases


def test_finding_detail_parameter_bases_carry_no_issue_tags():
    params = _parameters("g36_afdd", {})
    assert params and any(p.get("basis") for p in params)
    for p in params:
        assert not re.search(r"\((?:[\d.]+,\s*)?#\d+", p.get("basis", "")), p
