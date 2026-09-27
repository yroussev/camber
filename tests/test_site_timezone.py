"""Site time zone: ``Z`` / ``+hh:mm`` and epoch stamps are converted to local time (#56)."""

import os
import warnings

import numpy as np
import pandas as pd
import pytest

from camber.config import run_config
from camber.ingest.csv_long import LongCsvAdapter
from camber.ingest.csv_perpoint import PerPointCsvAdapter
from camber.ingest.csv_wide import WideCsvAdapter
from camber.io import load_csv
from camber.model.roles import Role
from camber.realio import load_point, load_status
from camber.rules.setback_rule import NightWeekendSetback
from camber.tsparse import TimezoneWarning, check_timezone, parse_timestamps

TZ = "America/Chicago"


def _chicago_fan():
    """A 5-minute fan that runs 07:00-16:00 Chicago time on weekdays, stamped in UTC ``Z``."""
    loc = pd.date_range("2026-01-05", "2026-01-31 23:55", freq="5min", tz=TZ)
    fan = ((loc.dayofweek < 5) & (loc.hour >= 7) & (loc.hour < 16)).astype(float)
    return loc, fan, loc.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------ parse_timestamps


def test_z_stamps_convert_to_the_site_wall_clock():
    idx = parse_timestamps(["2026-01-05T13:00:00Z", "2026-07-06T12:00:00Z"], timezone=TZ)
    assert idx.tz is None
    assert list(idx) == [pd.Timestamp("2026-01-05 07:00"), pd.Timestamp("2026-07-06 07:00")]


def test_without_a_site_timezone_the_old_clock_is_kept_and_a_loud_warning_names_the_risk():
    with pytest.warns(TimezoneWarning, match="NOT converted to local time"):
        idx = parse_timestamps(["2026-01-05T13:00:00Z"])
    assert idx[0] == pd.Timestamp("2026-01-05 13:00")  # back-compat: the clock as written


def test_strict_timezone_refuses_offset_stamps_without_a_zone():
    with pytest.raises(ValueError, match="strict_timezone"):
        parse_timestamps(["2026-01-05T13:00:00Z"], strict_timezone=True)
    # naive local stamps are fine under strict: nothing to convert
    assert parse_timestamps(["2026-01-05 07:00"], strict_timezone=True)[0].hour == 7


def test_naive_local_stamps_are_untouched_and_silent_with_or_without_a_zone():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        a = parse_timestamps(["21-Apr-23 8:30:03 AM PDT", "21-Apr-23 8:35:03 AM PDT"])
        b = parse_timestamps(["21-Apr-23 8:30:03 AM PDT"], timezone="America/Los_Angeles")
    assert a[0] == b[0] == pd.Timestamp("2023-04-21 08:30:03")


def test_mixed_offsets_across_a_dst_switch_parse_as_instants():
    # 01:30 CST (-06:00) then 03:30 CDT (-05:00): one hour apart in real time
    vals = ["2026-03-08T01:30:00-06:00", "2026-03-08T03:30:00-05:00"]
    idx = parse_timestamps(vals, timezone=TZ)
    assert list(idx) == [pd.Timestamp("2026-03-08 01:30"), pd.Timestamp("2026-03-08 03:30")]
    utc = parse_timestamps(vals, timezone="UTC")
    assert (utc[1] - utc[0]) == pd.Timedelta(hours=1)
    # without a zone this used to raise inside pandas; now it keeps the clock as written
    with pytest.warns(TimezoneWarning):
        kept = parse_timestamps(vals)
    assert list(kept.hour) == [1, 3]


def test_numeric_offsets_utc_suffix_fractional_seconds_and_epoch():
    assert parse_timestamps(["2026-01-05 13:00:00+0000"], timezone=TZ)[0].hour == 7
    assert parse_timestamps(["2026-01-05 13:00:00 UTC"], timezone=TZ)[0].hour == 7
    assert parse_timestamps(["2026-01-05T13:00:00.250Z"], timezone=TZ)[0] == pd.Timestamp(
        "2026-01-05 07:00:00.250"
    )
    epoch = int(pd.Timestamp("2026-01-05 13:00", tz="UTC").timestamp())
    assert parse_timestamps([epoch, epoch + 300], timezone=TZ)[0] == pd.Timestamp(
        "2026-01-05 07:00"
    )
    with pytest.warns(TimezoneWarning, match="epoch"):
        parse_timestamps([epoch, epoch + 300])


def test_a_date_ending_in_a_year_is_not_an_offset():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        idx = parse_timestamps(["01-02-2018", "01-03-2018"], timezone=TZ)
    assert idx[0] == pd.Timestamp("2018-01-02")


def test_rows_without_an_offset_are_site_local_and_assume_tz_is_converted():
    idx = parse_timestamps(["2026-01-05T13:00:00Z", "2026-01-05 08:00:00"], timezone=TZ)
    assert list(idx.hour) == [7, 8]
    ny = parse_timestamps(["2026-01-05 09:00"], assume_tz="America/New_York", timezone=TZ)
    assert ny[0] == pd.Timestamp("2026-01-05 08:00")
    aware = parse_timestamps(["2026-01-05T13:00:00Z"], timezone=TZ, naive=False)
    assert str(aware.tz) == TZ and aware[0].hour == 7
    assert str(parse_timestamps(["2026-01-05 08:00"], timezone=TZ, naive=False).tz) == TZ
    with pytest.warns(TimezoneWarning):
        kept = parse_timestamps(["2026-01-05T13:00:00Z"], naive=False)
    assert kept[0] == pd.Timestamp("2026-01-05 13:00", tz="UTC")
    mixed = parse_timestamps(["2026-01-05T13:00:00Z", "2026-01-05 08:00"], timezone=TZ, naive=False)
    assert list(mixed.hour) == [7, 8] and str(mixed.tz) == TZ
    ny_mixed = parse_timestamps(
        ["2026-01-05T13:00:00Z", "2026-01-05 09:00"], assume_tz="America/New_York", timezone=TZ
    )
    assert list(ny_mixed.hour) == [7, 8]


def test_tz_aware_datetime_objects_convert():
    vals = pd.Series(pd.date_range("2026-01-05 13:00", periods=2, freq="h", tz="UTC"))
    assert parse_timestamps(vals, timezone=TZ)[0].hour == 7


def test_unknown_zone_is_an_error():
    with pytest.raises(ValueError, match="unknown timezone"):
        parse_timestamps(["2026-01-05 13:00"], timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="IANA"):
        check_timezone("  ")
    assert check_timezone(None) is None and check_timezone(TZ) == TZ


# ------------------------------------------------------------------------------ DST handling


def test_fall_back_repeat_and_spring_forward_gap_after_conversion(tmp_path):
    # hourly UTC across both 2026 switches in Chicago
    fall = pd.date_range("2026-11-01 04:00", "2026-11-01 10:00", freq="h", tz="UTC")
    spring = pd.date_range("2026-03-08 06:00", "2026-03-08 11:00", freq="h", tz="UTC")
    for utc, want_dup, gap in ((fall, 1, None), (spring, 0, pd.Timestamp("2026-03-08 02:00"))):
        stamps = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        idx = parse_timestamps(stamps, timezone=TZ)
        assert int(idx.duplicated().sum()) == want_dup  # 01:00 CDT and 01:00 CST share a label
        p = tmp_path / f"w{want_dup}.csv"
        pd.DataFrame({"ts": stamps, "v": np.arange(len(utc), dtype=float)}).to_csv(p, index=False)
        df = load_csv(str(p), timezone=TZ)  # timegrid.regularize collapses the repeat
        assert df.index.is_unique and df.index.is_monotonic_increasing
        assert len(df) == len(utc) - want_dup
        if gap is not None:
            assert gap not in df.index  # the skipped local hour is simply absent
        mean = load_csv(str(p), timezone=TZ, dedupe="mean")
        if want_dup:
            # "first" keeps the earlier instant (01:00 CDT, UTC 06:00, value 2), in file order
            assert df.loc["2026-11-01 01:00", "v"] == 2.0
            assert load_csv(str(p), timezone=TZ, dedupe="last").loc["2026-11-01 01:00", "v"] == 3.0
            # the two readings stamped 01:00 local (UTC 06:00 and 07:00, values 2 and 3)
            assert mean.loc["2026-11-01 01:00", "v"] == 2.5


# ------------------------------------------------------------------------------ adapters + rules


def test_utc_fan_schedule_is_judged_on_local_time(tmp_path):
    loc, fan, z = _chicago_fan()
    p = tmp_path / "h.csv"
    pd.DataFrame({"timestamp_utc": z, "fan_s": fan}).to_csv(p, index=False)

    df = load_csv(str(p), timezone=TZ)
    on = df.index[df["fan_s"] > 0]
    assert on.hour.min() == 7 and on.hour.max() == 15  # 07:00-16:00 local, not 13:00-22:00
    fr = pd.DataFrame({Role.SUPPLY_FAN_STATUS: df["fan_s"]})
    f = NightWeekendSetback(start_hour=7, end_hour=16).analyze("fcu", fr)
    assert f.severity == "ok" and "setback effective" in f.summary

    with pytest.warns(TimezoneWarning):
        raw = load_csv(str(p))  # no zone: the historical (shifted) reading, with a warning
    fr_raw = pd.DataFrame({Role.SUPPLY_FAN_STATUS: raw["fan_s"]})
    assert NightWeekendSetback(start_hour=7, end_hour=16).analyze("fcu", fr_raw).severity != "ok"
    with pytest.raises(ValueError, match="strict"):
        load_csv(str(p), strict_timezone=True)


def test_every_file_adapter_takes_the_site_zone(tmp_path):
    stamps = ["2026-01-05T13:00:00Z", "2026-01-05T13:05:00Z"]
    pd.DataFrame({"ts": stamps, "p1": [1.0, 2.0]}).to_csv(tmp_path / "wide.csv", index=False)
    wide = WideCsvAdapter(str(tmp_path / "wide.csv"), timezone=TZ).load_points(["p1"], None)
    assert wide.index[0].hour == 7

    pd.DataFrame({"timestamp": stamps, "point": ["p1", "p1"], "value": [1.0, 2.0]}).to_csv(
        tmp_path / "long.csv", index=False
    )
    long = LongCsvAdapter(str(tmp_path / "long.csv"), timezone=TZ).load_points(["p1"], None)
    assert long.index[0].hour == 7

    folder = tmp_path / "pp"
    folder.mkdir()
    pd.DataFrame({"Timestamp": stamps, "Value": [1.0, 2.0]}).to_csv(folder / "p1.csv", index=False)
    pp = PerPointCsvAdapter(str(folder), timezone=TZ).load_points(["p1"], None)
    assert pp.index[0].hour == 7
    assert load_point(str(folder / "p1.csv"), timezone=TZ).index[0].hour == 7
    pd.DataFrame({"Timestamp": stamps, "Value": ["On", "Off"]}).to_csv(
        folder / "s.csv", index=False
    )
    assert load_status(str(folder / "s.csv"), timezone=TZ).index[0].hour == 7


def test_protocol_adapters_take_the_site_zone():
    import sqlite3

    from camber.ingest.bacnet import trendlog_to_series
    from camber.ingest.opcua import history_to_series
    from camber.ingest.sql import SqlSource

    t = pd.Timestamp("2026-01-05 13:00", tz="UTC").to_pydatetime()
    assert history_to_series([(t, 1.0)], timezone=TZ).index[0].hour == 7
    assert trendlog_to_series([(t, 1.0)], timezone=TZ).index[0].hour == 7
    con = sqlite3.connect(":memory:")
    con.execute("create table h (ts text, pt text, v real)")
    con.execute("insert into h values ('2026-01-05T13:00:00Z', 'p1', 1.0)")
    src = SqlSource(con, "h", ts_col="ts", point_col="pt", value_col="v", timezone=TZ)
    assert src.load_points(["p1"], resample=None).index[0].hour == 7


def _fan_folder(folder):
    os.makedirs(folder, exist_ok=True)
    loc, fan, z = _chicago_fan()
    pd.DataFrame({"Timestamp": z, "Value": fan}).to_csv(
        os.path.join(folder, "FCU_1_FanStatus.csv"), index=False
    )


def _cfg(folder, **source):
    return {
        "site": "TzSite",
        "source": {"kind": "perpoint_csv", "folder": folder, **source},
        "mapping": {"aliases": {"FanStatus": "supply_fan_status"}},
        "equipment": [{"class": "FCU", "marker": "FanStatus"}],
        "rules": [{"name": "night_weekend_setback", "params": {"start_hour": 7, "end_hour": 16}}],
    }


def test_config_source_timezone_fixes_the_setback_verdict(tmp_path):
    folder = str(tmp_path / "trends")
    _fan_folder(folder)
    res = run_config(_cfg(folder, timezone=TZ), base_dir=str(tmp_path))
    sb = [f for f in res.findings if f.rule == "night_weekend_setback"]
    assert len(sb) == 1 and sb[0].severity == "ok", sb[0].summary

    with pytest.warns(TimezoneWarning):
        old = run_config(_cfg(folder), base_dir=str(tmp_path))
    assert [f for f in old.findings if f.rule == "night_weekend_setback"][0].severity != "ok"

    with pytest.raises(ValueError, match="strict"):
        run_config(_cfg(folder, strict_timezone=True), base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown timezone"):
        run_config(_cfg(folder, timezone="Nowhere/Zone"), base_dir=str(tmp_path))


def test_config_shared_oat_file_is_converted(tmp_path):
    folder = str(tmp_path / "trends")
    _fan_folder(folder)
    utc = pd.date_range("2026-01-05 13:00", periods=48, freq="h", tz="UTC")
    pd.DataFrame(
        {"Timestamp": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "Value": np.arange(48.0)}
    ).to_csv(tmp_path / "oat.csv", index=False)
    from camber.config import _prepare

    cfg = {**_cfg(folder, timezone=TZ), "shared_oat": {"file": "oat.csv"}}
    prep = _prepare(cfg, str(tmp_path))
    assert prep.shared[Role.OAT].index[0] == pd.Timestamp("2026-01-05 07:00")
    assert all(r.timezone == TZ for r in prep.refs)


def test_store_source_takes_the_catalog_zone_and_warns_on_a_mismatch(tmp_path, monkeypatch):
    from camber import config as cfgmod

    meta = {"dataset": {"dataset_id": "some-dataset"}}

    class _Entry:
        ingest = {"local_timezone": "America/Los_Angeles"}
        timezone = ""

    monkeypatch.setattr("camber.datasets.get", lambda did: _Entry())
    assert cfgmod._site_timezone({}, meta)["timezone"] == "America/Los_Angeles"
    with pytest.warns(UserWarning, match="differs from the zone"):
        got = cfgmod._site_timezone({"timezone": TZ}, meta)
    assert got["timezone"] == TZ
    assert cfgmod._catalog_timezone({}) is None

    def _boom(did):
        raise KeyError(did)

    monkeypatch.setattr("camber.datasets.get", _boom)
    assert cfgmod._site_timezone({}, meta)["timezone"] is None
