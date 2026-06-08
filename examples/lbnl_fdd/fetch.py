"""Fetch the LBNL FDD CSVs these examples use (not bundled).

Dataset: LBNL Fault Detection and Diagnostics Datasets (CC-BY) — simulated HVAC
operational data with labeled faults at multiple severities plus a fault-free
baseline. https://www.osti.gov/dataexplorer/biblio/dataset/1881324

Default: downloads the single-duct-AHU (SDAHU) zip (~580 MB) to examples/_data/lbnl/
and extracts only the CSVs run_fdd.py + benchmark.py need (baseline, a leakage fault,
four stuck-damper severities). Re-run is a no-op if the CSVs are already present.

With ``--families``: also fetches the fan-coil-unit (FCU) and dual-duct-AHU (DDAHU)
scenarios so benchmark.py can score the detector suite ACROSS equipment families.
These are large (FCU ~0.5 GB, DDAHU ~1.7 GB zipped); skip unless you want the full
cross-equipment benchmark. The _data/ dir is git-ignored.
"""

from __future__ import annotations

import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "lbnl")
ZIP_URL = ("https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_SDAHU/"
           "LBNL_FDD_Data_Sets_SDAHU.zip")
TTL_ZIP_URL = ("https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_SDAHU/"
               "LBNL_FDD_Data_Sets_SDAHU_ttl.zip")
MEMBERS = [
    "LBNL_FDD_Dataset_SDAHU/AHU_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/coi_leakage_050_annual.csv",
    # stuck-damper severities for the FDD-accuracy benchmark (benchmark.py)
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_010_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_025_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_075_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_100_annual_short.csv",
]

# Extra equipment families for the cross-equipment benchmark (opt-in via --families).
# (subdir, zip url, ~size note, [zip members to extract])
FAMILY_SETS = [
    ("fcu",
     "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_FCU/"
     "LBNL_FDD_Data_Sets_FCU.zip", "~0.5 GB",
     ["LBNL_FDD_Dataset_FCU/FCU_FaultFree.csv",
      "LBNL_FDD_Dataset_FCU/FCU_OADMPRStuck_0.csv",
      "LBNL_FDD_Dataset_FCU/FCU_OADMPRStuck_100.csv",
      "LBNL_FDD_Dataset_FCU/FCU_OADMPRLeak_50.csv"]),
    ("ddahu",
     "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_DDAHU/"
     "LBNL_FDD_Data_Sets_DDAHU.zip", "~1.7 GB",
     ["LBNL_FDD_Dataset_DDAHU/DualDuct_FaultFree.csv",
      "LBNL_FDD_Dataset_DDAHU/DualDuct_DMPRStuck_OA_0.csv",
      "LBNL_FDD_Dataset_DDAHU/DualDuct_DMPRStuck_OA_100.csv"]),
]


def _fetch_ttl():
    """Fetch the small Brick (.ttl) model used by the Brick-interop example."""
    ttl_dir = os.path.join(DATA, "ttl")
    os.makedirs(ttl_dir, exist_ok=True)
    if any(f.endswith(".ttl") for f in os.listdir(ttl_dir)):
        return
    tz = os.path.join(DATA, "sdahu_ttl.zip")
    print(f"Downloading Brick model {TTL_ZIP_URL} ...")
    urllib.request.urlretrieve(TTL_ZIP_URL, tz)
    with zipfile.ZipFile(tz) as z:
        for m in z.namelist():
            if m.endswith(".ttl"):
                with z.open(m) as src, open(os.path.join(ttl_dir,
                                                         os.path.basename(m)), "wb") as f:
                    f.write(src.read())


def _fetch_set(subdir, url, size, members, zip_name):
    """Download ``url`` (if absent) and extract ``members`` into DATA/subdir."""
    out = os.path.join(DATA, subdir)
    os.makedirs(out, exist_ok=True)
    needed = [os.path.basename(m) for m in members]
    if all(os.path.exists(os.path.join(out, n)) for n in needed):
        print(f"LBNL {subdir.upper()} CSVs already present; nothing to do.")
        return
    zpath = os.path.join(DATA, zip_name)
    if not os.path.exists(zpath):
        print(f"Downloading {url}\n  ({size}; this takes a while) ...")
        urllib.request.urlretrieve(url, zpath)
    print(f"Extracting the {subdir.upper()} CSVs ...")
    with zipfile.ZipFile(zpath) as z:
        for m in members:
            dest = os.path.join(out, os.path.basename(m))
            with z.open(m) as src, open(dest, "wb") as f:
                f.write(src.read())
            print(f"  {os.path.basename(m)}")
    print(f"Done. CSVs in {out}\n(You may delete {zpath} to reclaim disk.)")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _fetch_ttl()
    _fetch_set("sdahu", ZIP_URL, "~580 MB", MEMBERS, "LBNL_SDAHU.zip")
    if "--families" in argv:
        for subdir, url, size, members in FAMILY_SETS:
            _fetch_set(subdir, url, size, members, f"LBNL_{subdir.upper()}.zip")
    else:
        print("\n(Add --families to also fetch FCU + DDAHU for the full"
              " cross-equipment benchmark.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
