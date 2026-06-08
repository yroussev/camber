"""Heating-vs-cooling diagnostic (scatter + metrics).

Implements ``spec-ahu-hec-scatter.md``: X = heating-coil valve %, Y =
cooling-coil valve %, one marker per interval. Upper-right (both > deadband) =
simultaneous heating and cooling (the fault). The headline metric is the % of
(occupied) intervals in that region -- the reheat-penalty quantifier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..points import find_column


@dataclass
class HeCMetrics:
    """Heating-vs-cooling overlap metrics for one AHU (reheat-penalty quantifier)."""

    ahu_id: int
    n_intervals: int
    n_considered: int          # after occupied_only filter
    simultaneous_pct: float    # % with HeC>thr AND CC>thr
    simultaneous_pct_oat_gt_65: float
    median_overlap: float      # median min(HeC,CC) during overlap intervals
    mean_overlap: float

    def as_dict(self):
        """Return as a plain dict."""
        return asdict(self)


def _resolve(df, ahu_id, oat_col):
    hec = find_column(df.columns, "AHU_HeC", ahu_id)
    cc = find_column(df.columns, "AHU_CC", ahu_id)
    if hec is None or cc is None:
        raise KeyError(f"AHU{ahu_id}: need both _HeC and _CC columns; found {hec}, {cc}")
    if oat_col is None:
        oat_col = find_column(df.columns, "AHU_OAT", ahu_id) or _first_oat(df.columns)
    return hec, cc, oat_col


def _first_oat(cols):
    for c in cols:
        if c.startswith("Bldg") and c.endswith("TempOa"):
            return c
    return None


def _occ_mask(df, ahu_id):
    occ = find_column(df.columns, "AHU_Occ", ahu_id)
    if occ is None:
        return None
    return df[occ].astype(float) > 0.5


def hec_metrics(df, ahu_id, *, threshold=5.0, occupied_only=False, oat_col=None,
                cooling_cutoff_f=65.0) -> HeCMetrics:
    """Compute the simultaneous-heating/cooling metrics for one AHU."""
    hec, cc, oat_col = _resolve(df, ahu_id, oat_col)
    sub = df[[hec, cc] + ([oat_col] if oat_col else [])].dropna()
    n_all = len(sub)
    if occupied_only:
        mask = _occ_mask(df, ahu_id)
        if mask is not None:
            sub = sub[mask.reindex(sub.index, fill_value=False)]
    n = len(sub)

    overlap = (sub[hec] > threshold) & (sub[cc] > threshold)
    n_overlap = int(overlap.sum())
    simult_pct = 100.0 * n_overlap / n if n else 0.0

    if oat_col:
        hot_overlap = overlap & (sub[oat_col] > cooling_cutoff_f)
        simult_hot = 100.0 * int(hot_overlap.sum()) / n if n else 0.0
    else:
        simult_hot = 0.0

    if n_overlap:
        mins = sub.loc[overlap, [hec, cc]].min(axis=1)
        median_overlap = float(mins.median())
        mean_overlap = float(mins.mean())
    else:
        median_overlap = mean_overlap = 0.0

    return HeCMetrics(
        ahu_id=ahu_id,
        n_intervals=n_all,
        n_considered=n,
        simultaneous_pct=round(simult_pct, 2),
        simultaneous_pct_oat_gt_65=round(simult_hot, 2),
        median_overlap=round(median_overlap, 2),
        mean_overlap=round(mean_overlap, 2),
    )


def ahu_hec_scatter(df, ahu_id, *, threshold=5.0, occupied_only=False,
                    color_by="oat", oat_col=None, ax=None):
    """Draw the heating-vs-cooling scatter. Returns (ax, HeCMetrics)."""
    import matplotlib.pyplot as plt

    hec, cc, oat_col = _resolve(df, ahu_id, oat_col)
    cols = [hec, cc] + ([oat_col] if oat_col else [])
    sub = df[cols].dropna()
    if occupied_only:
        mask = _occ_mask(df, ahu_id)
        if mask is not None:
            sub = sub[mask.reindex(sub.index, fill_value=False)]

    if ax is None:
        _, ax = plt.subplots(figsize=(6, 6))

    if color_by == "oat" and oat_col:
        sc = ax.scatter(sub[hec], sub[cc], c=sub[oat_col], cmap="coolwarm",
                        s=12, alpha=0.7)
        ax.figure.colorbar(sc, ax=ax, label="OAT (°F)")
    else:
        ax.scatter(sub[hec], sub[cc], s=12, alpha=0.6, color="#3366cc")

    # quadrant deadband lines + simultaneous-H/C region shading
    ax.axvline(threshold, color="grey", lw=0.7, ls="--")
    ax.axhline(threshold, color="grey", lw=0.7, ls="--")
    ax.axhspan(threshold, 100, xmin=threshold / 100, xmax=1.0,
               color="red", alpha=0.06)

    m = hec_metrics(df, ahu_id, threshold=threshold, occupied_only=occupied_only,
                    oat_col=oat_col)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_aspect("equal")
    ax.set_xlabel("Heating-coil valve (% open)")
    ax.set_ylabel("Cooling-coil valve (% open)")
    ax.set_title(f"AHU{ahu_id} simultaneous heating/cooling\n"
                 f"{m.simultaneous_pct:.1f}% of intervals in fault region "
                 f"({m.simultaneous_pct_oat_gt_65:.1f}% at OAT>65°F)")
    ax.text(0.97, 0.97, "simultaneous\nH + C", transform=ax.transAxes,
            ha="right", va="top", color="red", fontsize=9, alpha=0.8)
    return ax, m
