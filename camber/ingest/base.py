"""Source adapter interface: normalize any BAS data source to one shape.

Every input — a folder of per-point CSVs, one wide CSV, a Haystack ``hisRead``
response, a historian query — reduces to the same thing: named point time-series
on a common time grid. A :class:`SourceAdapter` hides where the data came from so
the model/resolve/rules layers above it never branch on source type.

A point's name is its raw ``<equip>_<measure>`` token as the source labels it
(e.g. ``VAV_117_HWValve``); turning the measure half into a vendor-neutral
:class:`~camber.model.roles.Role` is the mapping layer's job, not the adapter's.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class SourceAdapter(Protocol):
    """Read named point series from one data source onto a common time grid."""

    def point_names(self) -> list[str]:
        """All point names available from this source (``<equip>_<measure>``)."""
        ...

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        """Load the named points into a DataFrame (one column per name).

        Index is a sorted DatetimeIndex; missing names are omitted. ``resample``
        is a pandas offset alias (mean-aggregated) or None for native interval.
        """
        ...

    def units(self) -> dict:
        """Map of point name -> engineering unit string (best-effort; may be empty)."""
        ...
