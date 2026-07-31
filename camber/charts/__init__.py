"""Charts: diagnostic visuals — heating-vs-cooling scatter, reheat boxes, timeseries, zones,
load carpet (hour x date heatmap), CUSUM trajectory, and the energy-signature change-point plot.

``__all__`` is the curated package-level API (every chart's figure-producing entry point plus
its result types); the per-chart submodules (``camber.charts.carpet`` etc.) remain importable
directly. Importing this package pulls matplotlib, a core dependency.
"""

from .box_reheat import box_reheat_figure
from .carpet import carpet_matrix, load_carpet
from .cohort import (
    CohortResult,
    cohort_deviation,
    cohort_small_multiples,
    cohort_summary,
)
from .cusum_chart import cusum_plot
from .diagnostic import (
    DiagnosticTemplate,
    band,
    diagnostic_scatter,
    economizer_template,
    no_simultaneous_template,
    reset_line,
)
from .energy_signature import energy_signature
from .evidence import Evidence, evidence_descriptor, finding_evidence, render_evidence
from .loadprofile_chart import load_duration_chart, load_profile_chart
from .multitrend import fault_multitrend, mask_to_spans
from .oat_scatter import CloudShape, brush_back, classify_shape, oat_scatter
from .quality_dashboard import quality_dashboard, quality_matrix
from .readiness import presence_matrix, readiness_ribbon
from .savings import cumulative_savings, savings_chart
from .scatter import HeCMetrics, ahu_hec_scatter, hec_metrics
from .timeseries import ahu_hec_timeseries
from .zones_chart import zones_timeofweek_figure, zones_vs_oat_figure

__all__ = [
    "box_reheat_figure",
    "load_carpet",
    "carpet_matrix",
    "cohort_deviation",
    "cohort_small_multiples",
    "cohort_summary",
    "CohortResult",
    "cusum_plot",
    "diagnostic_scatter",
    "DiagnosticTemplate",
    "band",
    "reset_line",
    "economizer_template",
    "no_simultaneous_template",
    "energy_signature",
    "render_evidence",
    "finding_evidence",
    "evidence_descriptor",
    "Evidence",
    "load_profile_chart",
    "load_duration_chart",
    "fault_multitrend",
    "mask_to_spans",
    "oat_scatter",
    "classify_shape",
    "brush_back",
    "CloudShape",
    "quality_dashboard",
    "quality_matrix",
    "readiness_ribbon",
    "presence_matrix",
    "savings_chart",
    "cumulative_savings",
    "ahu_hec_scatter",
    "hec_metrics",
    "HeCMetrics",
    "ahu_hec_timeseries",
    "zones_timeofweek_figure",
    "zones_vs_oat_figure",
]
