"""Command-line entry point for the BAS re-tuning / FDD diagnostics.

Examples
--------
    # Run the AHU_HeC diagnostic on a real trend CSV for AHU 1:
    python -m camber.cli --csv trends.csv --ahu 1 --out out/

    # Demo on built-in synthetic data exhibiting the reheat fault:
    python -m camber.cli --demo reheat --ahu 1 --out out/
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _equip_ids(df, prefix="AHU"):
    from .points import count_equipment
    return list(range(1, count_equipment(df.columns, prefix) + 1))


def main(argv=None):
    """CLI entry point: parse args and run the requested diagnostics."""
    ap = argparse.ArgumentParser(description="BAS re-tuning / FDD diagnostics")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="trend-log CSV path")
    src.add_argument("--demo", choices=["none", "reheat"],
                     help="use built-in synthetic data")
    ap.add_argument("--ahu", type=int, default=None,
                    help="AHU id (default: all found)")
    ap.add_argument("--timestamp-col", default=None)
    ap.add_argument("--resample", default=None, help='e.g. "15min", "1h"')
    ap.add_argument("--occupied-only", action="store_true")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="deadband %% above which a valve counts as open")
    ap.add_argument("--out", default="out", help="output directory for PNGs/JSON")
    args = ap.parse_args(argv)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .charts.scatter import ahu_hec_scatter
    from .charts.timeseries import ahu_hec_timeseries

    if args.demo:
        from .synth import make_ahu_trends
        df = make_ahu_trends(fault=args.demo, ahu_id=args.ahu or 1)
    else:
        from .io import load_csv
        df = load_csv(args.csv, timestamp_col=args.timestamp_col,
                      resample=args.resample)

    ids = [args.ahu] if args.ahu else _equip_ids(df)
    if not ids:
        print("No AHU equipment found (need columns like AHU1_HeC, AHU1_CC).",
              file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    summary = []
    for i in ids:
        ax, m = ahu_hec_scatter(df, i, threshold=args.threshold,
                                occupied_only=args.occupied_only)
        ax.figure.tight_layout()
        ax.figure.savefig(os.path.join(args.out, f"AHU{i}_HeC_scatter.png"), dpi=120)
        plt.close(ax.figure)

        ax2 = ahu_hec_timeseries(df, i, threshold=args.threshold)
        ax2.figure.tight_layout()
        ax2.figure.savefig(os.path.join(args.out, f"AHU{i}_HeC_timeseries.png"), dpi=120)
        plt.close(ax2.figure)

        summary.append(m.as_dict())
        print(f"AHU{i}: simultaneous H/C = {m.simultaneous_pct:.1f}% "
              f"({m.simultaneous_pct_oat_gt_65:.1f}% at OAT>65°F), "
              f"n={m.n_considered}")

    with open(os.path.join(args.out, "hec_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {len(ids)*2} charts + hec_summary.json to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
