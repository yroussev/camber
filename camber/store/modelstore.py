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

**Identity.** Open the store for one facility (``BaselineStore.load(path, facility_id=...)``) and
every record is keyed by ``sha1(facility_id, equip, kind)``: the ``site`` the detectors pass is
kept only as a label, so renaming the facility cannot orphan its references. Unbound (the default,
and outside a portfolio workspace) the key is the ``site`` string, as before. A bound store reads
old site-keyed records through the same deprecated compatibility path as
:class:`camber.faultlifecycle.FaultLifecycle`; ``camber portfolio migrate`` re-keys them on disk.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from .._deprecation import warn_deprecated
from .._statefile import dedup, load_state, save_path, write_json
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
    "pump_power": LoadBaseline,
    "fan_efficiency": LoadBaseline,
    "filter_loading": LoadBaseline,
    "duct_static": LoadBaseline,
    "coil_valve_cool": LoadBaseline,
    "coil_valve_heat": LoadBaseline,
    "economizer_damper": LoadBaseline,
    "vav_damper": LoadBaseline,
    "vav_reheat_valve": LoadBaseline,
    "pump_head": LoadBaseline,
    "loop_deltat": LoadBaseline,
    "loop_dp": LoadBaseline,
    # 0.92 (#13, #14)
    "boiler_efficiency": LoadBaseline,
    "boiler_gas_signature": LoadBaseline,
    "cooling_tower_fan_effort": LoadBaseline,
    # 0.93 (#40, #6): DX / heat-pump refrigerant side, normalized on OAT + return air
    "dx_subcooling": LoadBaseline,
    "dx_superheat": LoadBaseline,
    "dx_temp_split": LoadBaseline,
    "discharge_superheat": LoadBaseline,
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
    facility_id: str = ""  # the identity key; "" for a legacy, site-keyed record
    aliases: list = field(default_factory=list)  # earlier fingerprints (site-keyed, merged)
    # How this reference came to be, beyond the fields above: the M&V store
    # (camber.mandv.rebaseline.MVBaselineStore) records reason, triggers, who and where, the data
    # window and its sha256, the fit statistics, the declared method and the CAMBER version here.
    provenance: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Return the record as a plain dict (an empty ``provenance`` is left out, so a drift
        baseline file is unchanged byte for byte)."""
        d = asdict(self)
        if not d["provenance"]:
            del d["provenance"]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> BaselineRecord:
        """Rebuild a record from :meth:`as_dict` output (unknown keys are ignored)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def model(self, *, model_types=None):
        """Rebuild the fitted model object from the stored coefficients.

        ``model_types`` maps a ``kind`` to the class whose ``from_dict`` rebuilds it (default: the
        drift model families); a ``"*"`` entry is the fallback for any other kind.
        """
        types = _MODEL_TYPES if model_types is None else model_types
        typ = types.get(self.kind) or types.get("*")
        if typ is None:
            raise KeyError(f"unknown model kind {self.kind!r} (known: {sorted(types)})")
        return typ.from_dict(self.coefficients)


def _merge_baselines(live: BaselineRecord, other: BaselineRecord) -> BaselineRecord:
    """Keep ``live`` as the reference and file ``other`` (and its history) under its history.

    Used when two records turn out to be the same equipment's baseline under different keys (a
    site-keyed record and a facility-keyed one, or two site labels of one renamed facility). The
    reference that stays live is the caller's choice; nothing is discarded, and the history stays
    ordered by ``frozen_at``.
    """
    past = [dict(h) for h in live.history] + [dict(h) for h in other.history]
    demoted = other.as_dict()
    demoted["history"] = []
    past.append(demoted)
    past.sort(key=lambda h: str(h.get("frozen_at", "")))
    out = BaselineRecord.from_dict(live.as_dict())
    out.history = past
    out.facility_id = live.facility_id or other.facility_id
    out.aliases = dedup([*live.aliases, other.fingerprint, *other.aliases], drop=out.fingerprint)
    return out


class BaselineStore:
    """A persistent, frozen-by-default store of fitted model coefficients.

    Keyed by the stable ``(key, equip, kind)`` fingerprint, the same scheme
    :mod:`camber.faultlifecycle` uses for findings, so a baseline and the faults measured against
    it line up on the same identity. The key is the store's ``facility_id`` when it is bound to one
    (see :meth:`load`), else the ``site`` string the caller passes.

    ``model_types`` (keyword-only) replaces the ``kind`` -> model-class map that rebuilds stored
    models (default: the drift families); a subclass may also set ``LIST_KEY`` (the JSON list the
    records live under) and ``SCHEMA`` (written as ``"schema"`` on disk; ``None`` writes none, so
    a drift baseline file keeps its historical layout).
    """

    LIST_KEY = "baselines"
    SCHEMA: int | None = None

    def __init__(
        self, path: str | None = None, *, facility_id=None, legacy_sites=None, model_types=None
    ):
        self.path = path
        self.model_types = dict(_MODEL_TYPES if model_types is None else model_types)
        self.facility_id = facility_id or None
        self._explicit_legacy = legacy_sites is not None
        self.legacy_sites = tuple(s for s in (legacy_sites or ()) if s)
        self.legacy_adopted = 0  # site-keyed records re-keyed through the compatibility path
        self._recs: dict[str, BaselineRecord] = {}

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: str, *, facility_id=None, legacy_sites=None, model_types=None):
        """Load a baseline store from JSON (empty if the file doesn't exist yet).

        ``facility_id`` binds the store to one facility (see the module docstring);
        ``legacy_sites`` are the site labels whose site-keyed records belong to it (when not given,
        a lookup's own ``site`` is used; pass them explicitly when a label could be ambiguous). A
        migrated path (a redirect stub) is followed like
        :meth:`camber.faultlifecycle.FaultLifecycle.load`. A file whose ``schema`` is newer
        than this class's ``SCHEMA`` is refused (upgrade CAMBER).
        """
        kw = {} if model_types is None else {"model_types": model_types}
        st = cls(path, facility_id=facility_id, legacy_sites=legacy_sites, **kw)
        data, _where, _red = load_state(path, facility_id=facility_id, list_key=cls.LIST_KEY)
        ver = data.get("schema")
        if isinstance(ver, int) and cls.SCHEMA is not None and ver > cls.SCHEMA:
            raise ValueError(
                f"{path} has schema {ver}; this CAMBER reads up to {cls.SCHEMA} -- upgrade CAMBER"
            )
        for d in data.get(cls.LIST_KEY, []):
            rec = BaselineRecord.from_dict(d)
            st._recs[rec.fingerprint] = rec
        if st.facility_id and st.legacy_sites:
            st._adopt_legacy(st.legacy_sites)
        return st

    def save(self, path: str | None = None) -> int:
        """Atomically write the store to JSON; returns the record count."""
        p = path or self.path
        if not p:
            raise ValueError("no path to save to (pass path= or construct with one)")
        doc: dict = {} if self.SCHEMA is None else {"schema": self.SCHEMA}
        doc[self.LIST_KEY] = [r.as_dict() for r in self._recs.values()]
        write_json(save_path(p, facility_id=self.facility_id), doc)
        return len(self._recs)

    def _adopt_legacy(self, sites) -> int:
        """Re-key this facility's site-keyed records (``site`` in ``sites``) to its id."""
        fid = self.facility_id
        want = {s for s in sites if s}
        if not fid or not want:
            return 0
        legacy = [r for r in self._recs.values() if not r.facility_id and r.site in want]
        for r in sorted(legacy, key=lambda x: str(x.frozen_at)):
            del self._recs[r.fingerprint]
            old = r.fingerprint
            r.facility_id = fid
            r.fingerprint = fingerprint(fid, r.equip, r.kind)
            if old != r.fingerprint and old not in r.aliases:
                r.aliases.append(old)
            cur = self._recs.get(r.fingerprint)
            # a facility-keyed record already in use stays the reference; the old one is history
            self._recs[r.fingerprint] = _merge_baselines(cur, r) if cur is not None else r
        if legacy:
            self.legacy_adopted += len(legacy)
            warn_deprecated(
                f"reading {len(legacy)} site-keyed baseline record(s) through the compatibility "
                "path",
                since="0.86",
                remove_in="2.0",
                use="`camber portfolio migrate` to re-key them to facility_id once, on disk",
                stacklevel=4,
            )
        return len(legacy)

    # ----------------------------------------------------------------- lookup
    def key(self, site: str, equip: str, kind: str) -> str:
        """Stable fingerprint for one baseline: ``(facility_id, equip, kind)`` when the store is
        bound to a facility, else ``(site, equip, kind)``."""
        return fingerprint(self.facility_id or site, equip, kind)

    def get(self, site: str, equip: str, kind: str) -> BaselineRecord | None:
        """The frozen record for this equipment/model, or ``None`` if none is frozen yet."""
        fp = self.key(site, equip, kind)
        if fp not in self._recs and self.facility_id and site and not self._explicit_legacy:
            self._adopt_legacy((site,))
        return self._recs.get(fp)

    def model_for(self, site: str, equip: str, kind: str):
        """The rebuilt frozen model for this equipment, or ``None`` if none is frozen yet."""
        rec = self.get(site, equip, kind)
        return None if rec is None else rec.model(model_types=self.model_types)

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
        accepted_by: str = "",
        provenance: dict | None = None,
    ) -> BaselineRecord:
        """Freeze a first baseline for this equipment. **Refuses to overwrite an existing one.**

        This establishes the reference; it is not a refit. If a baseline is already frozen here,
        raises :class:`ValueError` -- moving the reference is
        :meth:`accept_new_normal`'s job and must be an attributed decision. ``accepted_by`` and
        ``provenance`` (keyword-only) are recorded on the record as given.
        """
        fp = self.key(site, equip, kind)
        if self.get(site, equip, kind) is not None:
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
            accepted_by=str(accepted_by or ""),
            reason=reason,
            facility_id=self.facility_id or "",
            provenance=dict(provenance or {}),
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
        provenance: dict | None = None,
    ) -> BaselineRecord:
        """Supersede the frozen baseline with a newly fitted one -- an **operator decision**.

        The only sanctioned way the reference ever moves. ``accepted_by`` and ``reason`` are
        required and must be non-empty: an unattributed baseline change is indistinguishable from
        the automatic refit this policy exists to prevent. The superseded record is appended to
        ``history`` so the chain of what was normal, when, and on whose say-so stays intact.

        Accepting where nothing is frozen yet is allowed and behaves as the initial freeze,
        keeping the attribution. ``provenance`` (keyword-only) is recorded on the new record.
        """
        if not str(accepted_by).strip():
            raise ValueError("accept_new_normal requires accepted_by (who accepted the new normal)")
        if not str(reason).strip():
            raise ValueError("accept_new_normal requires reason (why the baseline moved)")
        fp = self.key(site, equip, kind)
        prev = self.get(site, equip, kind)
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
            facility_id=self.facility_id or "",
            aliases=list(prev.aliases) if prev is not None else [],
            provenance=dict(provenance or {}),
        )
        if prev is not None:
            past = list(prev.history)
            demoted = prev.as_dict()
            demoted["history"] = []  # the chain lives on the live record, not nested copies
            rec.history = past + [demoted]
        self._recs[fp] = rec
        return rec
