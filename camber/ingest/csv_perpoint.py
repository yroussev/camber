"""Per-point CSV adapter: one file per point, ``<name>.csv`` in a folder.

The common BAS "trend export" shape. Each file has a timestamp column and a value
column (header often ``Timestamp,Value (<unit>)``). Reuses the timestamp/value
parsing in :mod:`camber.realio`.
"""

from __future__ import annotations

import os
import re
from glob import glob

import pandas as pd

from .. import realio


class PerPointCsvAdapter:
    """SourceAdapter over a folder of one-CSV-per-point trend files."""

    def __init__(self, folder: str):
        self.folder = folder

    def point_names(self):
        """Sorted point names (one per ``<name>.csv`` file in the folder)."""
        return sorted(os.path.basename(p)[:-4]
                      for p in glob(os.path.join(self.folder, "*.csv")))

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        """Load the named points into a wide, optionally resampled frame."""
        cols = {}
        for name in names:
            path = os.path.join(self.folder, f"{name}.csv")
            if os.path.exists(path):
                cols[name] = realio.load_point(path, name=name)
        if not cols:
            return pd.DataFrame()
        df = pd.concat(cols, axis=1)
        if resample:
            df = df.resample(resample).mean(numeric_only=True)
        return df

    def units(self) -> dict:
        """Map point name -> unit parsed from each file's value-column header."""
        out = {}
        unit_re = re.compile(r"\(([^)]*)\)")
        for p in glob(os.path.join(self.folder, "*.csv")):
            try:
                with open(p, encoding="utf-8-sig", errors="replace") as f:
                    m = unit_re.search(f.readline())
                if m:
                    out[os.path.basename(p)[:-4]] = m.group(1).strip()
            except OSError:
                pass
        return out
