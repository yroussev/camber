"""Tests for edge config loading + the no-secrets-in-file invariant (camber.edge.config)."""

import json

import pytest

from camber.edge.config import EdgeConfig, build_forwarder, load_config


def _write(tmp_path, obj):
    p = tmp_path / "edge.json"
    p.write_text(json.dumps(obj))
    return str(p)


def test_load_config_env_overlay(tmp_path):
    path = _write(tmp_path, {"facility_id": "file-fac", "resample": "1h", "interval": 60})
    cfg = load_config(
        path,
        environ={"CAMBER_EDGE_FACILITY_ID": "env-fac", "CAMBER_EDGE_INTERVAL": "300"},
    )
    assert isinstance(cfg, EdgeConfig)
    assert cfg.facility_id == "env-fac" and cfg.interval == 300.0


def test_facility_id_required(tmp_path):
    path = _write(tmp_path, {"resample": "1h"})
    with pytest.raises(ValueError, match="facility_id"):
        load_config(path, environ={})


def test_rejects_secret_key_in_file(tmp_path):
    path = _write(tmp_path, {"facility_id": "f", "sink": {"kind": "s3", "secret_key": "AKIA..."}})
    with pytest.raises(ValueError, match="secrets must come from environment"):
        load_config(path, environ={})


def test_rejects_presigned_url_in_file(tmp_path):
    bad = {
        "facility_id": "f",
        "sink": {"kind": "presigned", "url": "https://x.s3/put?X-Amz-Signature=deadbeef"},
    }
    path = _write(tmp_path, bad)
    with pytest.raises(ValueError, match="presigned"):
        load_config(path, environ={})


def test_default_sink_needs_only_env_url(tmp_path):
    path = _write(tmp_path, {"facility_id": "f", "sink": {"kind": "presigned", "host": "lake.org"}})
    cfg = load_config(path, environ={})  # host is not a secret; loads fine
    assert cfg.sink == {"kind": "presigned", "host": "lake.org"}
    # build a forwarder with an injected source; the presigned URL template comes from the env
    src = _DummySource()
    fwd = build_forwarder(
        cfg,
        source=src,
        environ={"CAMBER_EDGE_SINK_URL_TEMPLATE": "https://lake.org/up/{key}"},
    )
    assert fwd.facility_id == "f"


def test_presigned_without_env_url_raises(tmp_path):
    path = _write(tmp_path, {"facility_id": "f", "sink": {"kind": "presigned", "host": "lake.org"}})
    cfg = load_config(path, environ={})
    with pytest.raises(ValueError, match="CAMBER_EDGE_SINK_URL_TEMPLATE"):
        build_forwarder(cfg, source=_DummySource(), environ={})


class _DummySource:
    def point_names(self):
        return []

    def load_points(self, names, resample="1h"):
        import pandas as pd

        return pd.DataFrame()

    def units(self):
        return {}


def test_build_source_csv_kinds(tmp_path):
    from camber.edge.config import _build_source

    (tmp_path / "w.csv").write_text("Timestamp,AHU_1_SupplyAir\n2024-01-01 00:00,55\n")
    src = _build_source({"kind": "csv_wide", "path": str(tmp_path / "w.csv")})
    assert "AHU_1_SupplyAir" in src.point_names()
    import pytest as _pt

    with _pt.raises(NotImplementedError, match="must be injected"):
        _build_source({"kind": "sql"})


def test_build_sink_kinds():
    from camber.edge.config import _build_sink

    assert _build_sink({"kind": "collect"}, {}).__class__.__name__ == "_CollectSink"
    assert _build_sink({"kind": "s3", "bucket": "b"}, {}).__class__.__name__ == "S3Sink"
    assert _build_sink({"kind": "gcs", "bucket": "b"}, {}).__class__.__name__ == "GcsSink"
    assert (
        _build_sink({"kind": "azure", "container": "c"}, {}).__class__.__name__ == "AzureBlobSink"
    )
    import pytest as _pt

    with _pt.raises(ValueError, match="unknown sink kind"):
        _build_sink({"kind": "nope"}, {})
