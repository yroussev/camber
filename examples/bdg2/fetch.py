"""Fetch the Building Data Genome 2 files this example uses (not bundled).

Dataset: Building Data Genome Project 2 (CC-BY) — 3,053 whole-building meters from
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
BASE = ("https://media.githubusercontent.com/media/buds-lab/"
        "building-data-genome-project-2/master/data/")
FILES = {
    "metadata.csv": "metadata/metadata.csv",
    "weather.csv": "weather/weather.csv",
    "electricity.csv": "meters/raw/electricity.csv",
    "chilledwater.csv": "meters/raw/chilledwater.csv",
}


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    for name, path in FILES.items():
        dest = os.path.join(DATA, name)
        if os.path.exists(dest):
            print(f"  {name} present")
            continue
        print(f"Downloading {name} ...")
        urllib.request.urlretrieve(BASE + path, dest)
    print(f"Done. Files in {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
