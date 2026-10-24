"""0.110 (#275): the capability registry -- schema validator, policy and the built-in capabilities.

Every test works on a small synthetic store in its own tmp directory; no default cache or
example data is touched.
"""

import hashlib
import json
import os
import re
import sys
import threading
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber._nopersist import persisting  # noqa: E402
from camber.capabilities import (  # noqa: E402
    ERROR_CODES,
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Caps,
    Prompt,
    PromptArg,
    Registry,
    default_registry,
    research_only,
    run_config_for,
)
from camber.capabilities._policy import redact  # noqa: E402
from camber.capabilities._schema import check_schema, validate  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

IDX = pd.date_range("2024-03-01", periods=24, freq="h")
NC = {
    "dataset_id": "some-nc-set",
    "licence": "CC-BY-NC-4.0",
    "access": "research_only",
    "redistribution": "prohibited",
}


def _frame(v=55.0):
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: v, Role.OAT: 60.0, Role.HEAT_VALVE: 0.0}, index=IDX)


@pytest.fixture
def store(tmp_path):
    """Three facilities: ``hq`` (two AHUs), ``annex`` (one AHU), ``nc-lab`` (research-only)."""
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id="hq", equip="AHU-1", equip_class="AHU", name="HQ")
    st.write_role_frame(_frame(56), facility_id="hq", equip="AHU-2", equip_class="AHU")
    st.write_role_frame(_frame(57), facility_id="annex", equip="AHU-9", equip_class="AHU")
    st.write_role_frame(_frame(58), facility_id="nc-lab", equip="AHU-1", equip_class="AHU")
    st.register_facility("nc-lab", name="NC Lab", dataset=NC)
    st.points()  # a current catalog cache, as after any read
    return st


def _ctx(store, allowed=("hq", "annex", "nc-lab"), **kw):
    return CallContext(store=store.root, allowed_facilities=frozenset(allowed), **kw)


def _u(text: str) -> str:
    """``text`` with the per-call token taken off every fact id (``F1@points-1a2b3c`` ->
    ``F1@points``)."""
    return re.sub(r"(@[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*?)-[0-9a-f]{6}\b", r"\1", text)


def _tree(root) -> dict:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = (
                    hashlib.sha256(fh.read()).hexdigest(),
                    os.stat(p).st_mtime_ns,
                )
    return out


def _cap(name="echo", handler=None, input_schema=None, output_schema=None):
    return Capability(
        name=name,
        title=name.title(),
        description="a test capability",
        input_schema=input_schema or {"type": "object", "additionalProperties": True},
        output_schema=output_schema or {"type": "object"},
        handler=handler or (lambda ctx, a: CapabilityResult(data=a, text="", facts=[])),
    )


# --------------------------------------------------------------------------- schema validator


def test_validator_types_and_object_keywords():
    s = {
        "type": "object",
        "required": ["a"],
        "additionalProperties": False,
        "properties": {
            "a": {"type": "integer", "minimum": 1, "maximum": 5},
            "b": {"type": ["string", "null"], "minLength": 2, "maxLength": 4, "pattern": "^x"},
            "c": {"enum": ["p", "q"]},
            "d": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 2},
            "e": {"type": "boolean", "default": True},
        },
    }
    check_schema(s)
    assert validate({"a": 3, "b": "xy", "c": "p", "d": [1.5], "e": False}, s) == []
    assert validate({"a": 3.0, "b": None}, s) == []  # 3.0 is an integer in JSON Schema
    errs = validate({"a": 0, "b": "y", "c": "z", "d": [], "e": 1, "f": 1}, s)
    text = "\n".join(errs)
    for frag in (
        "/a: 0 is below the minimum 1",
        "/b: shorter than 2",
        "/b: does not match",
        "/c: must be one of",
        "/d: fewer than 1",
        "/e: expected boolean, got integer",
        "/: unknown property 'f'",
    ):
        assert frag in text
    assert validate({}, s) == ["/: missing required property 'a'"]
    assert validate({"a": True}, s) == ["/a: expected integer, got boolean"]
    assert validate({"a": 9, "d": [1, "x", 3]}, s) == [
        "/a: 9 is above the maximum 5",
        "/d: more than 2 item(s)",
        "/d/1: expected number, got string",
    ]
    assert validate([1], {"type": "object"}) == ["/: expected object, got array"]
    assert validate({"z": "n"}, {"additionalProperties": {"type": "integer"}}) == [
        "/z: expected integer, got string"
    ]
    assert validate(1, {"const": 1.0}) == [] and validate(True, {"const": 1}) != []
    assert validate(2, {"exclusiveMinimum": 2}) and validate(2, {"exclusiveMaximum": 2})
    assert validate("abcde", {"maxLength": 3}) and validate("x", False) and not validate(1, True)


def test_check_schema_refuses_what_it_cannot_enforce():
    for bad, why in (
        ({"oneOf": []}, "unsupported schema keyword"),
        ({"type": "decimal"}, "unknown type"),
        ({"properties": []}, "'properties' must be"),
        ({"required": "a"}, "'required' must be"),
        ({"enum": "a"}, "'enum' must be"),
        ({"pattern": "("}, "bad 'pattern'"),
        ({"items": {"$ref": "#"}}, "unsupported"),
        ("yes", "must be an object"),
    ):
        with pytest.raises(ValueError, match=why):
            check_schema(bad)


# --------------------------------------------------------------------------- registry basics


def test_registration_ordering_and_duplicates():
    reg = Registry()
    for n in ("zeta", "alpha", "mid_one"):
        reg.register(_cap(n))
    assert [c.name for c in reg.capabilities()] == ["alpha", "mid_one", "zeta"]
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_cap("alpha"))
    for bad in ("Bad", "has-dash", ""):
        with pytest.raises(ValueError, match="snake_case"):
            reg.register(_cap(bad))
    with pytest.raises(ValueError, match="input_schema must be an object schema"):
        reg.register(_cap("arr", input_schema={"type": "array"}))
    # MCP 2025-11-25 allows only an object outputSchema / structuredContent
    for out in ({"type": "array"}, {"properties": {}}, {"type": ["object", "null"]}):
        with pytest.raises(ValueError, match="output_schema must be an object schema"):
            reg.register(_cap("arr_out", output_schema=out))
    with pytest.raises(ValueError, match="unsupported"):
        reg.register(_cap("anyof", output_schema={"anyOf": []}))

    def render(args):
        return [{"role": "user", "text": f"look at {args.get('facility_id')}"}]

    p = Prompt("triage", "Triage", "triage a site", (PromptArg("facility_id", "id", True),), render)
    reg.register_prompt(Prompt("a_first", "A", "a", (), render))
    reg.register_prompt(p)
    assert [x.name for x in reg.prompts()] == ["a_first", "triage"]
    with pytest.raises(ValueError, match="already registered"):
        reg.register_prompt(p)
    assert reg.prompts()[1].render({"facility_id": "hq"})[0]["text"] == "look at hq"

    with pytest.raises(CapabilityError) as e:
        reg.get("nope")
    assert e.value.code == "unknown_capability" and str(e.value).startswith("unknown_capability:")
    with pytest.raises(ValueError, match="unknown capability error code"):
        CapabilityError("oops", "x")
    assert set(ERROR_CODES) >= {"invalid_params", "refused_licence", "timeout", "internal"}


def test_default_registry_is_deterministic():
    names = [c.name for c in default_registry().capabilities()]
    assert {"facilities", "points"} <= set(names) and names == sorted(names)
    assert names == [c.name for c in default_registry().capabilities()]
    for cap in default_registry().capabilities():
        assert cap.description and cap.input_schema["type"] == "object"
    # the full set (#278) is pinned in tests/test_capabilities_278.py


def test_call_context_validation(store):
    with pytest.raises(ValueError, match="allowed_facilities"):
        CallContext(store=store.root, allowed_facilities=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="allowed_facilities"):
        CallContext(store=store.root, allowed_facilities="hq")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_trust"):
        CallContext(store=store.root, allowed_facilities=frozenset(), min_trust=2)
    ctx = CallContext(store=store.root, allowed_facilities={"hq"})  # type: ignore[arg-type]
    assert ctx.allowed_facilities == frozenset({"hq"}) and ctx.caps == Caps()


# --------------------------------------------------------------------------- policy


def test_invalid_params(store):
    reg = default_registry()
    for args in ({}, {"facility_id": ""}, {"facility_id": "hq", "extra": 1}, ["hq"]):
        with pytest.raises(CapabilityError) as e:
            reg.call("points", args, _ctx(store))  # type: ignore[arg-type]
        assert e.value.code == "invalid_params"
    with pytest.raises(CapabilityError) as e:
        reg.call("points", {"facility_id": 7}, _ctx(store))
    assert e.value.data == {"errors": ["/facility_id: expected string, got integer"]}
    with pytest.raises(CapabilityError) as e:
        reg.call("nope", {}, _ctx(store))
    assert e.value.code == "unknown_capability"


def test_facility_allowlist(store):
    reg = default_registry()
    ctx = _ctx(store, allowed=("hq",))
    for fid in ("annex", "no-such-site"):  # existing or not: the same refusal, no probing
        with pytest.raises(CapabilityError) as e:
            reg.call("points", {"facility_id": fid}, ctx)
        assert e.value.code == "not_allowed"
    listed = reg.call("facilities", {}, ctx).data
    assert [f["facility_id"] for f in listed["facilities"]] == ["hq"]
    assert reg.call("facilities", {}, _ctx(store, allowed=())).data["count"] == 0

    # a facility_ids list is checked the same way
    reg2 = Registry()
    reg2.register(_cap("multi"))
    with pytest.raises(CapabilityError) as e:
        reg2.call("multi", {"facility_ids": ["hq", "annex"]}, ctx)
    assert e.value.code == "not_allowed"
    assert reg2.call("multi", {"facility_ids": ["hq"]}, ctx).data == {"facility_ids": ["hq"]}


def test_result_naming_a_facility_outside_the_allowlist_is_internal(store):
    reg = Registry()
    reg.register(_cap("leaky", lambda ctx, a: CapabilityResult({"facility_id": "annex"}, "", [])))
    with pytest.raises(CapabilityError) as e:
        reg.call("leaky", {}, _ctx(store, allowed=("hq",)))
    assert e.value.code == "internal" and "outside the allowlist" in e.value.message


def test_research_only_data_is_refused_with_no_override(store):
    reg = default_registry()
    with pytest.raises(CapabilityError) as e:
        reg.call("points", {"facility_id": "nc-lab"}, _ctx(store))
    assert e.value.code == "refused_licence" and e.value.data == {"facility_id": "nc-lab"}
    res = reg.call("facilities", {}, _ctx(store))
    assert "nc-lab" not in [f["facility_id"] for f in res.data["facilities"]]
    assert res.data["withheld_research_only"] == 1
    assert any("research-only" in b for b in res.banners)


@pytest.mark.parametrize(
    "block, expected",
    [
        ({"access": "research_only"}, True),
        ({"redistribution": "prohibited"}, True),
        ({"licence": "CC-BY-ND-4.0"}, True),
        # a block recorded before the catalog held the dataset research-only: the catalog decides
        ({"dataset_id": "rbc-g36-ahu", "licence": "CC-BY-4.0", "access": "open"}, True),
        ({"dataset_id": "lbnl-sdahu", "licence": "CC-BY-4.0", "access": "open"}, False),
        ({"dataset_id": "retired-id", "licence": "CC0-1.0"}, False),
        ({}, False),
    ],
)
def test_research_only_detection(block, expected):
    assert research_only({"dataset": block} if block else {}) is expected
    assert research_only({"dataset": "not a block"}) is False


def test_handlers_run_no_persist_and_leave_the_store_untouched(store):
    seen = {}

    def spy(ctx, a):
        seen["persisting"] = persisting()
        return CapabilityResult(data={}, text="", facts=[])

    reg = Registry()
    reg.register(_cap("spy", spy))
    reg.call("spy", {}, _ctx(store))
    assert seen == {"persisting": False} and persisting()

    store._invalidate_catalog()  # a reader would now write the catalog cache back
    before = _tree(store.root)
    default = default_registry()
    default.call("facilities", {}, _ctx(store))
    default.call("points", {"facility_id": "hq"}, _ctx(store))
    assert _tree(store.root) == before


def test_rows_are_truncated_with_a_banner(store):
    ctx = _ctx(store, caps=Caps(max_rows=2))
    res = default_registry().call("points", {"facility_id": "hq"}, ctx)
    assert res.truncated is True and len(res.data["points"]) == 2
    assert res.data["count"] == 6  # the count before the cap
    assert any("'points' truncated: showing the first 2 of 6 rows" in b for b in res.banners)
    full = default_registry().call("points", {"facility_id": "hq"}, _ctx(store))
    assert full.truncated is False and len(full.data["points"]) == 6 and not full.banners

    reg = Registry()
    reg.register(_cap("rows", lambda ctx, a: CapabilityResult({"rows": list(range(5))}, "", [])))
    res = reg.call("rows", {}, ctx)
    assert res.data == {"rows": [0, 1]} and res.truncated and "'rows' truncated" in res.banners[0]


def test_too_large(store):
    ctx = _ctx(store, caps=Caps(max_bytes=50))
    with pytest.raises(CapabilityError) as e:
        default_registry().call("points", {"facility_id": "hq"}, ctx)
    assert e.value.code == "too_large" and e.value.data["max_bytes"] == 50


def test_timeout_abandons_a_slow_handler(store):
    release = threading.Event()

    def slow(ctx, a):
        release.wait(5)
        return CapabilityResult(data={}, text="", facts=[])

    reg = Registry()
    reg.register(_cap("slow", slow))
    t0 = time.monotonic()
    with pytest.raises(CapabilityError) as e:
        reg.call("slow", {}, _ctx(store, caps=Caps(max_seconds=0.05)))
    assert e.value.code == "timeout" and time.monotonic() - t0 < 2
    release.set()


def test_internal_errors_never_leak_local_paths(store, tmp_path):
    secret = str(tmp_path / "private" / "client-site" / "trends.csv")

    def boom(ctx, a):
        raise OSError(f"cannot open '{secret}' under {ctx.store} (also ~/x and C:\\data\\y)")

    reg = Registry()
    reg.register(_cap("boom", boom))
    with pytest.raises(CapabilityError) as e:
        reg.call("boom", {}, _ctx(store))
    msg = e.value.message
    assert e.value.code == "internal" and msg.startswith("capability 'boom' failed: OSError")
    assert str(tmp_path) not in msg and "client-site" not in msg and "data" not in msg
    assert e.value.__cause__ is None and e.value.__suppress_context__

    def handler_error(ctx, a):
        raise CapabilityError("not_found", f"nothing at {ctx.store}")

    reg.register(_cap("deliberate", handler_error))
    with pytest.raises(CapabilityError) as e:
        reg.call("deliberate", {}, _ctx(store))
    assert e.value.code == "not_found" and e.value.message == "nothing at <path>"

    def exits(ctx, a):
        raise SystemExit(3)

    reg.register(_cap("exits", exits))
    with pytest.raises(CapabilityError) as e:
        reg.call("exits", {}, _ctx(store))
    assert e.value.code == "internal" and "SystemExit" in e.value.message

    for bad in (
        lambda ctx, a: "not a result",
        lambda ctx, a: CapabilityResult({"x": float("nan")}, "", []),
        lambda ctx, a: CapabilityResult({}, "", [{"id": "F1"}, {"id": "F1"}]),
        lambda ctx, a: CapabilityResult({}, "", [{"kind": "no id"}]),
    ):
        r = Registry()
        r.register(_cap("bad", bad))
        with pytest.raises(CapabilityError) as e:
            r.call("bad", {}, _ctx(store))
        assert e.value.code == "internal"
    r = Registry()
    r.register(_cap("typed", output_schema={"type": "object", "required": ["x"]}))
    with pytest.raises(CapabilityError) as e:
        r.call("typed", {}, _ctx(store))
    assert e.value.code == "internal" and "output schema" in e.value.message
    with pytest.raises(CapabilityError) as e:
        r.call("typed", {}, object())  # type: ignore[arg-type]
    assert e.value.code == "internal"


def test_redact_keeps_units_and_ratios():
    assert redact("1.2 kBtu/ft2 and 3 W/m2 at 50/50") == "1.2 kBtu/ft2 and 3 W/m2 at 50/50"
    assert redact("see /etc/x, ~/y; and D:\\z") == "see <path>, <path>; and <path>"
    assert redact("in some/rel/dir", "some/rel/dir") == "in <path>"


def test_citations_are_checked(store):
    facts = [{"id": "F1@t", "kind": "count", "value": 4, "text": "4 boxes"}]

    def cites(text):
        return lambda ctx, a: CapabilityResult({}, text, facts)

    reg = Registry()
    reg.register(_cap("good", cites("There are 4 boxes [F1@t].")))
    reg.register(_cap("bad", cites("There are 4 boxes [F1@t]. And 9 pumps [F7@t].")))
    good = reg.call("good", {}, _ctx(store))
    assert _u(good.text) == "There are 4 boxes [F1@t]." and not good.banners
    bad = reg.call("bad", {}, _ctx(store))
    assert _u(bad.text) == "There are 4 boxes [F1@t]." and "removed" in bad.banners[0]


# --------------------------------------------------------------------------- the capabilities


def test_facilities_capability(store):
    res = default_registry().call("facilities", {}, _ctx(store))
    rows = {f["facility_id"]: f for f in res.data["facilities"]}
    assert list(rows) == ["annex", "hq"] and res.data["count"] == 2
    hq = rows["hq"]
    assert hq["name"] == "HQ" and hq["state"] == "active" and hq["timezone"] is None
    assert hq["start"] == "2024-03-01T00:00:00" and hq["end"] == "2024-03-01T23:00:00"
    assert (hq["n_points"], hq["n_equipment"]) == (6, 2)
    assert (rows["annex"]["n_points"], rows["annex"]["n_equipment"]) == (3, 1)
    assert _u(res.text).startswith("2 facilities are visible [F1@facilities].")
    assert "hq is active with 6 points [F3@facilities]." in _u(res.text)
    assert [_u(f["id"]) for f in res.facts] == ["F1@facilities", "F2@facilities", "F3@facilities"]
    assert not res.truncated and len(res.banners) == 1  # the research-only notice only
    json.dumps(res.data)


def test_facilities_lists_a_registered_facility_without_data(store):
    store.register_facility("new-site", name="New Site")
    res = default_registry().call("facilities", {}, _ctx(store, allowed=("new-site",)))
    row = res.data["facilities"][0]
    assert row["facility_id"] == "new-site" and row["start"] is None and row["n_points"] == 0
    with pytest.raises(CapabilityError) as e:
        default_registry().call("points", {"facility_id": "new-site"}, _ctx(store, ("new-site",)))
    assert e.value.code == "not_found"


def test_points_capability_filters(store):
    reg, ctx = default_registry(), _ctx(store)
    res = reg.call("points", {"facility_id": "hq"}, ctx)
    assert res.data["count"] == 6 and res.data["n_equipment"] == 2
    assert res.data["points"][0] == {"equip": "AHU-1", "equip_class": "AHU", "role": "heat_valve"}
    assert _u(res.text) == "hq stores 6 matching points on 2 equipment [F1@points]."
    one = reg.call(
        "points", {"facility_id": "hq", "equip": "AHU-2", "role": "supply_air_temp"}, ctx
    )
    assert one.data["points"] == [
        {"equip": "AHU-2", "equip_class": "AHU", "role": "supply_air_temp"}
    ]
    assert "matching equip AHU-2, role supply_air_temp" in one.facts[0]["text"]
    none = reg.call("points", {"facility_id": "hq", "equip_class": "VAV"}, ctx)
    assert none.data["count"] == 0 and none.data["points"] == []


def test_missing_store_is_not_found(tmp_path):
    ctx = CallContext(store=str(tmp_path / "nowhere"), allowed_facilities=frozenset({"hq"}))
    with pytest.raises(CapabilityError) as e:
        default_registry().call("facilities", {}, ctx)
    assert e.value.code == "not_found" and str(tmp_path) not in e.value.message


# --------------------------------------------------------------------------- run helper


def test_run_config_for_applies_policy_and_never_persists(store, tmp_path):
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": "hq"},
        "equipment": [{"class": "AHU"}],
        "rules": ["simultaneous_heat_cool"],
        "faults": {"store": "faults.json", "run_id": "R1"},
        "trust_gate": {"min_trust": 0.2},
    }
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    before = _tree(tmp_path)

    seen = {}

    def run(ctx, a):
        res = run_config_for(ctx)
        seen["res"] = res
        return CapabilityResult({"n": len(res.findings)}, "", [])

    reg = Registry()
    reg.register(_cap("run", run))
    reg.call("run", {}, _ctx(store, config=str(path), min_trust=0.6))
    res = seen["res"]
    assert res.persisted is False and res.faults["persisted"] is False
    assert res.config["trust_gate"]["min_trust"] == 0.6  # raised to the caller's floor
    assert _tree(tmp_path) == before and not (tmp_path / "faults.json").exists()

    for bad_cfg, code in (
        ({**cfg, "source": {**cfg["source"], "facility_id": "annex"}}, "not_allowed"),
        ({**cfg, "source": {**cfg["source"], "facility_id": "nc-lab"}}, "refused_licence"),
        ({"site": "Elsewhere", "source": {"folder": "trends"}}, "not_allowed"),
    ):
        path.write_text(json.dumps(bad_cfg))
        with pytest.raises(CapabilityError) as e:
            reg.call("run", {}, _ctx(store, allowed=("hq", "nc-lab"), config=str(path)))
        assert e.value.code == code
    for ctx_cfg, code in ((None, "invalid_params"), (str(tmp_path / "gone.json"), "not_found")):
        with pytest.raises(CapabilityError) as e:
            reg.call("run", {}, _ctx(store, config=ctx_cfg))
        assert e.value.code == code
