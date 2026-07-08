"""Report: audit and reporting outputs."""

from .audit import AuditReport, Benchmark, ECM
from .dashboard import build_dashboard, fig_to_base64
from .fleet import BuildingSummary, FleetReport, build_fleet_report
from .site import build_site_report

__all__ = ["AuditReport", "Benchmark", "ECM",
           "FleetReport", "BuildingSummary", "build_fleet_report",
           "build_dashboard", "build_site_report", "fig_to_base64"]
