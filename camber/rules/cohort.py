"""Cohort-deviation FDD rule — "this unit runs unlike its peers".

A fleet rule (one batch of like equipment in, one aggregate Finding out) built on
:func:`camber.charts.cohort.cohort_deviation`: it flags units whose behavior on a chosen role
(summarized as mean / peak / load factor / variability) deviates beyond ``k`` robust-σ from the
cohort norm. Enables the deviation-from-peers signal that per-unit rules can't see — a VAV within
every absolute bound can still run unlike its 40 siblings.

**Opt-in options (0.98, #85).** Every default reproduces the earlier behaviour exactly.

* ``group_by_topology`` -- compare each unit only with the units served by the same air handler
  (a served-by topology, else the naming heuristic, exactly as the rogue-zone census groups);
  groups smaller than ``min_cohort`` are left unscored.
* ``normalise`` -- divide each unit's summary before the comparison: ``"design_max"`` by its design
  airflow (``design_max={equip: cfm}``, else the peak of its ``AIRFLOW_SP``), or ``"reference"`` by
  the same summary of a declared reference unit (``reference={equip: reference_equip}``, e.g. the
  same box on a known-good day). Units with no denominator are left out, and reference units are
  not scored themselves.
* ``summary="variability"`` -- the standard deviation: a damper or airflow that never moves sits on
  the low side.
* ``tail`` -- ``"low"`` or ``"high"`` flags one side only.

**Size normalisation alone cannot isolate a stuck box.** On the ORNL test building (default subset:
one box stuck at 0-100 % among ten, one day per position, 15-minute data, grouped per day under its
rooftop unit, ``k`` 3.5), the raw mean airflow never flagged the stuck box (robust z -1.5 to
+2.0), and its airflow as a share of its own peak flagged one stuck day of six while scoring the
fault-free day at z -3.1 and flagging six healthy box-days. Its airflow against its own fault-free
day (``normalise="reference"``) flagged all six stuck days (|z| 22-68) and one healthy box-day; its
damper's variability on the low tail (``summary="variability"``, ``tail="low"``) scored the stuck
days at z -2.1 to -5.0 (three of six past 3.5) and the fault-free day at +5.7, which the low tail
ignores. For a single stuck actuator, the per-box ``actuator_stuck`` rule is the direct detector.
"""

from __future__ import annotations

import pandas as pd

from ..charts.cohort import (
    _SUMMARIES,
    _TAILS,
    cohort_deviation,
    cohort_deviation_from_values,
    cohort_summary,
)
from ..model.roles import Role
from ._topology_grouping import resolve_grouping
from .base import Finding

_NORMALISE = (None, "reference", "design_max")


class CohortDeviation:
    """Flag units deviating > ``k`` robust-σ from their cohort on ``role`` (a FleetRule)."""

    roles_optional: tuple = ()

    def __init__(
        self,
        role,
        *,
        k: float = 3.5,
        min_cohort: int = 3,
        summary: str = "mean",
        name: str | None = None,
        group_by_topology: bool = False,
        normalise: str | None = None,
        reference: dict | None = None,
        design_max: dict | None = None,
        tail: str = "both",
    ):
        self.role = role
        self.k = k
        self.min_cohort = min_cohort
        self.summary = summary
        self.name = name or f"cohort_deviation_{getattr(role, 'name', str(role))}".lower()
        self.roles_required = (role,)
        if summary not in _SUMMARIES:
            raise ValueError(f"summary must be one of {_SUMMARIES}, got {summary!r}")
        if normalise not in _NORMALISE:
            raise ValueError(f"normalise must be one of {_NORMALISE}, got {normalise!r}")
        if tail not in _TAILS:
            raise ValueError(f"tail must be one of {_TAILS}, got {tail!r}")
        if normalise == "reference" and not reference:
            raise ValueError("normalise='reference' needs reference={equip: reference_equip}")
        self.group_by_topology = bool(group_by_topology)
        self.normalise = normalise
        self.reference = dict(reference or {})
        self.design_max = dict(design_max or {})
        self.tail = tail
        if self.group_by_topology:
            self.wants_topology = True  # the runner auto-builds a naming topology when none given
        if normalise == "design_max":
            # the fallback design airflow is the unit's peak airflow setpoint
            self.roles_optional = (Role.AIRFLOW_SP,)

    def _plain(self) -> bool:
        return not self.group_by_topology and self.normalise is None and self.tail == "both"

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run across the cohort's role-frames; return one aggregate Finding."""
        if not self._plain():
            return self._analyze_options(frames, topology)
        res = cohort_deviation(
            frames, self.role, k=self.k, summary=self.summary, min_cohort=self.min_cohort
        )
        rname = getattr(self.role, "name", str(self.role))
        n = len(res.values)
        if n < self.min_cohort:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={"n": n, "min_cohort": self.min_cohort},
                summary=f"cohort: need >= {self.min_cohort} units with {rname}, have {n}",
            )
        metrics = {
            "n": n,
            "summary": self.summary,
            "k": self.k,
            "outliers": res.outliers,
            "z": res.z,
            "median": res.median,
            "mad": res.mad,
        }
        if res.outliers:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="warn",
                metrics=metrics,
                summary=(
                    f"cohort: {len(res.outliers)} of {n} units deviate > {self.k}σ on "
                    f"{rname} ({self.summary}): " + ", ".join(res.outliers)
                ),
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity="ok",
            metrics=metrics,
            summary=f"cohort: all {n} units within {self.k}σ on {rname} ({self.summary})",
        )

    # ------------------------------------------------------------------ 0.98 (#85) options
    def _values(self, frames: dict) -> tuple:
        """``(values, left_out, caveats)``: each unit's (normalised) summary."""
        vals = cohort_summary(frames, self.role, summary=self.summary).dropna()
        left_out: dict = {}
        caveats: list = []
        if self.normalise == "design_max":
            out = {}
            for eq, v in vals.items():
                d = self.design_max.get(eq)
                if d is None:
                    fr = frames.get(eq)
                    if fr is not None and Role.AIRFLOW_SP in fr.columns:
                        peak = pd.to_numeric(fr[Role.AIRFLOW_SP], errors="coerce").max()
                        d = float(peak) if pd.notna(peak) else None
                if d is None or not d > 0:
                    left_out[eq] = "no design airflow (design_max or AIRFLOW_SP)"
                    continue
                out[eq] = float(v) / float(d)
            vals = pd.Series(out, dtype=float)
            caveats.append(
                "normalised by design airflow: a share of design flow evens out box sizes but "
                "cannot by itself single out a stuck box (a healthy box at its minimum and a box "
                "stuck at the same share look alike)"
            )
        elif self.normalise == "reference":
            refs = set(self.reference.values())
            out = {}
            for eq, v in vals.items():
                if eq in refs:
                    continue  # a reference is the denominator, not a unit under test
                ref = self.reference.get(eq)
                if ref is None or ref not in vals.index or not float(vals[ref]) != 0:
                    left_out[eq] = "no reference unit with data"
                    continue
                out[eq] = float(v) / float(vals[ref])
            vals = pd.Series(out, dtype=float)
            caveats.append(
                "normalised by each unit's declared reference (the same summary on the reference "
                "unit): a unit is compared with itself first, then with its peers"
            )
        if left_out:
            caveats.append(f"{len(left_out)} unit(s) left out: no normaliser")
        return vals, left_out, caveats

    def _analyze_options(self, frames: dict, topology) -> Finding:
        rname = getattr(self.role, "name", str(self.role))
        vals, left_out, caveats = self._values(frames)
        groups = {"<all>": list(vals.index)}
        provenance = None
        if self.group_by_topology:
            gm, gcav, provenance, _ = resolve_grouping(None, list(vals.index), topology)
            caveats = list(gcav) + caveats
            if gm:
                groups = {}
                for eq in vals.index:
                    groups.setdefault(gm.get(eq, "<ungrouped>"), []).append(eq)
        z: dict = {}
        outliers: list = []
        by_group: dict = {}
        small: list = []
        for g, members in groups.items():
            if len(members) < self.min_cohort:
                small.extend(members)
                continue
            res = cohort_deviation_from_values(
                vals[members],
                self.role,
                k=self.k,
                summary=self.summary,
                min_cohort=self.min_cohort,
                tail=self.tail,
            )
            z.update(res.z)
            outliers.extend(res.outliers)
            if res.outliers:
                by_group[g] = list(res.outliers)
        n = len(z)
        metrics = {
            "n": n,
            "summary": self.summary,
            "k": self.k,
            "tail": self.tail,
            "normalise": self.normalise,
            "group_by_topology": self.group_by_topology,
            "grouping_provenance": provenance,
            "n_groups": sum(1 for m in groups.values() if len(m) >= self.min_cohort),
            "outliers": outliers,
            "outliers_by_group": by_group,
            "z": z,
            "unscored_small_groups": sorted(small),
            "left_out": left_out,
        }
        if small:
            caveats.append(
                f"{len(small)} unit(s) in groups of fewer than {self.min_cohort} were not scored"
            )
        what = f"{rname} ({self.summary}" + (f", {self.normalise}" if self.normalise else "") + ")"
        side = {"both": "", "low": " low", "high": " high"}[self.tail]
        if n == 0:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics=metrics,
                summary=f"cohort: no group of >= {self.min_cohort} units with {rname}",
                caveats=caveats,
            )
        if outliers:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="warn",
                metrics=metrics,
                summary=(
                    f"cohort: {len(outliers)} of {n} units deviate > {self.k}σ{side} on {what}: "
                    + ", ".join(outliers)
                ),
                caveats=caveats,
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity="ok",
            metrics=metrics,
            summary=f"cohort: all {n} units within {self.k}σ{side} on {what}",
            caveats=caveats,
        )
