"""Fetch the Building Data Genome 2 files this example uses (not bundled).

Dataset: Building Data Genome Project 2 (CC-BY-SA 4.0) — 3,053 whole-building meters from
1,636 buildings, hourly, 2016-2017. https://github.com/buds-lab/building-data-genome-project-2
(Miller et al., Scientific Data, 2020.)

The repo stores data via Git LFS; this pulls the actual CSVs from the LFS media
endpoint into examples/_data/bdg2/ (git-ignored). Re-run is a no-op if present.
"""

from __future__ import annotations

import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "bdg2")
BASE = (
    "https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/master/data/"
)
# The publisher's CLEANED meters -- the files the dataset catalog ingests. The raw export carries
# ~24,700 all-zero building-days per meter type in 2016 (meter outages) that the benchmark would
# otherwise fit as real consumption.
FILES = {
    "metadata.csv": "metadata/metadata.csv",
    "weather.csv": "weather/weather.csv",
    "cleaned/electricity_cleaned.csv": "meters/cleaned/electricity_cleaned.csv",
    "cleaned/chilledwater_cleaned.csv": "meters/cleaned/chilledwater_cleaned.csv",
}


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    for name, path in FILES.items():
        dest = os.path.join(DATA, *name.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            print(f"  {name} present")
            continue
        print(f"Downloading {name} ...")
        urllib.request.urlretrieve(BASE + path, dest)
    print(f"Done. Files in {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
