"""Write every vector CSV (inputs and CAMBER's predicted series) as Parquet, with the same columns.

For engines that read Parquet natively (DataFusion, DuckDB, Spark, ...). The CSVs stay the source
of truth; this only re-types them. Needs pandas and pyarrow, no CAMBER::

    python export_parquet.py OUT_DIR

Column types: ``date`` / ``start`` / ``end`` are ``date32`` (local calendar days, no time zone);
``period`` is a string; ``estimated`` is a boolean; ``days`` / ``days_observed`` are ``int32``;
every other column is ``float64``. The layout under ``OUT_DIR`` mirrors this folder.
"""

from __future__ import annotations

import argparse
import glob
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATES = ("date", "start", "end")
INTS = ("days", "days_observed")


def typed(df: pd.DataFrame) -> pd.DataFrame:
    """The CSV's columns with explicit types (see the module docstring)."""
    out = {}
    for c in df.columns:
        if c in DATES:
            out[c] = pd.to_datetime(df[c], format="%Y-%m-%d").dt.date
        elif c == "period":
            out[c] = df[c].astype(str)
        elif c == "estimated":
            out[c] = df[c].astype(str).str.lower().isin(["true", "1", "yes", "e"])
        elif c in INTS:
            out[c] = df[c].astype("int32")
        else:
            out[c] = df[c].astype("float64")
    return pd.DataFrame(out)


def export(out_dir: str, root: str = HERE) -> list:
    """Convert every CSV under ``inputs/`` and ``predictions/``; returns the written paths."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    written = []
    for path in sorted(glob.glob(os.path.join(root, "inputs", "**", "*.csv"), recursive=True)) + (
        sorted(glob.glob(os.path.join(root, "predictions", "*.csv")))
    ):
        rel = os.path.relpath(path, root)
        dst = os.path.join(out_dir, os.path.splitext(rel)[0] + ".parquet")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        table = pa.Table.from_pandas(typed(pd.read_csv(path)), preserve_index=False)
        pq.write_table(table, dst)
        written.append(dst)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir", help="where to write the Parquet files")
    a = ap.parse_args(argv)
    n = len(export(a.out_dir))
    print(f"wrote {n} Parquet files under {a.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
