"""Measurement & verification: change-point models, TOWT, statistics, weather, resampling."""

from ._caltrack import CalTRACKResult
from .caltrack import (
    DailyNMECResult,
    NMECResult,
    TOWTHourlyResult,
    caltrack_savings,
    daily_changepoint_savings,
    towt_savings_hourly,
)
from .caltrack_daily import (
    CalTRACKBillingModel,
    CalTRACKDailyModel,
    caltrack_billing_savings,
    caltrack_daily_savings,
    fit_caltrack_billing,
    fit_caltrack_daily,
)
from .caltrack_hourly import CalTRACKHourlyModel, caltrack_hourly_savings, fit_caltrack_hourly
from .nonroutine import (
    NonRoutineResult,
    StepChangeResult,
    StepChangesResult,
    detect_non_routine,
    detect_step_change,
    detect_step_changes,
)
from .normalized import NormalizedSavings, normalized_annual_consumption, normalized_savings
from .rc_model import (
    Calibration,
    OptionDSavings,
    RCModel,
    calibrate,
    daily_schedule,
    option_d_savings,
)
from .retrofit_isolation import (
    DriverModel,
    IsolationSavings,
    fit_driver_model,
    isolation_normalized_savings,
    isolation_savings,
)

__all__ = [
    "caltrack_daily_savings",
    "caltrack_billing_savings",
    "caltrack_hourly_savings",
    "fit_caltrack_daily",
    "fit_caltrack_billing",
    "fit_caltrack_hourly",
    "CalTRACKDailyModel",
    "CalTRACKBillingModel",
    "CalTRACKHourlyModel",
    "CalTRACKResult",
    "daily_changepoint_savings",
    "DailyNMECResult",
    "towt_savings_hourly",
    "TOWTHourlyResult",
    "caltrack_savings",  # deprecated (0.105): daily_changepoint_savings
    "NMECResult",  # deprecated (0.105): DailyNMECResult
    "detect_non_routine",
    "NonRoutineResult",
    "detect_step_change",
    "StepChangeResult",
    "detect_step_changes",
    "StepChangesResult",
    "normalized_annual_consumption",
    "normalized_savings",
    "NormalizedSavings",
    "fit_driver_model",
    "DriverModel",
    "isolation_savings",
    "IsolationSavings",
    "isolation_normalized_savings",
    "RCModel",
    "Calibration",
    "OptionDSavings",
    "calibrate",
    "daily_schedule",
    "option_d_savings",
]
