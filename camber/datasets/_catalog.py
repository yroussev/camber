"""The dataset catalog: schema, loader and validator for ``camber/datasets/catalog.json``.

The catalog is package data -- a reviewed list of open building datasets CAMBER knows how to fetch,
verify and ingest. It never contains the data itself (CAMBER redistributes nothing; every file is
downloaded from its publisher). :func:`validate_catalog` is the gate a catalog change must pass:

* every entry carries the provenance a report needs (publisher, citation, licence, landing page);
* the licence is an SPDX id from :data:`LICENCES`, and ``access`` is ``"research_only"`` **iff** the
  licence is non-commercial (NC) or no-derivatives (ND) -- so a research-only dataset can never be
  mislabelled open, nor an open one hidden behind the acknowledgement gate;
* every URL is ``https``; pinned files carry a size and a 64-hex sha256;
* subsets, runs, archive members, mappings, config templates and quirks all resolve.

The encumbered-dataset denylist lives with the repository's site-neutrality guard, not in the
package; tests pass its patterns in through ``deny_patterns``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from importlib.resources import files as _files

from ._quirks import validate_quirk

SCHEMA_VERSION = 1

# SPDX licence id -> (commercial use allowed, share-alike). NC / ND licences are research-only.
LICENCES = {
    "CC0-1.0": (True, False),
    "PDDL-1.0": (True, False),
    "CC-BY-3.0": (True, False),
    "CC-BY-4.0": (True, False),
    "ODC-BY-1.0": (True, False),
    "CC-BY-SA-3.0": (True, True),
    "CC-BY-SA-4.0": (True, True),
    "ODbL-1.0": (True, True),
    "MIT": (True, False),
    "BSD-3-Clause": (True, False),
    "Apache-2.0": (True, False),
    "CC-BY-NC-4.0": (False, False),
    "CC-BY-NC-SA-4.0": (False, True),
    "CC-BY-NC-ND-4.0": (False, False),
    "CC-BY-ND-4.0": (False, False),
}
ACCESS = ("open", "research_only")
KINDS = ("simulated", "real", "lab")
ADAPTERS = ("wide_csv", "bdg2")
ARCHIVES = ("zip", "tar")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REQUIRED = (
    "id",
    "title",
    "summary",
    "publisher",
    "citation",
    "landing_url",
    "licence",
    "access",
    "verified_on",
    "kind",
    "files",
    "subsets",
    "ingest",
)

__all__ = [
    "SCHEMA_VERSION",
    "LICENCES",
    "DatasetEntry",
    "is_research_only_licence",
    "load_catalog_data",
    "load_entries",
    "validate_catalog",
    "package_text",
]


def is_research_only_licence(licence: str) -> bool:
    """True for a non-commercial (NC) or no-derivatives (ND) licence id."""
    parts = str(licence).upper().split("-")
    return "NC" in parts or "ND" in parts


def package_text(*parts: str) -> str:
    """Read a text file shipped in ``camber.datasets`` (e.g. ``"mappings", "lbnl_sdahu.json"``)."""
    node = _files("camber.datasets")
    for p in parts:
        node = node.joinpath(p)
    return node.read_text("utf-8")


def _package_has(*parts: str) -> bool:
    node = _files("camber.datasets")
    for p in parts:
        node = node.joinpath(p)
    return node.is_file()


@dataclass(frozen=True)
class DatasetEntry:
    """One catalog entry (read-only view over the validated JSON)."""

    id: str
    title: str
    summary: str
    publisher: str
    citation: str
    landing_url: str
    licence: str
    access: str
    verified_on: str
    kind: str
    files: tuple
    subsets: dict
    ingest: dict
    teaches: tuple = ()
    dois: tuple = ()
    attribution_required: bool = True
    labeled_faults: bool = False
    labels: dict = field(default_factory=dict)
    equipment: str = ""
    timezone: str = ""
    requires_extras: tuple = ()
    suggested_analyses: dict = field(default_factory=dict)
    known_issues: tuple = ()
    licence_check: dict | None = None
    store_bytes_estimate: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> DatasetEntry:
        """Build from one catalog JSON object (assumed validated)."""
        return cls(
            id=d["id"],
            title=d["title"],
            summary=d["summary"],
            publisher=d["publisher"],
            citation=d["citation"],
            landing_url=d["landing_url"],
            licence=d["licence"],
            access=d["access"],
            verified_on=d["verified_on"],
            kind=d["kind"],
            files=tuple(dict(f) for f in d["files"]),
            subsets=dict(d["subsets"]),
            ingest=dict(d["ingest"]),
            teaches=tuple(d.get("teaches", ())),
            dois=tuple(d.get("dois", ())),
            attribution_required=bool(d.get("attribution_required", True)),
            labeled_faults=bool(d.get("labeled_faults", False)),
            labels=dict(d.get("labels", {})),
            equipment=d.get("equipment", ""),
            timezone=d.get("timezone", ""),
            requires_extras=tuple(d.get("requires_extras", ())),
            suggested_analyses=dict(d.get("suggested_analyses", {})),
            known_issues=tuple(d.get("known_issues", ())),
            licence_check=d.get("licence_check"),
            store_bytes_estimate=d.get("store_bytes_estimate"),
        )

    @property
    def research_only(self) -> bool:
        """True when the licence forbids commercial use or derivatives (acknowledgement needed)."""
        return self.access == "research_only"

    @property
    def commercial_ok(self) -> bool:
        """True when the licence allows commercial use."""
        return LICENCES.get(self.licence, (False, False))[0]

    @property
    def share_alike(self) -> bool:
        """True for a share-alike licence (redistributed adaptations keep the licence)."""
        return LICENCES.get(self.licence, (False, False))[1]

    def file(self, name: str) -> dict:
        """The file record called ``name`` (``KeyError`` if absent)."""
        for f in self.files:
            if f["name"] == name:
                return f
        raise KeyError(f"{self.id}: no file {name!r}")

    def subset(self, name: str | None = None) -> dict:
        """The subset spec (``None`` -> ``"default"``); ``KeyError`` names the valid subsets."""
        key = name or "default"
        if key not in self.subsets:
            raise KeyError(f"{self.id}: unknown subset {key!r} (known: {sorted(self.subsets)})")
        return self.subsets[key]

    def subset_files(self, name: str | None = None) -> list:
        """File records a subset needs, in catalog order."""
        wanted = self.subset(name).get("files", "all")
        if wanted == "all":
            return list(self.files)
        return [f for f in self.files if f["name"] in set(wanted)]

    def download_bytes(self, name: str | None = None) -> int:
        """Total declared download size of a subset (unknown sizes count as 0)."""
        return sum(int(f.get("size") or 0) for f in self.subset_files(name))

    def runs(self, name: str | None = None) -> list:
        """The ingest runs a subset selects (every run for ``"runs": "all"``)."""
        wanted = self.subset(name).get("runs", "all")
        runs = list(self.ingest.get("runs", []))
        if wanted == "all":
            return runs
        order = {r: i for i, r in enumerate(wanted)}
        return sorted((r for r in runs if r["id"] in order), key=lambda r: order[r["id"]])

    def provenance(self) -> dict:
        """The provenance block recorded on an ingested facility and shown in reports."""
        return {
            "dataset_id": self.id,
            "title": self.title,
            "publisher": self.publisher,
            "licence": self.licence,
            "access": self.access,
            "citation": self.citation,
            "dois": list(self.dois),
            "landing_url": self.landing_url,
            "attribution_required": self.attribution_required,
            "redistribution": "prohibited" if self.research_only else "allowed",
            "known_issues": list(self.known_issues),
        }

    def as_dict(self) -> dict:
        """A JSON-friendly dict of the entry (the catalog's own shape)."""
        out = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "teaches": list(self.teaches),
            "publisher": self.publisher,
            "citation": self.citation,
            "dois": list(self.dois),
            "landing_url": self.landing_url,
            "licence": self.licence,
            "access": self.access,
            "attribution_required": self.attribution_required,
            "verified_on": self.verified_on,
            "kind": self.kind,
            "labeled_faults": self.labeled_faults,
            "labels": self.labels,
            "equipment": self.equipment,
            "timezone": self.timezone,
            "requires_extras": list(self.requires_extras),
            "files": [dict(f) for f in self.files],
            "subsets": self.subsets,
            "ingest": self.ingest,
            "suggested_analyses": self.suggested_analyses,
            "known_issues": list(self.known_issues),
            "store_bytes_estimate": self.store_bytes_estimate,
        }
        if self.licence_check is not None:
            out["licence_check"] = self.licence_check
        return out


def load_catalog_data() -> dict:
    """The raw catalog JSON shipped with the package."""
    return json.loads(package_text("catalog.json"))


def load_entries(data: dict | None = None) -> list:
    """Validated :class:`DatasetEntry` objects (``ValueError`` listing every problem)."""
    data = load_catalog_data() if data is None else data
    errs = validate_catalog(data)
    if errs:
        raise ValueError("invalid dataset catalog:\n  " + "\n  ".join(errs))
    return [DatasetEntry.from_dict(d) for d in data["datasets"]]


def _https(url) -> bool:
    return isinstance(url, str) and url.startswith("https://") and len(url) > len("https://")


def _check_files(did: str, d: dict, errs: list) -> dict:
    names: dict = {}
    for f in d.get("files") or []:
        name = f.get("name") if isinstance(f, dict) else None
        if not name:
            errs.append(f"{did}: file without a name")
            continue
        if name in names:
            errs.append(f"{did}: duplicate file {name!r}")
        names[name] = f
        if not _https(f.get("url")):
            errs.append(f"{did}/{name}: url must be https")
        size, sha = f.get("size"), f.get("sha256")
        if size is not None and (not isinstance(size, int) or size <= 0):
            errs.append(f"{did}/{name}: size must be a positive integer or null")
        if sha is not None and not _SHA_RE.match(str(sha)):
            errs.append(f"{did}/{name}: sha256 must be 64 lowercase hex characters or null")
        if bool(f.get("pinned", sha is not None)) != (sha is not None and size is not None):
            errs.append(f"{did}/{name}: 'pinned' must be true exactly when size and sha256 are set")
        if f.get("archive") not in (None, *ARCHIVES):
            errs.append(f"{did}/{name}: archive must be one of {ARCHIVES} or null")
        if f.get("members") is not None and f.get("archive") is None:
            errs.append(f"{did}/{name}: 'members' only applies to an archive")
    if not names:
        errs.append(f"{did}: needs at least one file")
    return names


def _check_ingest(did: str, d: dict, file_names: dict, errs: list) -> set:
    ing = d.get("ingest") or {}
    if ing.get("adapter") not in ADAPTERS:
        errs.append(f"{did}: ingest.adapter must be one of {ADAPTERS}")
    fid = str(ing.get("facility") or "")
    if not fid.startswith("ds-"):
        errs.append(f"{did}: ingest.facility must start with 'ds-'")
    mp = ing.get("mapping")
    if mp is not None and not _package_has("mappings", mp):
        errs.append(f"{did}: mapping {mp!r} is not shipped in camber/datasets/mappings/")
    for q in ing.get("quirks") or []:
        errs.extend(f"{did}: {e}" for e in validate_quirk(q))
    run_ids: set = set()
    for r in ing.get("runs") or []:
        rid = r.get("id", "")
        if not _RUN_RE.match(str(rid)):
            errs.append(f"{did}: bad run id {rid!r}")
        if rid in run_ids:
            errs.append(f"{did}: duplicate run id {rid!r}")
        run_ids.add(rid)
        fname = r.get("file")
        if fname not in file_names:
            errs.append(f"{did}/{rid}: run file {fname!r} is not in files")
            continue
        members = file_names[fname].get("members")
        if r.get("member") and members is not None and r["member"] not in members:
            errs.append(f"{did}/{rid}: member {r['member']!r} is not in {fname}")
        if not r.get("equip") or not r.get("class"):
            errs.append(f"{did}/{rid}: run needs 'equip' and 'class'")
        if "label" not in r:
            errs.append(f"{did}/{rid}: run needs a 'label' ('' for fault-free)")
    for dv in ing.get("derived") or []:
        if dv.get("op") != "splice":
            errs.append(f"{did}: unknown derived op {dv.get('op')!r}")
        for k in ("base", "fault"):
            if dv.get(k) not in run_ids:
                errs.append(f"{did}: splice {k} {dv.get(k)!r} is not a run")
        try:
            _dt.date.fromisoformat(str(dv.get("onset", ""))[:10])
        except ValueError:
            errs.append(f"{did}: splice onset must be an ISO date")
    return run_ids


def _check_subsets(did: str, d: dict, file_names: dict, run_ids: set, errs: list) -> None:
    subsets = d.get("subsets") or {}
    for need in ("default", "full"):
        if need not in subsets:
            errs.append(f"{did}: needs a {need!r} subset")
    for sname, sub in subsets.items():
        fl = sub.get("files", "all")
        if fl != "all":
            for n in fl:
                if n not in file_names:
                    errs.append(f"{did}/{sname}: subset file {n!r} is not in files")
        runs = sub.get("runs", "all")
        if runs != "all":
            for rid in runs:
                if rid not in run_ids:
                    errs.append(f"{did}/{sname}: subset run {rid!r} is not a run")


def _check_entry(d: dict, deny: list, errs: list) -> None:
    did = d.get("id", "?")
    for k in _REQUIRED:
        if k not in d or d[k] in (None, ""):
            errs.append(f"{did}: missing {k!r}")
    if not _ID_RE.match(str(did)):
        errs.append(f"{did}: id must match {_ID_RE.pattern}")
    lic = d.get("licence")
    if lic not in LICENCES:
        errs.append(f"{did}: licence {lic!r} is not an allowed SPDX id ({sorted(LICENCES)})")
    access = d.get("access")
    if access not in ACCESS:
        errs.append(f"{did}: access must be one of {ACCESS}")
    elif lic in LICENCES and (access == "research_only") != is_research_only_licence(lic):
        errs.append(
            f"{did}: access {access!r} contradicts licence {lic!r} "
            "(research_only exactly when the licence is NC or ND)"
        )
    if d.get("kind") not in KINDS:
        errs.append(f"{did}: kind must be one of {KINDS}")
    if not _https(d.get("landing_url")):
        errs.append(f"{did}: landing_url must be https")
    lc = d.get("licence_check")
    if lc is not None and not _https((lc or {}).get("url")):
        errs.append(f"{did}: licence_check.url must be https")
    try:
        _dt.date.fromisoformat(str(d.get("verified_on", "")))
    except ValueError:
        errs.append(f"{did}: verified_on must be an ISO date")
    file_names = _check_files(did, d, errs)
    run_ids = _check_ingest(did, d, file_names, errs)
    _check_subsets(did, d, file_names, run_ids, errs)
    tmpl = (d.get("suggested_analyses") or {}).get("config_template")
    if tmpl is not None and not _package_has("configs", tmpl):
        errs.append(f"{did}: config template {tmpl!r} is not shipped in camber/datasets/configs/")
    if deny:
        blob = json.dumps(d).lower()
        for pat in deny:
            if re.search(pat, blob, flags=re.IGNORECASE):
                errs.append(f"{did}: matches an encumbered-dataset pattern")


def validate_catalog(data, *, deny_patterns=()) -> list:
    """Every problem with a catalog document (an empty list means valid).

    ``deny_patterns`` are regexes no entry may match (the repository's encumbered-dataset guard
    patterns, passed in by the test-suite -- the package itself carries no such list).
    """
    errs: list = []
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return [f"catalog must be an object with schema == {SCHEMA_VERSION}"]
    entries = data.get("datasets")
    if not isinstance(entries, list) or not entries:
        return ["catalog.datasets must be a non-empty list"]
    seen: set = set()
    for d in entries:
        if not isinstance(d, dict):
            errs.append("every dataset entry must be an object")
            continue
        did = d.get("id")
        if did in seen:
            errs.append(f"{did}: duplicate id")
        seen.add(did)
        _check_entry(d, list(deny_patterns), errs)
    return errs
