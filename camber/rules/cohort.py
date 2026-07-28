"""Cohort-deviation FDD rule — "this unit runs unlike its peers".

A fleet rule (one batch of like equipment in, one aggregate Finding out) built on
:func:`camber.charts.cohort.cohort_deviation`: it flags units whose behavior on a chosen role
(summarized as mean / peak / load factor) deviates beyond ``k`` robust-σ from the cohort norm.
Enables the deviation-from-peers signal that per-unit rules can't see — a VAV within every
absolute bound can still run unlike its 40 siblings.
"""

from __future__ import annotations

from ..charts.cohort import cohort_deviation
from .base import Finding


class CohortDeviation:
    """Flag units deviating > ``k`` robust-σ from their cohort on ``role`` (a FleetRule)."""

    roles_optional = ()

    def __init__(
        self,
        role,
        *,
        k: float = 3.5,
        min_cohort: int = 3,
        summary: str = "mean",
        name: str | None = None,
    ):
        self.role = role
        self.k = k
        self.min_cohort = min_cohort
        self.summary = summary
        self.name = name or f"cohort_deviation_{getattr(role, 'name', str(role))}".lower()
        self.roles_required = (role,)

    def analyze_fleet(self, frames: dict) -> Finding:
        """Run across the cohort's role-frames; return one aggregate Finding."""
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
