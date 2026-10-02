"""Portfolio lifecycle, step 4: month partitions and the retention policy.

The load-bearing claims: new writes land in ``year=/month=`` partitions while year-only stores stay
readable and migrate losslessly; ``camber retention apply`` rolls raw data up, verifies the rollup
against the raw row count and only then prunes; it honours legal hold > facility override >
portfolio default; it is idempotent, a dry run by default, and a crash at any point leaves a state
the next run finishes.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import (  # noqa: E402
    DEFAULT_POLICY,
    RETENTION_SCHEMA,
    Portfolio,
    PortfolioLocked,
)
from camber.portfolio import _retention as _ret  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store import _swap as _swap  # noqa: E402
from camber.store.parquet_store import role_frame_to_long  # noqa: E402

NOW = "2026-09-29"


def _frame(start, periods, freq="h"):
    idx = pd.date_range(start, periods=periods, freq=freq)
    return pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: np.linspace(50, 60, periods), Role.OAT: np.arange(periods) % 7},
        index=idx,
    )


def _legacy_write(root, fid, frame, equip="AHU_1"):
    """Write the pre-0.95 year-only layout, exactly as the old write_long did."""
    long = role_frame_to_long(frame, equip=equip, equip_class="ahu")
    long["facility_id"] = fid
    long["year"] = pd.to_datetime(long["ts"]).dt.year.astype("int32")
    ds.write_dataset(
        pa.Table.from_pandas(long, preserve_index=False),
        root,
        format="parquet",
        partitioning=["facility_id", "year"],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
        basename_template="part-0-{i}.parquet",
    )


def _sorted(df):
    cols = ["ts", "equip", "equip_class", "role", "value"]
    return df[cols].sort_values(["ts", "equip", "role"]).reset_index(drop=True)


# --------------------------------------------------------------------------- month partitions


def test_writes_are_month_partitioned_and_reads_prune_months(tmp_path):
    st = ParquetStore(str(tmp_path / "s"))
    st.write_role_frame(_frame("2025-01-30", 24 * 5), facility_id="f1", equip="AHU_1")
    fdir = tmp_path / "s" / "facility_id=f1" / "year=2025"
    assert sorted(os.listdir(fdir)) == ["month=1", "month=2"]
    parts = st.partitions()
    assert [(p["year"], p["month"], p["legacy"]) for p in parts] == [
        (2025, 1, False),
        (2025, 2, False),
    ]
    assert sum(p["rows"] for p in parts) == 24 * 5 * 2
    feb = st.read_long(facility_id="f1", start="2025-02-01", end="2025-02-28")
    assert len(feb) == 24 * 3 * 2 and feb["month"].unique().tolist() == [2]
    full = st.read_long(facility_id="f1")
    assert len(full) == 24 * 5 * 2 and st.read_role_frame(
        facility_id="f1", equip="AHU_1"
    ).shape == (
        120,
        2,
    )
    filt = ParquetStore._build_filter(start="2025-02-01", months=True)
    frags = list(st._dataset().get_fragments(filter=filt))
    assert frags and all("month=2" in f.path for f in frags)  # January is never opened


def test_next_seq_never_reuses_a_live_file_name_after_a_prune(tmp_path):
    st = ParquetStore(str(tmp_path / "s"))
    for start in ("2023-06-01", "2024-06-01", "2024-06-02"):
        st.write_role_frame(_frame(start, 24), facility_id="f1", equip="AHU_1")
    assert st.prune(before_year=2024) == 1
    st.write_role_frame(_frame("2024-06-03", 24), facility_id="f1", equip="AHU_1")
    assert len(st.read_long(facility_id="f1")) == 3 * 24 * 2  # nothing was overwritten


def test_legacy_year_partitions_read_mixed_and_migrate_losslessly(tmp_path):
    root = str(tmp_path / "s")
    _legacy_write(root, "old", _frame("2024-11-15", 24 * 60))
    st = ParquetStore(root)
    st.write_role_frame(_frame("2025-01-10", 24 * 3), facility_id="old", equip="AHU_2")  # mixed
    before = _sorted(st.read_long(facility_id="old"))
    assert len(before) == (24 * 60 + 24 * 3) * 2
    part = [p for p in st.partitions() if p["legacy"]]
    assert [(p["year"], p["rows"]) for p in part] == [(2024, 24 * 47 * 2), (2025, 24 * 13 * 2)]
    # a range read through a mixed layout never skips the legacy files
    dec = st.read_long(facility_id="old", start="2024-12-01", end="2024-12-31 23:00")
    assert len(dec) == 31 * 24 * 2

    plan = st.migrate_partitions()
    assert plan["dry_run"] and plan["rows"] == 24 * 60 * 2 and not plan["applied"]
    assert any(p["legacy"] for p in st.partitions())
    r = st.migrate_partitions(apply=True)
    assert r["applied"] and not any(p["legacy"] for p in st.partitions())
    ydir = os.path.join(root, "facility_id=old", "year=2025")
    assert sorted(os.listdir(ydir)) == ["_migrated.json", "month=1"]
    legacy_name = next(iter(st.migrated_files("old", 2025)))
    assert legacy_name.endswith(".parquet") and st.migrated_files("old", 1999) == {}
    pd.testing.assert_frame_equal(_sorted(st.read_long(facility_id="old")), before)
    # the month files hold only the stored columns: pyarrow 17-24 read a part file's own hive path
    # into dictionary partition-key columns, which then clashed with the keys on every read
    import pyarrow.parquet as pq

    for p in st.partitions(facility_id="old"):
        for f in os.listdir(p["path"]):
            if f.endswith(".parquet"):
                names = pq.read_schema(os.path.join(p["path"], f)).names
                assert not {"facility_id", "year", "month"} & set(names), (f, names)
    again = st.migrate_partitions(apply=True)
    assert again["partitions"] == [] and not again["applied"]  # idempotent
    st.write_role_frame(_frame("2025-01-20", 24), facility_id="old", equip="AHU_3")
    assert len(st.read_long(facility_id="old")) == len(before) + 48


def test_migrating_a_year_again_never_overwrites_the_rows_it_migrated_before(tmp_path):
    """Year-only files landing after a migration (an older edge forwarder) migrate again safely.

    The stage holds hard links to the year's month files; the second migration used to reuse the
    first one's ``part-legacy0-0`` name and write through the link, truncating the original.
    """
    import pyarrow.parquet as pq

    root = str(tmp_path / "s")
    st = ParquetStore(root)
    ydir = os.path.join(root, "facility_id=old", "year=2024")
    os.makedirs(ydir)

    def legacy(name, value):
        df = role_frame_to_long(_frame("2024-03-01", 10) * 0 + value, equip="AHU_1")
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), os.path.join(ydir, name))

    legacy("part-aaaaaaaaaaaaaaaa.parquet", 1.0)
    st.migrate_partitions(apply=True)
    legacy("part-bbbbbbbbbbbbbbbb.parquet", 2.0)
    st.migrate_partitions(apply=True)
    got = st.read_long(facility_id="old")
    assert len(got) == 40 and sorted(got["value"].unique()) == [1.0, 2.0]
    assert set(st.migrated_files("old", 2024)) == {
        "part-aaaaaaaaaaaaaaaa.parquet",
        "part-bbbbbbbbbbbbbbbb.parquet",
    }


def test_a_crash_mid_migration_leaves_the_data_readable_and_rerun_finishes(tmp_path, monkeypatch):
    root = str(tmp_path / "s")
    _legacy_write(root, "old", _frame("2024-11-15", 24 * 80))
    st = ParquetStore(root)
    before = _sorted(st.read_long(facility_id="old"))
    real = _swap.commit
    calls = []

    def boom(target):
        calls.append(target)
        if len(calls) == 2:
            raise KeyboardInterrupt("simulated crash while swapping the second year")
        return real(target)

    monkeypatch.setattr(_swap, "commit", boom)
    with pytest.raises(KeyboardInterrupt):
        st.migrate_partitions(apply=True)
    monkeypatch.setattr(_swap, "commit", real)
    pd.testing.assert_frame_equal(_sorted(st.read_long(facility_id="old")), before)
    st.migrate_partitions(apply=True)
    assert not any(p["legacy"] for p in st.partitions())
    pd.testing.assert_frame_equal(_sorted(st.read_long(facility_id="old")), before)
    assert not [n for n in os.listdir(os.path.join(root, "facility_id=old")) if n[0] == "_"]


def test_migrate_partitions_inside_a_workspace_is_audited(tmp_path, capsys):
    pf = Portfolio.init(tmp_path / "ws")
    _legacy_write(pf.store_root, "old", _frame("2024-12-30", 48))
    with pytest.raises(ValueError, match="reason is required"):
        pf.store.migrate_partitions(apply=True)
    assert main(["store", "migrate-partitions", pf.store_root]) == 0
    assert "old year=2024" in capsys.readouterr().out
    assert main(["store", "migrate-partitions", pf.store_root, "--apply"]) == 1
    assert "--yes" in capsys.readouterr().err
    rc = main(
        ["store", "migrate-partitions", pf.store_root, "--apply", "--yes", "--reason", "0.95"]
    )
    assert rc == 0 and "applied" in capsys.readouterr().out
    assert pf.audit_log()[-1]["action"] == "store.migrate_partitions"
    assert main(["store", "migrate-partitions", pf.store_root, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["partitions"] == []


# --------------------------------------------------------------------------- the policy


def test_policy_rules_validate_and_precedence_holds(tmp_path):
    assert DEFAULT_POLICY["weather_audit"] == {"keep": "forever"}
    assert _ret.validate_rule("raw_trends", {"keep_months": 36}) == {"keep_months": 36}
    for cls, rule, msg in (
        ("audit", {"keep": "forever"}, "never deleted"),
        ("nope", {}, "unknown data class"),
        ("raw_trends", {"keep_last": 3}, "does not take"),
        ("raw_trends", {"keep_months": 0}, ">= 1"),
        ("raw_trends", {"keep_months": 3, "keep_years": 1}, "not several"),
        ("drift_baselines", {"keep_versions": "some"}, "keep_versions"),
        ("reports", {"keep": "equipment_life"}, "keep must be"),
        ("reports", {}, "empty"),
    ):
        with pytest.raises(ValueError, match=msg):
            _ret.validate_rule(cls, rule)
    assert _ret.parse_rule_args(["keep_months=36", "keep=forever"]) == {
        "keep_months": 36,
        "keep": "forever",
    }
    with pytest.raises(ValueError, match="KEY=VALUE"):
        _ret.parse_rule_args(["keep_months"])
    assert _ret.min_age_days({"keep_months": 25}) == 775
    assert _ret.min_age_days({"keep_years": 7}) == 2562
    assert _ret.min_age_days({"keep": "indefinite"}) is None

    pf = Portfolio.init(tmp_path / "ws")
    a = pf.add_facility("A", reason="x", activate=True)["facility_id"]
    b = pf.add_facility("B", reason="x", activate=True)["facility_id"]
    assert pf.set_retention("raw_trends", {"keep_years": 3}, reason="board decision") == {
        "keep_years": 3
    }
    assert pf.set_retention("drift_baselines", {"keep_versions": 5}, reason="x") == {
        "keep": "equipment_life",
        "keep_versions": 5,
    }
    eff = pf.set_retention_override(a, "raw_trends", {"keep_months": 60}, reason="contract")
    assert eff["raw_trends"] == {"rule": {"keep_months": 60}, "source": "facility"}
    assert pf.effective_retention(b)["raw_trends"]["source"] == "default"
    h = pf.hold(a, reason="litigation")
    assert h["reason"] == "litigation" and pf.hold(a, reason="again") == h  # idempotent
    assert pf.effective_retention(a)["raw_trends"]["source"] == "legal_hold"
    doc = pf.retention_policy(now=NOW)
    assert doc["schema"] == RETENTION_SCHEMA["$id"] and doc["legal_holds"] == [a]
    assert doc["facilities"][a]["rules"]["raw_trends"]["min_age_days"] is None
    assert doc["facilities"][b]["rules"]["raw_trends"]["min_age_days"] == 3 * 366
    assert set(doc["classes"]) == set(DEFAULT_POLICY)
    assert pf.release_hold(a, reason="settled") and not pf.release_hold(a, reason="x")
    assert pf.effective_retention(a)["raw_trends"]["source"] == "facility"
    eff = pf.set_retention_override(a, "raw_trends", None, reason="back to default")
    assert eff["raw_trends"]["source"] == "default"
    with pytest.raises(ValueError, match="never deleted"):
        pf.set_retention("audit", {"keep": "indefinite"}, reason="x")
    acts = [r["action"] for r in pf.audit_log()]
    for want in ("retention.set", "retention.override", "retention.hold", "retention.release"):
        assert want in acts


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _check(inst, sch, root, path="$"):
    """A small validator for the JSON Schema subset RETENTION_SCHEMA uses (no dependency)."""
    if "$ref" in sch:
        sch = root["$defs"][sch["$ref"].rsplit("/", 1)[1]]
    if "const" in sch:
        assert inst == sch["const"], path
    if "enum" in sch:
        assert inst in sch["enum"], path
    if "oneOf" in sch:
        ok = 0
        for sub in sch["oneOf"]:
            try:
                _check(inst, sub, root, path)
                ok += 1
            except AssertionError:
                pass
        assert ok == 1, path
    t = sch.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        good = any(
            (isinstance(inst, int) and not isinstance(inst, bool))
            if x == "integer"
            else isinstance(inst, _TYPES[x])
            for x in types
        )
        assert good, f"{path}: {inst!r} is not {t}"
    if "minimum" in sch and isinstance(inst, int) and not isinstance(inst, bool):
        assert inst >= sch["minimum"], path
    if isinstance(inst, dict):
        for k in sch.get("required", ()):
            assert k in inst, f"{path}: missing {k}"
        props = sch.get("properties", {})
        extra = sch.get("additionalProperties", True)
        for k, v in inst.items():
            if k in props:
                _check(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                raise AssertionError(f"{path}: unexpected {k}")
            elif isinstance(extra, dict):
                _check(v, extra, root, f"{path}.{k}")
    if isinstance(inst, list) and "items" in sch:
        for i, v in enumerate(inst):
            _check(v, sch["items"], root, f"{path}[{i}]")


def test_policy_document_matches_its_json_schema(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility("A", reason="x", activate=True)["facility_id"]
    pf.set_retention_override(fid, "reports", {"keep_last": 3}, reason="x")
    pf.add_facility("B", reason="x", activate=True)
    pf.hold(fid, reason="litigation")
    doc = json.loads(json.dumps(pf.retention_policy()))  # JSON-clean
    _check(doc, RETENTION_SCHEMA, RETENTION_SCHEMA)
    bad = {**doc, "defaults": {"raw_trends": {"keep_months": 0}}}
    with pytest.raises(AssertionError):
        _check(bad, RETENTION_SCHEMA, RETENTION_SCHEMA)
    try:  # the real validator too, where it is installed
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(doc, RETENTION_SCHEMA)


# --------------------------------------------------------------------------- apply


def _ws(tmp_path):
    """A workspace with 3 years of hourly data and per-facility state for one facility."""
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility("North", reason="x", activate=True)["facility_id"]
    pf.store.write_role_frame(
        _frame("2023-06-01", 24 * 1200), facility_id=fid, equip="AHU_1", equip_class="ahu"
    )
    sdir = pf.state_dir(fid)
    os.makedirs(os.path.join(sdir, "reports"))
    faults = [
        {"fingerprint": "old-closed", "status": "resolved", "resolved_at": "2018-01-02",
         "last_seen": "2018-01-01"},
        {"fingerprint": "old-open", "status": "open", "last_seen": "2018-01-01"},
        {"fingerprint": "new-closed", "status": "resolved", "resolved_at": "2026-01-01"},
        {"fingerprint": "odd-date", "status": "suppressed", "last_seen": "run-17"},
    ]  # fmt: skip
    json.dump({"faults": faults}, open(os.path.join(sdir, "faults.json"), "w"))
    hist = [{"frozen_at": f"2020-01-{i + 1:02d}", "coefficients": {}} for i in range(14)]
    json.dump(
        {"baselines": [{"fingerprint": "b1", "kind": "pump_flow", "history": hist}]},
        open(os.path.join(sdir, "baselines.json"), "w"),
    )
    mv = {"schema": 1, "mv_baselines": [{"kind": "mv_bills", "history": hist}]}
    json.dump(mv, open(os.path.join(sdir, "mv_baselines.json"), "w"))
    with open(os.path.join(sdir, "weather_audit.ndjson"), "w") as fh:
        for ts in ("2019-05-01T00:00:00Z", "2026-05-01T00:00:00Z"):
            fh.write(json.dumps({"ts": ts, "service": "isd", "sent": True}) + "\n")
    from camber.portfolio._state import record_outputs

    paths = {}
    for i in range(14):
        p = os.path.join(sdir, "reports", f"r{i:02d}.html")
        open(p, "w").write(str(i))
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        paths[p] = "report"
    record_outputs(pf.root, fid, paths)
    return pf, fid


def test_apply_rolls_up_verifies_then_prunes_and_is_idempotent(tmp_path):
    pf, fid = _ws(tmp_path)
    raw_before = pf.store.read_long(facility_id=fid)
    audit_before = len(pf.audit_log())
    plan = pf.apply_retention(now=NOW)
    fp = plan["facilities"][fid]
    assert plan["dry_run"] and plan["changes"] == 1
    assert [(p["year"], p["month"]) for p in fp["raw"]][:2] == [(2023, 6), (2023, 7)]
    assert fp["raw"][-1]["year"] == 2024 and fp["raw"][-1]["month"] == 7  # keeps >= 25 months
    assert fp["findings"] == ["old-closed"] and fp["drift_versions"] == 5 and fp["mv_versions"] == 0
    assert len(fp["reports"]) == 2 and fp["weather_audit_lines"] == 0
    assert len(pf.audit_log()) == audit_before  # a dry run changes nothing
    assert len(pf.store.read_long(facility_id=fid)) == len(raw_before)

    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert r["problems"] == [] and not r["dry_run"]
    raw_after = pf.store.read_long(facility_id=fid)
    assert pd.Timestamp(raw_after["ts"].min()) == pd.Timestamp("2024-08-01")
    gone = raw_before[pd.to_datetime(raw_before["ts"]) < "2024-08-01"]
    hourly = ParquetStore(os.path.join(pf.root, "rollups", "hourly")).read_long(facility_id=fid)
    daily = ParquetStore(os.path.join(pf.root, "rollups", "daily")).read_long(facility_id=fid)
    assert int(hourly["n"].sum()) == len(gone) == int(daily["n"].sum())
    # the daily mean equals the mean of the raw rows it summarizes
    day = gone[pd.to_datetime(gone["ts"]).dt.floor("D") == pd.Timestamp("2023-06-10")]
    sat = day[day["role"] == Role.SUPPLY_AIR_TEMP.value]["value"].mean()
    got = daily[(daily["ts"] == pd.Timestamp("2023-06-10")) & (daily["role"] == "supply_air_temp")]
    assert got["value"].iloc[0] == pytest.approx(sat) and got["n"].iloc[0] == 24
    sdir = pf.state_dir(fid)
    kept = [f["fingerprint"] for f in json.load(open(os.path.join(sdir, "faults.json")))["faults"]]
    assert kept == ["old-open", "new-closed", "odd-date"]
    b = json.load(open(os.path.join(sdir, "baselines.json")))["baselines"][0]
    assert [h["frozen_at"][-2:] for h in b["history"]] == [f"{d:02d}" for d in range(6, 15)]
    mv = json.load(open(os.path.join(sdir, "mv_baselines.json")))["mv_baselines"][0]
    assert len(mv["history"]) == 14  # M&V (incl. billing) baselines keep every version
    assert sorted(os.listdir(os.path.join(sdir, "reports")))[:1] == ["r02.html"]
    assert len(os.listdir(os.path.join(sdir, "reports"))) == 12
    assert len(open(os.path.join(sdir, "weather_audit.ndjson")).readlines()) == 2
    man = pf.manifest(fid)
    assert "reports/r00.html" not in man["files"] and "reports/r13.html" in man["files"]
    log = pf.audit_log(facility_id=fid)
    assert log[-1]["action"] == "retention.apply" and log[-1]["details"]["raw_partitions"] == 14

    n = len(pf.audit_log())
    again = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert again["changes"] == 0 and len(pf.audit_log()) == n  # idempotent: no-op, no audit


def test_hold_and_override_protect_data(tmp_path):
    pf, fid = _ws(tmp_path)
    pf.hold(fid, reason="litigation")
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert r["held"] == [fid] and r["changes"] == 0
    assert pd.Timestamp(pf.store.read_long(facility_id=fid)["ts"].min()) == pd.Timestamp(
        "2023-06-01"
    )
    pf.release_hold(fid, reason="settled")
    pf.set_retention_override(fid, "raw_trends", {"keep_years": 10}, reason="contract")
    pf.set_retention_override(fid, "weather_audit", {"keep_years": 2}, reason="privacy")
    fp = pf.apply_retention(now=NOW)["facilities"][fid]
    assert fp["raw"] == [] and fp["weather_audit_lines"] == 1
    pf.apply_retention(apply=True, now=NOW, reason="nightly")
    lines = open(os.path.join(pf.state_dir(fid), "weather_audit.ndjson")).readlines()
    assert len(lines) == 1 and "2026-05-01" in lines[0]


def test_hourly_rollups_expire_into_daily(tmp_path):
    pf, fid = _ws(tmp_path)
    pf.apply_retention(apply=True, now=NOW, reason="nightly")
    daily_n = ParquetStore(os.path.join(pf.root, "rollups", "daily")).read_long()["n"].sum()
    # seven years on, the hourly rollups of 2023-06.. expire; the daily ones stay
    june = os.path.join(pf.root, "rollups", "daily", f"facility_id={fid}", "year=2023", "month=6")
    for f in os.listdir(june):
        os.remove(os.path.join(june, f))
    later = "2031-01-15"
    pf.set_retention("raw_trends", {"keep": "indefinite"}, reason="freeze raw for the test")
    fp = pf.apply_retention(now=later)["facilities"][fid]
    assert [(p["year"], p["month"]) for p in fp["hourly"]][0] == (2023, 6)
    pf.apply_retention(apply=True, now=later, reason="nightly")
    hourly = ParquetStore(os.path.join(pf.root, "rollups", "hourly")).read_long()
    assert pd.Timestamp(hourly["ts"].min()) >= pd.Timestamp("2023-08-01")
    daily = ParquetStore(os.path.join(pf.root, "rollups", "daily")).read_long()
    assert daily["n"].sum() == daily_n  # the deleted June daily rollup was rebuilt from hourly


def test_legacy_partitions_are_rolled_up_or_reported(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    _legacy_write(pf.store_root, "old", _frame("2023-01-01", 24 * 600))
    fp = pf.apply_retention(now=NOW)["facilities"]["old"]
    assert [(p["year"], p["legacy"]) for p in fp["raw"]] == [(2023, True)]
    assert fp["skipped_legacy"] == [{"year": 2024, "rows": 24 * 235 * 2}]
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert r["problems"] == []
    assert [p["year"] for p in pf.store.partitions(facility_id="old")] == [2024]
    daily = ParquetStore(os.path.join(pf.root, "rollups", "daily")).read_long()
    assert daily["n"].sum() == 365 * 24 * 2 and sorted(daily["month"].unique()) == list(
        range(1, 13)
    )


def test_a_year_mixing_both_layouts_waits_for_migration(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    _legacy_write(pf.store_root, "old", _frame("2023-01-01", 24 * 30))
    pf.store.write_role_frame(_frame("2023-01-20", 24 * 3), facility_id="old", equip="AHU_2")
    fp = pf.apply_retention(now=NOW)["facilities"]["old"]
    assert fp["raw"] == [] and [x["year"] for x in fp["skipped_legacy"]] == [2023]
    pf.store.migrate_partitions(apply=True, reason="0.95")
    fp = pf.apply_retention(now=NOW)["facilities"]["old"]
    assert [(p["year"], p["month"]) for p in fp["raw"]] == [(2023, 1)]
    pf.apply_retention(apply=True, now=NOW, reason="nightly")
    daily = ParquetStore(os.path.join(pf.root, "rollups", "daily")).read_long()
    assert daily["n"].sum() == (24 * 30 + 24 * 3) * 2  # both layouts' rows in one rollup


def test_a_write_during_the_rollup_keeps_the_partition(tmp_path, monkeypatch):
    pf, fid = _ws(tmp_path)
    real = _ret._read_dir
    hit = []

    def racing(path):
        df = real(path)
        if not hit and os.sep + "store" + os.sep in path and path.endswith("month=6"):
            hit.append(path)  # a backfill lands in the partition being rolled up
            pf.store.write_role_frame(_frame("2023-06-15", 2), facility_id=fid, equip="AHU_9")
        return df

    monkeypatch.setattr(_ret, "_read_dir", racing)
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert len(r["problems"]) == 1 and "written to during the rollup" in r["problems"][0]
    assert (2023, 6) in {(p["year"], p["month"]) for p in pf.store.partitions(facility_id=fid)}
    monkeypatch.undo()
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")  # the next run catches up
    assert r["problems"] == []
    assert (2023, 6) not in {(p["year"], p["month"]) for p in pf.store.partitions(facility_id=fid)}
    hourly = ParquetStore(os.path.join(pf.root, "rollups", "hourly")).read_long(facility_id=fid)
    assert "AHU_9" in set(hourly["equip"])


def test_offboarding_past_its_grace_period_is_archived_by_apply(tmp_path):
    pf, fid = _ws(tmp_path)
    pf.offboard(fid, reason="contract ended", apply=True, now="2026-08-01T00:00:00Z")
    r = pf.apply_retention(now=NOW)
    assert r["archive_due"] == [fid]
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert r["archived"] == [fid] and pf.registry.state(fid) == "archived"
    assert pf.audit_log(facility_id=fid)[-1]["reason"].endswith("grace period ended)")


def test_a_crash_between_rollup_and_prune_is_finished_by_the_next_run(tmp_path, monkeypatch):
    pf, fid = _ws(tmp_path)
    raw_before = pf.store.read_long(facility_id=fid)
    gone = int((pd.to_datetime(raw_before["ts"]) < "2024-08-01").sum())
    real = ParquetStore.drop_partition
    n = []

    def boom(self, *a, **k):
        n.append(a)
        if len(n) == 3:
            raise KeyboardInterrupt("simulated crash after the third rollup")
        return real(self, *a, **k)

    monkeypatch.setattr(ParquetStore, "drop_partition", boom)
    with pytest.raises(KeyboardInterrupt):
        pf.apply_retention(apply=True, now=NOW, reason="nightly")
    monkeypatch.setattr(ParquetStore, "drop_partition", real)
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert r["problems"] == []
    hourly = ParquetStore(os.path.join(pf.root, "rollups", "hourly")).read_long(facility_id=fid)
    assert int(hourly["n"].sum()) == gone  # rollups were replaced, not duplicated
    assert pd.Timestamp(pf.store.read_long(facility_id=fid)["ts"].min()) == pd.Timestamp(
        "2024-08-01"
    )


def test_a_failed_verification_keeps_the_raw_partition(tmp_path, monkeypatch, capsys):
    pf, fid = _ws(tmp_path)
    real = _ret._write_rollup

    def short(root, f, y, m, frame, **kw):
        got = real(root, f, y, m, frame, **kw)
        return {**got, "n": got["n"] - 1} if (y, m) == (2023, 7) else got

    monkeypatch.setattr(_ret, "_write_rollup", short)
    r = pf.apply_retention(apply=True, now=NOW, reason="nightly")
    assert len(r["problems"]) == 1 and "2023-07" in r["problems"][0]
    assert [p["month"] for p in pf.store.partitions(facility_id=fid)][0] == 7  # July kept
    assert pf.audit_log(facility_id=fid)[-1]["action"] == "retention.incomplete"


# --------------------------------------------------------------------------- CLI


def _cli(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_retention(tmp_path, capsys, monkeypatch):
    pf, fid = _ws(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    rc, out, _ = _cli(capsys, "retention", "show")
    assert rc == 0 and "raw_trends" in out and "keep_months=25" in out and "holds: none" in out
    rc, out, _ = _cli(capsys, "retention", "show", "--json")
    assert json.loads(out)["schema"] == "camber.retention/1"
    rc, out, _ = _cli(capsys, "retention", "set", "reports", "keep_last=20", "--reason", "x")
    assert rc == 0 and "keep_last=20" in out
    rc, _, err = _cli(capsys, "retention", "set", "audit", "keep=indefinite", "--reason", "x")
    assert rc == 1 and "never deleted" in err
    rc, out, _ = _cli(
        capsys, "retention", "override", fid, "raw_trends", "keep_months=40", "--reason", "x"
    )
    assert rc == 0 and "keep_months=40 (facility)" in out
    rc, out, _ = _cli(capsys, "retention", "show")
    assert f"{fid}" in out and "keep_months=40" in out
    rc, _, err = _cli(capsys, "retention", "override", fid, "raw_trends", "--reason", "x")
    assert rc == 1 and "--clear" in err
    rc, out, _ = _cli(capsys, "retention", "override", fid, "raw_trends", "--clear",
                      "--reason", "x")  # fmt: skip
    assert rc == 0 and "(default)" in out
    rc, out, _ = _cli(capsys, "retention", "hold", fid, "--reason", "litigation")
    assert rc == 0 and "legal hold since" in out
    rc, out, _ = _cli(capsys, "retention", "show", "--facility", fid)
    assert "LEGAL HOLD" in out and "(legal_hold)" in out
    rc, out, _ = _cli(capsys, "retention", "show", "--facility", fid, "--json")
    assert json.loads(out)["findings"]["source"] == "legal_hold"
    rc, out, _ = _cli(capsys, "retention", "apply", "--now", NOW)
    assert rc == 0 and "LEGAL HOLD -- skipped" in out and "nothing to do" in out
    rc, out, _ = _cli(capsys, "retention", "release", fid, "--reason", "settled")
    assert "hold released" in out
    rc, out, _ = _cli(capsys, "retention", "release", fid, "--reason", "settled")
    assert "was not held" in out
    rc, out, _ = _cli(capsys, "retention", "apply", "--now", NOW)
    assert rc == 0 and "roll up, verify, prune" in out and "--apply" in out
    rc, _, err = _cli(capsys, "retention", "apply", "--now", NOW, "--apply", "--reason", "x")
    assert rc == 1 and "needs confirmation" in err
    rc, out, _ = _cli(
        capsys, "retention", "apply", "--now", NOW, "--apply", "--reason", "cron", "--yes"
    )
    assert rc == 0 and "(applied)" in out
    rc, out, _ = _cli(capsys, "retention", "apply", "--now", NOW, "--json")
    assert json.loads(out)["changes"] == 0

    def locked(self, **kw):
        raise PortfolioLocked("portfolio is locked by 1@host since t")

    monkeypatch.setattr(Portfolio, "apply_retention", locked)
    rc, _, err = _cli(capsys, "retention", "apply", "--apply", "--reason", "cron", "--yes")
    assert rc == 75 and "try again later" in err


def test_cli_retention_reports_problems_and_legacy(tmp_path, capsys, monkeypatch):
    pf = Portfolio.init(tmp_path / "ws")
    _legacy_write(pf.store_root, "old", _frame("2023-01-01", 24 * 600))
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    rc, out, _ = _cli(capsys, "retention", "apply", "--now", NOW)
    assert rc == 0 and "migrate-partitions" in out
    monkeypatch.setattr(_ret, "_write_rollup", lambda *a, **k: {"rows": 0, "n": -1})
    rc, out, _ = _cli(capsys, "retention", "apply", "--now", NOW, "--apply", "--reason", "x",
                      "--confirm", "apply")  # fmt: skip
    assert rc == 1 and "raw partition kept" in out


def test_archived_and_purged_facilities_are_left_alone(tmp_path):
    pf, fid = _ws(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    pf.archive(fid, reason="x", apply=True, skip_grace=True)
    assert pf.apply_retention(now=NOW)["changes"] == 0
    pf.purge(fid, reason="x", apply=True, confirm=fid)
    assert fid not in pf.apply_retention(now=NOW)["facilities"]
    with pytest.raises(KeyError):
        pf.set_retention_override(fid, "reports", {"keep_last": 1}, reason="x")
