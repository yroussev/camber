"""Report: audit and reporting outputs."""

from .audit import AuditReport, Benchmark, ECM
from .dashboard import build_dashboard, fig_to_base64
from .fleet import BuildingSummary, FleetReport, build_fleet_report

__all__ = ["AuditReport", "Benchmark", "ECM",
           "FleetReport", "BuildingSummary", "build_fleet_report",
           "build_dashboard", "fig_to_base64"]
