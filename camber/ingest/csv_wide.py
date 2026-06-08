"""Wide CSV adapter: one file, a timestamp column + one column per point.

Common from spreadsheet exports and data historians. Reuses
:func:`camber.io.load_csv` for timestamp parsing and resampling.
"""

from __future__ import annotations

import pandas as pd

from .. import io as _io


class WideCsvAdapter:
    """SourceAdapter over a single wide CSV (timestamp + many point columns)."""

    def __init__(self, path: str, timestamp_col: str | None = None):
        self.path = path
        self.timestamp_col = timestamp_col
        self._frame: pd.DataFrame | None = None

    def _load_all(self, resample: str | None) -> pd.DataFrame:
        return _io.load_csv(self.path, timestamp_col=self.timestamp_col,
                            resample=resample)

    def point_names(self):
        """Column names of the wide CSV (one per point)."""
        if self._frame is None:
            self._frame = self._load_all(resample=None)
        return list(self._frame.columns)

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        """Load the named columns into a frame, optionally resampled."""
        df = self._load_all(resample=resample)
        keep = [c for c in names if c in df.columns]
        return df[keep] if keep else pd.DataFrame(index=df.index)

    def units(self) -> dict:
        """Per-point units; empty for wide CSVs, which rarely carry them."""
        # wide CSVs rarely carry per-column units; left empty by design
        return {}
