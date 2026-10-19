"""The forwarder's high-water mark: what each point has had acknowledged, so a poll sends only news.

0.109 (#266). Without it every poll re-reads and re-sends the whole source window; the landing
deduplicates the unchanged months, but the device still spends the reads, the bandwidth and the
PUTs. With it the forwarder keeps, per source point, the **last observation the sink
acknowledged** (its timestamp and value) in ``<spool>/high_water.json`` and a poll

- reads each point only from the start of the month holding its mark (the *open month*), so the
  month part it sends is still a superset of the landed one and the landing replaces it whole
  (0.108, #228), and the last observation, the hour still in progress when it was polled, is
  re-read and its revision sent;
- skips a month whose every point has nothing newer than its mark and an unchanged last value,
  so an idle month costs no PUT.

The mark advances **only on acknowledgement**: a part's per-point last observations wait in
``inflight`` (keyed by the part's spool key) until a drain acks that key, and are dropped when the
key leaves the spool any other way (evicted by the disk cap). A crash, a lost write or a drain run
outside the forwarder only leaves the mark behind, which re-sends data (at-least-once, as before),
never skips it.

The file is written like the spool's other side files: tmp + fsync + atomic ``os.replace`` under
the spool lock. It carries a fingerprint of the facility, the source spec, the mapping, the
resample and the site zone; when they change, or when the file is missing or unreadable, the mark
starts empty and the poll reads the whole window, as before 0.109 (with a WARNING when it was
present). Delete the file to re-read the whole window on purpose (a backfill older than a mark's
month is not re-read otherwise).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime

import pandas as pd

__all__ = ["HIGH_WATER_FILE", "HighWater", "config_fingerprint", "read_high_water"]

_LOG = logging.getLogger("camber.edge")

HIGH_WATER_FILE = "high_water.json"
_VERSION = 1


def _fingerprint(**parts) -> str:
    """A stable sha256 of what the marks depend on (any JSON-able values; key order ignored)."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def config_fingerprint(cfg) -> str:
    """The fingerprint :func:`~camber.edge.config.build_forwarder` gives an
    :class:`~camber.edge.config.EdgeConfig`'s forwarder (``edge status`` compares it)."""
    return _fingerprint(
        facility_id=cfg.facility_id,
        source=cfg.source,
        mapping=cfg.mapping,
        resample=cfg.resample,
        timezone=cfg.timezone,
    )


def _month_start(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.normalize().replace(day=1)


def _parse_mark(obj) -> dict:
    """``{"ts": Timestamp, "value": float}`` from a stored mark; ``ValueError`` when malformed."""
    if not isinstance(obj, dict):
        raise ValueError(f"a mark must be an object, got {obj!r}")
    ts = pd.Timestamp(obj["ts"])
    if ts is pd.NaT:
        raise ValueError("a mark's ts is NaT")
    value = obj["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"a mark's value must be a number, got {value!r}")
    return {"ts": ts, "value": float(value)}


def _dump_mark(mark: dict) -> dict:
    return {"ts": mark["ts"].isoformat(), "value": mark["value"]}


def read_high_water(root: str) -> dict:
    """The state at the spool ``root`` for ``edge status`` (reads only; creates nothing).

    ``{"state": "missing"}``, ``{"state": "unreadable", "error": ...}``, or ``{"state": "ok",
    "fingerprint", "updated_at", "marks": {point: {"ts", "value"}}, "inflight": n}``.
    """
    path = os.path.join(root, HIGH_WATER_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        marks, inflight, fp = _parse(raw)
    except FileNotFoundError:
        return {"state": "missing"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"state": "unreadable", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "state": "ok",
        "fingerprint": fp,
        "updated_at": raw.get("updated_at"),
        "marks": {p: _dump_mark(m) for p, m in sorted(marks.items())},
        "inflight": len(inflight),
    }


def _parse(raw) -> tuple:
    if not isinstance(raw, dict) or raw.get("version") != _VERSION:
        raise ValueError(f"not a version-{_VERSION} high-water state")
    fp = raw["fingerprint"]
    if not isinstance(fp, str):
        raise ValueError("the fingerprint must be a string")
    marks = {str(p): _parse_mark(m) for p, m in dict(raw.get("points", {})).items()}
    inflight = {
        str(k): {str(p): _parse_mark(m) for p, m in dict(v).items()}
        for k, v in dict(raw.get("inflight", {})).items()
    }
    return marks, inflight, fp


class HighWater:
    """The persisted per-point marks of one forwarder (see the module docstring)."""

    def __init__(self, root: str, fingerprint: str, *, clock: Callable[[], datetime]):
        self.root = root
        self.fingerprint = fingerprint
        self._clock = clock
        self.marks: dict = {}
        self.inflight: dict = {}
        self.state = "missing"  # or "ok", "unreadable", "reset"
        self._dirty = False
        self._load()

    @property
    def path(self) -> str:
        return os.path.join(self.root, HIGH_WATER_FILE)

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            marks, inflight, fp = _parse(raw)
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.state = "unreadable"
            self._dirty = True
            _LOG.warning(
                "edge.high_water state %s is unreadable (%s: %s); this poll reads the whole "
                "source window and the state is rewritten",
                self.path,
                type(exc).__name__,
                exc,
            )
            return
        if fp != self.fingerprint:
            self.state = "reset"
            self._dirty = True
            _LOG.warning(
                "edge.high_water the source or config changed since %s was written (%d mark(s) "
                "dropped); this poll reads the whole source window",
                self.path,
                len(marks),
            )
            return
        self.marks, self.inflight, self.state = marks, inflight, "ok"

    # ------------------------------------------------------------------ reading
    def floor(self, point: str, tz=None) -> pd.Timestamp | None:
        """The first timestamp a poll needs for ``point``: its mark's month start, or ``None``.

        ``tz`` (a tz-aware source index's zone) places the month start on that zone's calendar;
        a stored mark keeps only its UTC offset."""
        mark = self.marks.get(point)
        if mark is None:
            return None
        ts = mark["ts"]
        if tz is not None and ts.tzinfo is not None:
            ts = ts.tz_convert(tz)
        return _month_start(ts)

    def is_news(self, point: str, last: dict) -> bool:
        """Whether ``last`` (a ``{"ts", "value"}`` last observation of ``point`` in a month part)
        is past the point's mark, or revises the marked observation."""
        mark = self.marks.get(point)
        if mark is None:
            return True
        try:
            if last["ts"] > mark["ts"]:
                return True
            return bool(last["ts"] == mark["ts"] and last["value"] != mark["value"])
        except TypeError:  # tz-aware against tz-naive: cannot compare, so send it
            return True

    # ------------------------------------------------------------------ writing
    def note_sent(self, key: str, lasts: dict) -> None:
        """Remember the per-point last observations of the part queued as ``key``."""
        if lasts and self.inflight.get(key) != lasts:
            self.inflight[key] = dict(lasts)
            self._dirty = True

    def acknowledge(self, acked, still_queued) -> int:
        """Advance the marks from the parts the sink acked; forget parts that left the spool
        unacknowledged. Returns how many points advanced."""
        advanced = set()
        for key in acked:
            for point, last in self.inflight.pop(key, {}).items():
                mark = self.marks.get(point)
                try:
                    newer = mark is None or last["ts"] >= mark["ts"]
                except TypeError:
                    newer = True
                if newer and mark != last:
                    self.marks[point] = last
                    advanced.add(point)
            self._dirty = True
        gone = [k for k in self.inflight if k not in still_queued]
        for key in gone:  # evicted by the disk cap: never acked, so never a mark
            del self.inflight[key]
            self._dirty = True
        return len(advanced)

    def save(self) -> bool:
        """Write the state atomically when it changed (the caller holds the spool lock)."""
        if not self._dirty:
            return False
        from .spool import _fsync_dir

        rec = {
            "version": _VERSION,
            "fingerprint": self.fingerprint,
            "updated_at": self._clock().isoformat(),
            "points": {p: _dump_mark(m) for p, m in sorted(self.marks.items())},
            "inflight": {
                k: {p: _dump_mark(m) for p, m in sorted(v.items())}
                for k, v in sorted(self.inflight.items())
            },
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        _fsync_dir(self.root)
        self._dirty = False
        self.state = "ok"
        return True
