"""Rebuild the BDG2 inputs of the shared M&V vectors locally, from the publisher's own files.

CAMBER redistributes no datasets, so the Building Data Genome 2 aggregates these vectors use are
not in the repository. This script downloads the exact source files from the publisher, checks
them against the sha256 pins of CAMBER's ``bdg2`` catalog entry, rebuilds the daily, calendar-month
and bill aggregates deterministically, checks each derived CSV against its own sha256 pin, and
writes them to a local folder that is never committed (default: ``local/`` next to this file).

Standalone: numpy, pandas and the standard library only; no CAMBER install. ::

    python fetch_bdg2.py                      # download to the cache, build into ./local
    python fetch_bdg2.py --source DIR         # use BDG2 files already on disk (still verified)
    python fetch_bdg2.py --out DIR --cache DIR

With ``--predictions`` it also writes CAMBER's predicted series for the BDG2 cases, rebuilt from
the coefficients in ``expected.json`` (``generate.py`` writes the same series from the fitted
models, when CAMBER is installed).

Data: Building Data Genome Project 2. Miller, C., Kathirgamanathan, A., Picchetti, B. et al.
(2020). The Building Data Genome Project 2, energy meter data from the ASHRAE Great Energy
Predictor III competition. Scientific Data 7, 368. doi:10.1038/s41597-020-00712-x. Licence
CC BY-SA 4.0. The files this script writes are an adaptation of that data and carry the same
licence; keep them local or share them under CC BY-SA 4.0 with this attribution.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "local")

CITATION = (
    "Miller, C., Kathirgamanathan, A., Picchetti, B. et al. (2020). The Building Data Genome "
    "Project 2, energy meter data from the ASHRAE Great Energy Predictor III competition. "
    "Scientific Data 7, 368. doi:10.1038/s41597-020-00712-x"
)
LICENCE = "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)"
LANDING = "https://github.com/buds-lab/building-data-genome-project-2"

# The source files, with the URLs and sha256 pins of CAMBER's `bdg2` catalog entry
# (camber/datasets/catalog.json; a test keeps the two in step).
_MEDIA = (
    "https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/master/data/"
)
SOURCES = {
    "weather.csv": (
        _MEDIA + "weather/weather.csv",
        "a8189f1c6acdf3b9933a9e6354b8e7c1278cd56a7075929623a17565d44f04bd",
    ),
    "cleaned/electricity_cleaned.csv": (
        _MEDIA + "meters/cleaned/electricity_cleaned.csv",
        "b6ffc9b4dfcefe5c753594730a08ae822b0d50fec6815abb8f185591e6c630a3",
    ),
    "cleaned/chilledwater_cleaned.csv": (
        _MEDIA + "meters/cleaned/chilledwater_cleaned.csv",
        "8211aaf210379af50cbf7af87579d12414c3500d3e3f4dea725761c93a172bcd",
    ),
    "cleaned/steam_cleaned.csv": (
        _MEDIA + "meters/cleaned/steam_cleaned.csv",
        "ea5956c49ed1d6cc1b611a752b5a9dc3bcd4d45ddcd674e53b3c41661a6d3b9b",
    ),
    "cleaned/gas_cleaned.csv": (
        _MEDIA + "meters/cleaned/gas_cleaned.csv",
        "b3b059d32e8a16a92fb274e2e90483fac9b7797e42a9e07840ab9df130266405",
    ),
}

# --------------------------------------------------------------------------- the cases

KWH_PER_THERM = 29.30711
KBTU_PER_KWH = 3.412142
REAL = [
    {
        "id": "bdg2_rat_public_leta_elec",
        "building": "Rat_public_Leta",
        "meter": "electricity",
        "fuel": "electricity",
        "unit": "kWh",
        "factor": 1.0,
        "note": "electricity with heating and cooling arms (daily best: 5P)",
    },
    {
        "id": "bdg2_fox_lodging_stephen_chw",
        "building": "Fox_lodging_Stephen",
        "meter": "chilledwater",
        "fuel": "chilled_water",
        "unit": "kBtu",
        "factor": KBTU_PER_KWH,
        "note": "chilled water (daily best: 3PC)",
    },
    {
        "id": "bdg2_hog_education_jewel_steam",
        "building": "Hog_education_Jewel",
        "meter": "steam",
        "fuel": "steam",
        "unit": "kBtu",
        "factor": KBTU_PER_KWH,
        "note": "steam (daily best: 3PH)",
    },
    {
        "id": "bdg2_panther_education_sophia_gas",
        "building": "Panther_education_Sophia",
        "meter": "gas",
        "fuel": "natural_gas",
        "unit": "therm",
        "factor": 1.0 / KWH_PER_THERM,
        "bills": True,
        "note": "natural gas in a hot climate (daily best: 5P, but with a falling 'cooling' arm "
        "that fails SEP's sign test); also as irregular bill periods",
    },
    {
        "id": "bdg2_robin_education_lizbeth_elec",
        "building": "Robin_education_Lizbeth",
        "meter": "electricity",
        "fuel": "electricity",
        "unit": "kWh",
        "factor": 1.0,
        "note": "electricity, near-linear (daily best: 2P)",
    },
    {
        "id": "bdg2_hog_office_napoleon_elec",
        "building": "Hog_office_Napoleon",
        "meter": "electricity",
        "fuel": "electricity",
        "unit": "kWh",
        "factor": 1.0,
        "note": "electricity with no weather signal: the weak fit the baseline gate declines",
    },
]
REAL_YEARS = (2016, 2017)
MONTH_MIN_SHARE = 0.9  # a month or bill needs >= 90% of its days observed
BILL_SEED = 2016
BILL_DAYS = (27, 35)

# bill cases (shared with the synthetic bill case in gen_bills.py)
INJECTED_SAVING = 0.10
FIRST_READ_DAYS = (28, 35)
N_BASELINE_BILLS = 12  # the estimated read is baseline bill ESTIMATED_AT, merged into the next one
ESTIMATED_AT = 4
GAP_AT = 6  # the reporting bill left out (a missing bill)
BDG2_BILL_CASES = [
    {
        "id": "bills_bdg2_hog_education_jewel_steam",
        "source": "bdg2",
        "from_daily": "bdg2_hog_education_jewel_steam",
        "unit": "kBtu",
        "rate": 0.025,
        "seed": 202,
        "first_start": "2016-01-14",
        "last_day": "2017-12-31",
        "note": "BDG2 steam (heating only) re-read as bills",
    },
    {
        "id": "bills_bdg2_rat_public_leta_elec",
        "source": "bdg2",
        "from_daily": "bdg2_rat_public_leta_elec",
        "unit": "kWh",
        "rate": 0.12,
        "seed": 203,
        "first_start": "2016-01-14",
        "last_day": "2017-12-31",
        "note": "BDG2 electricity (heating and cooling arms) re-read as bills",
    },
]

# sha256 of every derived CSV, relative to the output folder. `generate.py` (with CAMBER and the
# BDG2 files) refreshes these; a test checks them whenever the BDG2 files are on disk.
DERIVED_SHA256 = {
    "inputs/bdg2/bdg2_rat_public_leta_elec_daily.csv": (
        "fde818cc8489e0a0bc7308a50e2fbf36af652633f86a56346d2ba60891f28af5"
    ),
    "inputs/bdg2/bdg2_rat_public_leta_elec_monthly.csv": (
        "935c01727f811def67af58f2620653351f39500a5c83a00973959c12faed7512"
    ),
    "inputs/bdg2/bdg2_fox_lodging_stephen_chw_daily.csv": (
        "9bfbaa2348ce7c3efc23bcc7703f22f6f14f04840b0ec12f100508cf152bfe50"
    ),
    "inputs/bdg2/bdg2_fox_lodging_stephen_chw_monthly.csv": (
        "62e574fb2bcbab2c89cdbe4efc32bff2543e699d397d395e550d6c7d551a35a8"
    ),
    "inputs/bdg2/bdg2_hog_education_jewel_steam_daily.csv": (
        "2dde24208acd0e50aa86ba6f0c09f4112d366c02567829869850b1fa4ea2fcd7"
    ),
    "inputs/bdg2/bdg2_hog_education_jewel_steam_monthly.csv": (
        "f0daf0f555615e804be25f87e4b08003524cf93a3b3b9c033ee3d84a3525a3a4"
    ),
    "inputs/bdg2/bdg2_panther_education_sophia_gas_daily.csv": (
        "5d46b6430ff553e601e2cccd13bf6ea24f2baf8b437dd658f2e9f165414911c7"
    ),
    "inputs/bdg2/bdg2_panther_education_sophia_gas_monthly.csv": (
        "00f204156981900183807dd28a00f4cba83f1c969c382749cba44ec4765f891f"
    ),
    "inputs/bdg2/bdg2_panther_education_sophia_gas_bills.csv": (
        "6e160337d0644c212bb426e705175ed3f5b969198a6a2df7821d4bdca8f9d0b1"
    ),
    "inputs/bdg2/bdg2_robin_education_lizbeth_elec_daily.csv": (
        "dc7575c7553b9e511b546e80fd45eaf2e9b56155cb5e226446da526527c03c9c"
    ),
    "inputs/bdg2/bdg2_robin_education_lizbeth_elec_monthly.csv": (
        "ded53f4ce5a49285418ccec20dbd9150bbbe0d5df896d63a00330f7554c446ec"
    ),
    "inputs/bdg2/bdg2_hog_office_napoleon_elec_daily.csv": (
        "3f7cd6939013f17cc4a4c05be5e9905eb4bf04000a32ce6321bebd0bcd1bcefe"
    ),
    "inputs/bdg2/bdg2_hog_office_napoleon_elec_monthly.csv": (
        "0de9f39dc679081fd6bc7f1231e72286ca2b866588700f80fd5355fc4e027f41"
    ),
    "inputs/bills/bills_bdg2_hog_education_jewel_steam_bills.csv": (
        "78125925df1867d8467c6288605d3a39b2bc82be52cc1251d76c171921a5b1db"
    ),
    "inputs/bills/bills_bdg2_hog_education_jewel_steam_oat.csv": (
        "e49f0f71a88509fd2f27f0715b4b83e11bf8e34e81099ab3ea038e236cbec6c9"
    ),
    "inputs/bills/bills_bdg2_rat_public_leta_elec_bills.csv": (
        "54c01bf0d3bf430036a3f9fb0d6e596108ddb9b09c832c1ce40dd8ef1c66d5e6"
    ),
    "inputs/bills/bills_bdg2_rat_public_leta_elec_oat.csv": (
        "8750502235cb2d6ea7e8a478cd521186271aafc84050a2299d75fa139f30c8b1"
    ),
}

# --------------------------------------------------------------------------- aggregation


def write_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")


def periodize(daily: pd.DataFrame, periods: list) -> pd.DataFrame:
    """Aggregate a daily table into periods ``[(start, end_inclusive), ...]``.

    A period is kept when at least ``MONTH_MIN_SHARE`` of its days are in the daily table; its
    ``energy`` is the observed sum scaled by ``days / days_observed`` and ``oat_f`` the mean of the
    observed days' OAT.
    """
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    rows = []
    for start, end in periods:
        days = int((end - start).days) + 1
        sl = d.loc[start:end]
        n = len(sl)
        if n < MONTH_MIN_SHARE * days:
            continue
        rows.append(
            {
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "days": days,
                "days_observed": n,
                "period": sl["period"].iloc[0],
                "oat_f": round(float(sl["oat_f"].mean()), 2),
                "energy": round(float(sl["energy"].sum()) * days / n, 2),
            }
        )
    return pd.DataFrame(rows)


def calendar_months(years) -> list:
    out = []
    for y in years:
        for m in range(1, 13):
            s = pd.Timestamp(year=y, month=m, day=1)
            out.append((s, s + pd.offsets.MonthEnd(0)))
    return out


def bill_periods(years, seed: int = BILL_SEED) -> list:
    """Seeded irregular read dates: 27-35-day bills inside each year (none crosses a year)."""
    rng = np.random.default_rng(seed)
    out = []
    for y in years:
        start, last = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")
        while start <= last:
            n = int(rng.integers(BILL_DAYS[0], BILL_DAYS[1] + 1))
            end = start + pd.Timedelta(days=n - 1)
            if (last - end).days < 20:  # a stub under 20 days joins this bill
                end = last
            out.append((start, end))
            start = end + pd.Timedelta(days=1)
    return out


def read_periods(case: dict) -> list:
    """A bill case's seeded 28-35-day read periods from ``first_start`` to ``last_day``."""
    rng = np.random.default_rng(case["seed"])
    s, last = pd.Timestamp(case["first_start"]), pd.Timestamp(case["last_day"])
    out = []
    while True:
        n = int(rng.integers(FIRST_READ_DAYS[0], FIRST_READ_DAYS[1] + 1))
        e = s + pd.Timedelta(days=n - 1)
        if e > last:
            break
        out.append((s, e))
        s = e + pd.Timedelta(days=1)
    return out


def bills_from_daily(case: dict, daily: pd.DataFrame) -> pd.DataFrame:
    """Bills over the read periods: energy = observed days' sum x days / days observed, with one
    estimated read (trued up by the next bill), one missing reporting bill, the injected saving
    and a synthetic seeded rate per bill."""
    d = daily.set_index("date")
    rng = np.random.default_rng(case["seed"] + 1000)
    rows = []
    for i, (s, e) in enumerate(read_periods(case)):
        sl = d.loc[s:e]
        days = int((e - s).days) + 1
        rows.append(
            {
                "start": s.strftime("%Y-%m-%d"),
                "end": e.strftime("%Y-%m-%d"),
                "days": days,
                "days_observed": len(sl),
                "period": "baseline" if i < N_BASELINE_BILLS else "reporting",
                "estimated": False,
                "energy": float(sl["energy"].sum()) * days / max(len(sl), 1),
                "rate": round(case["rate"] * (1.0 + rng.uniform(-0.08, 0.08)), 5),
            }
        )
    b = pd.DataFrame(rows)
    # an estimated read: the utility's guess from the previous bill's rate of use; the next actual
    # read trues it up, so the two bills together still carry the metered energy
    i = ESTIMATED_AT
    guess = b.loc[i - 1, "energy"] / b.loc[i - 1, "days"] * b.loc[i, "days"]
    b.loc[i + 1, "energy"] += b.loc[i, "energy"] - guess
    b.loc[i, "energy"] = guess
    b.loc[i, "estimated"] = True
    # a missing reporting bill: a gap in service
    b = b.drop(index=N_BASELINE_BILLS + GAP_AT).reset_index(drop=True)
    b["energy"] = b["energy"].round(2)
    b["energy_injected"] = np.where(
        b["period"] == "reporting", (b["energy"] * (1.0 - INJECTED_SAVING)).round(2), b["energy"]
    )
    b["cost"] = (b["energy"] * b["rate"]).round(2)
    b["cost_injected"] = (b["energy_injected"] * b["rate"]).round(2)
    cols = ["start", "end", "days", "days_observed", "period", "estimated", "energy"]
    return b[cols + ["energy_injected", "cost", "cost_injected"]]


def bdg2_daily(case: dict, weather: pd.DataFrame, source: str) -> pd.DataFrame:
    """Whole days only: 24 hourly meter readings, and >= 20 hourly air temperatures."""
    path = os.path.join(source, "cleaned", f"{case['meter']}_cleaned.csv")
    m = pd.read_csv(path, usecols=["timestamp", case["building"]], parse_dates=["timestamp"])
    m = m.set_index("timestamp")[case["building"]]
    day = m.index.normalize()
    cnt = m.notna().groupby(day).sum()
    tot = m.groupby(day).sum(min_count=1)
    energy = tot[cnt == 24] * case["factor"]
    site = case["building"].split("_")[0]
    w = weather[weather["site_id"] == site].dropna(subset=["airTemperature"])
    g = w.groupby(w["timestamp"].dt.normalize())["airTemperature"]
    oat = (g.mean() * 9.0 / 5.0 + 32.0)[g.count() >= 20]
    d = pd.DataFrame({"energy": energy, "oat_f": oat}).dropna()
    d = d[d.index.year.isin(REAL_YEARS)]
    return pd.DataFrame(
        {
            "date": d.index.strftime("%Y-%m-%d"),
            "period": np.where(d.index.year == REAL_YEARS[0], "baseline", "reporting"),
            "oat_f": np.round(d["oat_f"].to_numpy(), 2),
            "energy": np.round(d["energy"].to_numpy(), 2),
        }
    )


def bill_inputs(case: dict, daily_csv: pd.DataFrame, out: str) -> dict:
    """Write one bill case's bills and daily OAT CSVs from a daily table."""
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(daily_csv["date"]),
            "oat_f": daily_csv["oat_f"],
            "energy": daily_csv["energy"],
        }
    )
    rel_b = f"inputs/bills/{case['id']}_bills.csv"
    rel_t = f"inputs/bills/{case['id']}_oat.csv"
    write_csv(bills_from_daily(case, daily), os.path.join(out, rel_b))
    oat = pd.DataFrame({"date": daily["date"].dt.strftime("%Y-%m-%d"), "oat_f": daily["oat_f"]})
    write_csv(oat, os.path.join(out, rel_t))
    return {"bills": rel_b, "oat": rel_t}


# --------------------------------------------------------------------------- sources


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_cache() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mv_vectors", "bdg2")


def ensure_sources(*, source: str | None = None, cache: str | None = None) -> str:
    """The folder holding the verified BDG2 source files; downloads into ``cache`` when no
    ``source`` folder is given. Raises ``ValueError`` on a sha256 mismatch."""
    root = source or cache or default_cache()
    for name, (url, sha) in SOURCES.items():
        path = os.path.join(root, *name.split("/"))
        if not os.path.exists(path):
            if source:
                raise FileNotFoundError(f"{path} is missing (expected the BDG2 file {name})")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print(f"downloading {name} ...", file=sys.stderr)
            tmp = path + ".part"
            with urllib.request.urlopen(url) as r, open(tmp, "wb") as fh:  # noqa: S310
                shutil.copyfileobj(r, fh)
            os.replace(tmp, path)
        got = sha256_of(path)
        if got != sha:
            raise ValueError(f"{name}: sha256 {got} does not match the pinned {sha}")
    return root


def build(out: str, source: str) -> dict:
    """Write every BDG2-derived input CSV under ``out``; returns ``{case_id: {interval: rel}}``
    (bill cases: ``{"bills": rel, "oat": rel}``)."""
    weather = pd.read_csv(
        os.path.join(source, "weather.csv"),
        usecols=["timestamp", "site_id", "airTemperature"],
        parse_dates=["timestamp"],
    )
    files: dict = {}
    for c in REAL:
        rel = f"inputs/bdg2/{c['id']}_daily.csv"
        write_csv(bdg2_daily(c, weather, source), os.path.join(out, rel))
        files[c["id"]] = {"daily": rel}
    files.update(derive(out))
    return files


def derive(out: str) -> dict:
    """The monthly, bill-period and bill-case CSVs, from the daily CSVs already under ``out``."""
    files: dict = {}
    for c in REAL:
        rel_d = f"inputs/bdg2/{c['id']}_daily.csv"
        daily = pd.read_csv(os.path.join(out, rel_d))
        rel = f"inputs/bdg2/{c['id']}_monthly.csv"
        write_csv(periodize(daily, calendar_months(REAL_YEARS)), os.path.join(out, rel))
        files[c["id"]] = {"daily": rel_d, "monthly": rel}
        if c.get("bills"):
            rel = f"inputs/bdg2/{c['id']}_bills.csv"
            write_csv(periodize(daily, bill_periods(REAL_YEARS)), os.path.join(out, rel))
            files[c["id"]]["bills"] = rel
    for b in BDG2_BILL_CASES:
        daily = pd.read_csv(os.path.join(out, f"inputs/bdg2/{b['from_daily']}_daily.csv"))
        files[b["id"]] = bill_inputs(b, daily, out)
    return files


def derived_paths() -> list:
    """Every derived CSV path, relative to the output folder, in a fixed order."""
    out = []
    for c in REAL:
        out += [f"inputs/bdg2/{c['id']}_daily.csv", f"inputs/bdg2/{c['id']}_monthly.csv"]
        if c.get("bills"):
            out.append(f"inputs/bdg2/{c['id']}_bills.csv")
    for b in BDG2_BILL_CASES:
        out += [f"inputs/bills/{b['id']}_bills.csv", f"inputs/bills/{b['id']}_oat.csv"]
    return out


def verify(out: str) -> list:
    """Derived CSVs whose sha256 differs from its pin (``[]`` when all match)."""
    bad = []
    for rel in derived_paths():
        path = os.path.join(out, rel)
        if not os.path.exists(path) or sha256_of(path) != DERIVED_SHA256.get(rel):
            bad.append(rel)
    return bad


def available(out: str = DEFAULT_OUT) -> bool:
    """True when every derived CSV is under ``out`` (verification is separate)."""
    return all(os.path.exists(os.path.join(out, rel)) for rel in derived_paths())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help="where to write (default: ./local)")
    ap.add_argument("--cache", default=None, help=f"download cache (default {default_cache()})")
    ap.add_argument("--source", default=None, help="BDG2 files already on disk (verified)")
    ap.add_argument(
        "--predictions",
        action="store_true",
        help="also write CAMBER's predicted series, rebuilt from expected.json",
    )
    a = ap.parse_args(argv)
    print(f"Data: Building Data Genome Project 2 -- {CITATION}")
    print(f"Licence: {LICENCE}; {LANDING}")
    print("The files written below are an adaptation of that data, under the same licence.\n")
    src = ensure_sources(source=a.source, cache=a.cache)
    build(a.out, src)
    bad = verify(a.out)
    if bad:
        print("sha256 mismatch for:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    n = len(derived_paths())
    print(f"wrote and verified {n} files under {a.out}")
    if a.predictions:
        sys.path.insert(0, HERE)
        import check_vectors

        k = check_vectors.write_local_predictions(check_vectors.load_expected(), a.out)
        print(f"wrote {k} predicted series under {os.path.join(a.out, 'predictions')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
