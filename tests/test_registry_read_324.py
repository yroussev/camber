"""0.112 (#324): a momentary failure to read the store's facility registry.

A writer replaces ``_facilities.json`` with ``os.replace``; some filesystems (a Docker Desktop
bind mount) show a reader the file as briefly missing. Such a read is retried; one that keeps
failing while the file changes is ``unavailable`` with ``"retryable": true``. A call never runs a
catalog-dataset facility without its packaged template, never refuses it as a licence problem
because of the read, and never carries a registry notice its data contradicts. A registry that
stays missing or damaged keeps the fail-closed refusal (#290).

The read failures are injected (no Docker): every OS runs these tests.
"""

import errno
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber import _servable  # noqa: E402
from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402

FID = fx.FID
DS = "ds-synthetic-ahu-excess-oa"
REG = default_registry()
_TEMPLATE_RULES = ["economizer_high_limit", "outdoor_air_fraction"]


@pytest.fixture(autouse=True)
def _quick_retries(monkeypatch):
    monkeypatch.setattr(_servable, "_RETRY_WAITS", (0.0, 0.001, 0.001, 0.001))
    _servable._SEEN.clear()


def _dataset_store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.write_role_frame(fx.ahu_frame(seed=2), facility_id=DS, equip="AHU__x", equip_class="AHU")
    st.register_facility(
        DS,
        name="Synthetic AHU",
        dataset={"dataset_id": "synthetic-ahu-excess-oa", "licence": "CC0-1.0"},
    )
    return st


def _ctx(st):
    return CallContext(store=st.root, allowed_facilities=frozenset({FID, DS}))


class _Flaky:
    """Stands in for the registry's raw read: ``plan`` says what each read does in turn (an
    exception to raise, or ``None`` to read the file); reads past the plan read the file."""

    def __init__(self, monkeypatch, plan):
        self.plan = list(plan)
        self.reads = 0
        self._real = _servable._read_regular
        monkeypatch.setattr(_servable, "_read_regular", self)

    def __call__(self, path):
        self.reads += 1
        step = self.plan.pop(0) if self.plan else None
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, bytes):
            return step
        return self._real(path)


def _missing():
    return FileNotFoundError(errno.ENOENT, "No such file or directory")


def test_a_template_run_survives_a_momentary_registry_read_failure(tmp_path, monkeypatch):
    st = _dataset_store(tmp_path)
    clean = REG.call("rule_run", {"facility_id": DS}, _ctx(st))
    assert clean.data["rules_run"] == _TEMPLATE_RULES
    # every second read of the registry fails, as on a bind mount during a writer's replace
    flaky = _Flaky(monkeypatch, [_missing(), None] * 10)
    res = REG.call("rule_run", {"facility_id": DS}, _ctx(st))
    assert flaky.reads == 2  # the failed read, then the retry the whole call uses
    assert res.data["rules_run"] == _TEMPLATE_RULES
    assert res.data["from_config"] is True
    assert res.data["counts"] == clean.data["counts"]
    assert res.banners == clean.banners  # no registry notice, and the template's banner kept


@pytest.mark.parametrize("name", ["rule_run", "sensor_trust", "facilities"])
def test_a_first_read_failure_is_retried_not_refused(tmp_path, monkeypatch, name):
    st = _dataset_store(tmp_path)
    args = {} if name == "facilities" else {"facility_id": DS}
    clean = REG.call(name, args, _ctx(st))
    _Flaky(monkeypatch, [_missing(), _missing()])
    res = REG.call(name, args, _ctx(st))
    assert res.data == clean.data and res.banners == clean.banners


def test_a_call_reads_the_registry_once(tmp_path, monkeypatch):
    st = _dataset_store(tmp_path)
    flaky = _Flaky(monkeypatch, [])
    REG.call("rule_run", {"facility_id": DS}, _ctx(st))
    assert flaky.reads == 1  # the allowlist check, the template and the notice share one read


def _unavailable(st, name="rule_run"):
    args = {} if name == "facilities" else {"facility_id": DS}
    with pytest.raises(CapabilityError) as ei:
        REG.call(name, args, _ctx(st))
    return ei.value


@pytest.mark.parametrize("name", ["rule_run", "sensor_trust", "facilities"])
def test_a_registry_that_keeps_changing_unreadably_is_unavailable_retryable(
    tmp_path, monkeypatch, name
):
    st = _dataset_store(tmp_path)
    # missing, then half written, then missing ...: a writer at work, never a settled state
    _Flaky(monkeypatch, [_missing(), b'{"ds-', _missing(), b"{"] * 10)
    e = _unavailable(st, name)
    assert e.code == "unavailable", (e.code, e.message)
    assert e.data == {"retryable": True, "reason": "registry_unreadable"}
    assert "facility registry" in e.message and "retry" in e.message
    assert "licence" not in e.message


def test_an_io_error_reading_the_registry_is_unavailable_not_a_licence_refusal(
    tmp_path, monkeypatch
):
    st = _dataset_store(tmp_path)
    _Flaky(monkeypatch, [OSError(errno.EIO, "Input/output error")] * 50)
    e = _unavailable(st)
    assert e.code == "unavailable" and e.data["retryable"] is True


def test_a_registry_that_stays_missing_is_still_refused(tmp_path):
    st = _dataset_store(tmp_path)
    os.remove(os.path.join(st.root, "_facilities.json"))
    with pytest.raises(CapabilityError) as ei:
        REG.call("rule_run", {"facility_id": DS}, _ctx(st))
    assert ei.value.code == "refused_licence"
    assert _servable.read_registry(st.root)[1].startswith("is missing")


def test_a_registry_that_stays_damaged_is_still_refused(tmp_path):
    st = _dataset_store(tmp_path)
    with open(os.path.join(st.root, "_facilities.json"), "w") as fh:
        fh.write('{"ds-')
    assert _servable.read_registry(st.root) == ({}, "cannot be read")
    with pytest.raises(CapabilityError) as ei:
        REG.call("rule_run", {"facility_id": DS}, _ctx(st))
    assert ei.value.code == "refused_licence"


def test_a_vanished_registry_seen_before_is_retried_in_a_store_of_ones_own_data(
    tmp_path, monkeypatch
):
    st = fx.make_store(tmp_path, meta=None)
    st.register_facility(FID, timezone="America/Chicago")
    entries, problem = _servable.read_registry(st.root)
    assert problem is None and entries[FID]["timezone"] == "America/Chicago"
    flaky = _Flaky(monkeypatch, [_missing()])
    entries, problem = _servable.read_registry(st.root)
    assert flaky.reads == 2 and entries[FID]["timezone"] == "America/Chicago"


def test_the_store_registry_reads_the_calls_snapshot(tmp_path, monkeypatch):
    from camber.store import ParquetStore

    st = _dataset_store(tmp_path)
    with _servable.call_snapshot():
        before = ParquetStore(st.root).facilities_meta()
        # the file goes away mid-call: the call keeps the registry it read
        os.rename(os.path.join(st.root, "_facilities.json"), str(tmp_path / "aside.json"))
        assert ParquetStore(st.root).facilities_meta() == before
        assert _servable.read_registry(st.root)[1] is None
    assert ParquetStore(st.root).facilities_meta() == {}


def test_startup_says_retry_when_the_registry_cannot_be_read(tmp_path, monkeypatch):
    from camber.mcp import _launch

    st = _dataset_store(tmp_path)
    _Flaky(monkeypatch, [_missing(), b"{"] * 20)
    with pytest.raises(_launch.LaunchError, match="facility registry"):
        _launch.resolve_allowlist(st.root, None, True)


def test_unreadable_registry_bytes_are_damaged_not_a_crash(tmp_path):
    st = _dataset_store(tmp_path)
    with open(os.path.join(st.root, "_facilities.json"), "wb") as fh:
        fh.write(b"\xff\xfe{}")
    assert _servable.read_registry(st.root) == ({}, "cannot be read")
