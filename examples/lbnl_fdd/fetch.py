"""Fetch the LBNL FDD CSVs these examples use (not bundled).

Dataset: LBNL Fault Detection and Diagnostics Datasets (CC-BY) — simulated HVAC
operational data with labeled faults at multiple severities plus a fault-free
baseline. https://www.osti.gov/dataexplorer/biblio/dataset/1881324

Default: downloads the single-duct-AHU (SDAHU) zip (~580 MB) to examples/_data/lbnl/
and extracts only the CSVs run_fdd.py + benchmark.py need (baseline, a leakage fault,
four stuck-damper severities). Re-run is a no-op if the CSVs are already present.

With ``--families``: also fetches the fan-coil-unit (FCU) and dual-duct-AHU (DDAHU)
scenarios so benchmark.py can score the detector suite ACROSS equipment families.
``--fpu`` adds the VAV fan-power-unit subset (VAV zone-terminal drift) and ``--chiller``
the chiller-plant subset (the plant-level chiller / cooling-tower detectors). These are
large (FCU ~0.5 GB, DDAHU ~1.7 GB, FPU ~1 GB, chiller ~0.8 GB zipped); skip unless you
want that benchmark. The _data/ dir is git-ignored.
"""

from __future__ import annotations

import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "lbnl")
ZIP_URL = (
    "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_SDAHU/LBNL_FDD_Data_Sets_SDAHU.zip"
)
TTL_ZIP_URL = (
    "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_SDAHU/"
    "LBNL_FDD_Data_Sets_SDAHU_ttl.zip"
)
MEMBERS = [
    "LBNL_FDD_Dataset_SDAHU/AHU_annual.csv",
    # cooling-coil-valve leakage severity sweep (benchmark.py scores leaking_valve across
    # severities)
    "LBNL_FDD_Dataset_SDAHU/coi_leakage_010_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/coi_leakage_025_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/coi_leakage_050_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/coi_leakage_100_annual.csv",
    # stuck-damper severities for the FDD-accuracy benchmark (benchmark.py)
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_010_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_025_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_075_annual.csv",
    "LBNL_FDD_Dataset_SDAHU/damper_stuck_100_annual_short.csv",
]
# The proven-present core (baseline + one leak + the four dampers); the extra leak severities above
# are optional (may be absent in some zip releases) and don't gate the "already fetched" no-op.
REQUIRED = [
    m
    for m in MEMBERS
    if "coi_leakage_010" not in m and "coi_leakage_025" not in m and "coi_leakage_100" not in m
]

# Extra equipment families for the cross-equipment benchmark (opt-in via --families).
# (subdir, zip url, ~size note, [zip members to extract])
FAMILY_SETS = [
    (
        "fcu",
        "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_FCU/LBNL_FDD_Data_Sets_FCU.zip",
        "~0.5 GB",
        [
            "LBNL_FDD_Dataset_FCU/FCU_FaultFree.csv",
            "LBNL_FDD_Dataset_FCU/FCU_OADMPRStuck_0.csv",
            "LBNL_FDD_Dataset_FCU/FCU_OADMPRStuck_100.csv",
            "LBNL_FDD_Dataset_FCU/FCU_OADMPRLeak_50.csv",
        ],
    ),
    (
        "ddahu",
        "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_DDAHU/"
        "LBNL_FDD_Data_Sets_DDAHU.zip",
        "~1.7 GB",
        [
            "LBNL_FDD_Dataset_DDAHU/DualDuct_FaultFree.csv",
            "LBNL_FDD_Dataset_DDAHU/DualDuct_DMPRStuck_OA_0.csv",
            "LBNL_FDD_Dataset_DDAHU/DualDuct_DMPRStuck_OA_100.csv",
        ],
    ),
]


# Fan-power VAV terminal-unit set (opt-in via --fpu) for the VAV zone-terminal DRIFT benchmark.
# Faults are imposed in the WEST zone; the parallel-FPU (PFPU) CSVs are extracted by basename (the
# archive's internal folder prefix isn't assumed). Members cover the fault-free baseline plus a
# spread of the two detectors' target faults (damper/airflow-sensor for airflow drift; reheat-valve
# leak/stuck/coil-fouling for reheat-valve drift).
FPU_URL = "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_FPU/LBNL_FDD_Data_Sets_FPU.zip"
FPU_MEMBERS = [
    "PFPU_FaultFree.csv",
    "PFPU_VAVDMPRStuck_50%.csv",
    "PFPU_VAVDMPRStuck_100%.csv",
    "PFPU_SensorBias_VAVAirflow_-400CFM.csv",
    "PFPU_SensorBias_VAVAirflow_+400CFM.csv",
    "PFPU_ReheatVLVLeak_50%MaxFlow.csv",
    "PFPU_ReheatVLVLeak_80%MaxFlow.csv",
    "PFPU_ReheatVLVStuck_0%.csv",
    "PFPU_ReheatVLVStuck_100%.csv",
    "PFPU_ReheatCoilFouling_Waterside_Severe.csv",
]
FPU_REQUIRED = ["PFPU_FaultFree.csv", "PFPU_VAVDMPRStuck_100%.csv"]


# Chiller-plant set (opt-in via --chiller) for the PLANT-LEVEL detector benchmark. The plant data
# has no refrigerant-side points, so only chiller_efficiency (kW/ton) and cooling_tower_approach are
# runnable; members are the fault-free baseline plus the physical heat-rejection faults those two
# detectors target (cooling-tower fouling + PID, three-way-bypass leak/stuck) and a sensor-bias run
# (a genuine negative for a physical detector). Extracted by basename (archive prefix not assumed).
CHILLER_URL = (
    "https://fdddata.lbl.gov/data/Simulated_LBNL_FDD_Data_Sets_Chiller_Plant/"
    "LBNL_FDD_Data_Sets_Chiller_Plant.zip"
)
CHILLER_MEMBERS = [
    "ChillerPlant.csv",  # fault-free baseline (calibration + a negative)
    "ChillerPlant_coolingtower_fouling_065.csv",
    "ChillerPlant_coolingtower_fouling_080.csv",
    "ChillerPlant_coolingtower_fouling_095.csv",
    "ChillerPlant_coolingtower_PI.csv",
    "ChillerPlant_bypass_leakage_025.csv",
    "ChillerPlant_bypass_leakage_050.csv",
    "ChillerPlant_bypass_leakage_075.csv",
    "ChillerPlant_bypass_stuck_050.csv",
    "ChillerPlant_bypass_stuck_075.csv",
    "ChillerPlant_chiller_bias_2.csv",  # CHW-temp sensor bias — physical-detector negative
]
CHILLER_REQUIRED = ["ChillerPlant.csv", "ChillerPlant_coolingtower_fouling_095.csv"]


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
                with z.open(m) as src, open(os.path.join(ttl_dir, os.path.basename(m)), "wb") as f:
                    f.write(src.read())


def _fetch_set(subdir, url, size, members, zip_name, required=None, match_basename=False):
    """Download ``url`` (if absent) and extract ``members`` into DATA/subdir.

    ``required`` (default = all members) is the subset whose local presence means "already fetched".
    Optional members (severity variants that may not exist in every zip release) extract when
    present but never gate the no-op, so a broadened catalog can't cause an endless re-download.
    ``match_basename`` matches each target by file *basename* against the zip members (robust to an
    unknown internal folder prefix), used where the archive layout isn't known ahead of time.
    """
    out = os.path.join(DATA, subdir)
    os.makedirs(out, exist_ok=True)
    needed = [os.path.basename(m) for m in (required if required is not None else members)]
    if all(os.path.exists(os.path.join(out, n)) for n in needed):
        print(f"LBNL {subdir.upper()} CSVs already present; nothing to do.")
        return
    zpath = os.path.join(DATA, zip_name)
    if not os.path.exists(zpath):
        print(f"Downloading {url}\n  ({size}; this takes a while) ...")
        urllib.request.urlretrieve(url, zpath)
    print(f"Extracting the {subdir.upper()} CSVs ...")
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        by_base = {os.path.basename(n): n for n in names}
        for m in members:
            member = (
                by_base.get(os.path.basename(m)) if match_basename else (m if m in names else None)
            )
            if member is None:
                # a labeled fault CSV not present in this release of the zip — skip, don't crash.
                # benchmark.py already guards each scenario with os.path.exists, so a missing member
                # simply isn't scored. Keeps the fetch robust as the fault catalog is broadened.
                print(f"  (skipped, not in zip: {os.path.basename(m)})")
                continue
            dest = os.path.join(out, os.path.basename(m))
            with z.open(member) as src, open(dest, "wb") as f:
                f.write(src.read())
            print(f"  {os.path.basename(m)}")
    print(f"Done. CSVs in {out}\n(You may delete {zpath} to reclaim disk.)")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _fetch_ttl()
    _fetch_set("sdahu", ZIP_URL, "~580 MB", MEMBERS, "LBNL_SDAHU.zip", required=REQUIRED)
    if "--families" in argv:
        for subdir, url, size, members in FAMILY_SETS:
            _fetch_set(subdir, url, size, members, f"LBNL_{subdir.upper()}.zip")
    if "--fpu" in argv:
        _fetch_set(
            "fpu",
            FPU_URL,
            "~1 GB",
            FPU_MEMBERS,
            "LBNL_FPU.zip",
            required=FPU_REQUIRED,
            match_basename=True,
        )
    if "--chiller" in argv:
        _fetch_set(
            "chiller",
            CHILLER_URL,
            "~0.8 GB",
            CHILLER_MEMBERS,
            "LBNL_Chiller_Plant.zip",
            required=CHILLER_REQUIRED,
            match_basename=True,
        )
    if not any(f in argv for f in ("--families", "--fpu", "--chiller")):
        print(
            "\n(Add --families to fetch FCU + DDAHU for the cross-equipment benchmark, "
            "--fpu for the VAV fan-power-unit drift benchmark, or --chiller for the "
            "chiller-plant plant-level benchmark.)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
