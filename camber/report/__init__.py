"""Report: audit and reporting outputs."""

from .ahu import ahu_diagnosis_table
from .audit import ECM, AuditReport, Benchmark
from .chiller import chiller_diagnosis_table
from .condenser import condenser_diagnosis_table
from .dashboard import build_dashboard, fig_to_base64
from .evaporator import evaporator_diagnosis_table
from .fleet import BuildingSummary, FleetReport, build_fleet_report
from .linking import (
    carpet_svg_html,
    interactive_scatter_html,
    multitrend_svg_html,
    selection_bus_html,
)
from .pump import pump_diagnosis_table
from .site import build_site_report
from .vav import vav_diagnosis_table

__all__ = [
    "AuditReport",
    "Benchmark",
    "ECM",
    "FleetReport",
    "BuildingSummary",
    "build_fleet_report",
    "build_dashboard",
    "build_site_report",
    "chiller_diagnosis_table",
    "condenser_diagnosis_table",
    "evaporator_diagnosis_table",
    "pump_diagnosis_table",
    "ahu_diagnosis_table",
    "vav_diagnosis_table",
    "fig_to_base64",
    "carpet_svg_html",
    "multitrend_svg_html",
    "interactive_scatter_html",
    "selection_bus_html",
]
