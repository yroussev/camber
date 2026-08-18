"""Durable store for **fitted model coefficients**, with a freeze / accept-new-normal policy.

CAMBER persists two things today and neither is a model: :mod:`camber.faultlifecycle` persists
*findings* (the lifecycle of a fault flag) and :mod:`camber.store.parquet_store` persists *data*.
A drift alert needs a third thing -- the fitted baseline it measures against -- to survive between
runs. Refit the baseline from the window you are judging and the comparison is circular: whatever
the equipment is doing now becomes, by construction, normal.

So a baseline here is **frozen**. It is fit once over a commissioning/baseline period, written
with provenance, and thereafter only ever *read*. :meth:`BaselineStore.freeze` refuses to
overwrite an existing record; changing the reference requires the explicit, attributed
:meth:`BaselineStore.accept_new_normal` -- an operator decision ("we cleaned the tubes, this is
the new normal"), never a scheduled or automatic refit. Superseded records are kept in
``history``, so what the baseline used to be, and who moved it, stays answerable.

State is a JSON document written atomically, mirroring :class:`camber.faultlifecycle.FaultLifecycle`
-- coefficient sets are tens of rows, need human inspection more than columnar scans, and reusing
that proven shape adds no dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from ..chillerbaseline import LoadBaseline
from ..integrate.tickets import fingerprint

__all__ = [
    "BaselineRecord",
    "BaselineStore",
]

# kind -> the model class whose ``from_dict`` rebuilds it. New model families register here.
_MODEL_TYPES = {
    "chiller_approach_cond": LoadBaseline,
    "chiller_approach_evap": LoadBaseline,
    "chiller_subcooling": LoadBaseline,
    "chiller_superheat": LoadBaseline,
    "chiller_cw_range": LoadBaseline,
    "cooling_tower_approach": LoadBaseline,
    "chiller_head_pressure": LoadBaseline,
    "chiller_suction_pressure": LoadBaseline,
    "pump_flow": LoadBaseline,
    "pump_head": LoadBaseline,
    "loop_deltat": LoadBaseline,
    "loop_dp": LoadBaseline,
}


@dataclass
class BaselineRecord:
    """One frozen model baseline and the provenance of how it came to be the reference."""

    fingerprint: str
    site: str
    equip: str
    kind: str  # model family, e.g. "chiller_approach_cond" (see _MODEL_TYPES)
    coefficients: dict  # the model's own as_dict() payload
    frozen_at: str  # run_id / ISO timestamp at which this became the reference
    period_start: str = ""  # the window the fit was taken over
    period_end: str = ""
    accepted_by: str = ""  # operator who accepted it (empty for the initial freeze)
    reason: str = ""  # why this is the reference
    supersedes: str = ""  # frozen_at of the baseline this replaced
    history: list = field(default_factory=list)  # superseded records, oldest first

    def as_dict(self) -> dict:
        """Return the record as a plain dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BaselineRecord:
        """Rebuild a record from :meth:`as_dict` output."""
        return cls(**d)

    def model(self):
        """Rebuild the fitted model object from the stored coefficients."""
        typ = _MODEL_TYPES.get(self.kind)
        if typ is None:
            raise KeyError(f"unknown model kind {self.kind!r} (known: {sorted(_MODEL_TYPES)})")
        return typ.from_dict(self.coefficients)


class BaselineStore:
    """A persistent, frozen-by-default store of fitted model coefficients.

    Keyed by the stable ``(site, equip, kind)`` fingerprint, the same scheme
    :mod:`camber.faultlifecycle` uses for findings, so a baseline and the faults measured against
    it line up on the same identity.
    """

    def __init__(self, path: str | None = None):
        self.path = path
        self._recs: dict[str, BaselineRecord] = {}

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: str) -> BaselineStore:
        """Load a baseline store from JSON (empty if the file doesn't exist yet)."""
        st = cls(path)
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            for d in data.get("baselines", []):
                st._recs[d["fingerprint"]] = BaselineRecord.from_dict(d)
        return st

    def save(self, path: str | None = None) -> int:
        """Atomically write the store to JSON; returns the record count."""
        p = path or self.path
        if not p:
            raise ValueError("no path to save to (pass path= or construct with one)")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"baselines": [r.as_dict() for r in self._recs.values()]}, fh, indent=2)
        os.replace(tmp, p)
        return len(self._recs)

    # ----------------------------------------------------------------- lookup
    def key(self, site: str, equip: str, kind: str) -> str:
        """Stable fingerprint for one (site, equip, model-kind) baseline."""
        return fingerprint(site, equip, kind)

    def get(self, site: str, equip: str, kind: str) -> BaselineRecord | None:
        """The frozen record for this equipment/model, or ``None`` if none is frozen yet."""
        return self._recs.get(self.key(site, equip, kind))

    def model_for(self, site: str, equip: str, kind: str):
        """The rebuilt frozen model for this equipment, or ``None`` if none is frozen yet."""
        rec = self.get(site, equip, kind)
        return None if rec is None else rec.model()

    def records(self) -> list:
        """All records, sorted by (site, equip, kind)."""
        return sorted(self._recs.values(), key=lambda r: (r.site, r.equip, r.kind))

    # ----------------------------------------------------------------- write policy
    def freeze(
        self,
        model,
        *,
        site: str,
        equip: str,
        kind: str,
        frozen_at: str,
        period=("", ""),
        reason: str = "initial baseline",
    ) -> BaselineRecord:
        """Freeze a first baseline for this equipment. **Refuses to overwrite an existing one.**

        This establishes the reference; it is not a refit. If a baseline is already frozen here,
        raises :class:`ValueError` -- moving the reference is
        :meth:`accept_new_normal`'s job and must be an attributed decision.
        """
        fp = self.key(site, equip, kind)
        if fp in self._recs:
            raise ValueError(
                f"a baseline is already frozen for {equip!r}/{kind!r}; "
                "use accept_new_normal(...) to supersede it deliberately"
            )
        start, end = period
        rec = BaselineRecord(
            fingerprint=fp,
            site=site,
            equip=equip,
            kind=kind,
            coefficients=model.as_dict(),
            frozen_at=str(frozen_at),
            period_start=str(start),
            period_end=str(end),
            reason=reason,
        )
        self._recs[fp] = rec
        return rec

    def accept_new_normal(
        self,
        model,
        *,
        site: str,
        equip: str,
        kind: str,
        accepted_by: str,
        reason: str,
        at: str,
        period=("", ""),
    ) -> BaselineRecord:
        """Supersede the frozen baseline with a newly fitted one -- an **operator decision**.

        The only sanctioned way the reference ever moves. ``accepted_by`` and ``reason`` are
        required and must be non-empty: an unattributed baseline change is indistinguishable from
        the automatic refit this policy exists to prevent. The superseded record is appended to
        ``history`` so the chain of what was normal, when, and on whose say-so stays intact.

        Accepting where nothing is frozen yet is allowed and behaves as the initial freeze,
        keeping the attribution.
        """
        if not str(accepted_by).strip():
            raise ValueError("accept_new_normal requires accepted_by (who accepted the new normal)")
        if not str(reason).strip():
            raise ValueError("accept_new_normal requires reason (why the baseline moved)")
        fp = self.key(site, equip, kind)
        prev = self._recs.get(fp)
        start, end = period
        rec = BaselineRecord(
            fingerprint=fp,
            site=site,
            equip=equip,
            kind=kind,
            coefficients=model.as_dict(),
            frozen_at=str(at),
            period_start=str(start),
            period_end=str(end),
            accepted_by=str(accepted_by),
            reason=str(reason),
            supersedes=prev.frozen_at if prev is not None else "",
        )
        if prev is not None:
            past = list(prev.history)
            demoted = prev.as_dict()
            demoted["history"] = []  # the chain lives on the live record, not nested copies
            rec.history = past + [demoted]
        self._recs[fp] = rec
        return rec
