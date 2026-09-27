"""The dataset catalog: schema, loader and validator for ``camber/datasets/catalog.json``.

The catalog is package data -- a reviewed list of open building datasets CAMBER knows how to fetch,
verify and ingest. It never contains the data itself (CAMBER redistributes nothing; every file is
downloaded from its publisher). :func:`validate_catalog` is the gate a catalog change must pass:

* every entry carries the provenance a report needs (publisher, citation, licence, landing page);
* the licence is an SPDX id from :data:`LICENCES`, and ``access`` is ``"research_only"`` whenever
  the licence is non-commercial (NC) or no-derivatives (ND) -- an NC / ND dataset can never be
  labelled open. The reverse needs a stated reason: an open-licence entry is held research-only
  only with an ``access_reason`` (e.g. an archive that bundles third-party files whose open licence
  CAMBER cannot vouch for), which the licence gate, the report banner and the provenance show;
* every URL is ``https``; pinned files carry a size and a 64-hex sha256; a ``manual: true`` entry
  (files the user downloads by hand, e.g. from a portal with terms, then ``ingest --from-dir``)
  carries ``manual_instructions`` and may omit a file's URL;
* subsets, runs, archive members, mappings, config templates and quirks all resolve;
* every **data issue** -- a problem in the data *as published* -- is described with the columns it
  affects, numeric evidence, the publisher documentation it contradicts (with a DOI; a publisher
  that mints none is cited by a pinned https URL) and CAMBER's handling (:data:`HANDLINGS`), and
  the handling is wired: a ``fix`` issue has a ``fix`` quirk, an ``exclude`` issue names what it
  excludes, and every quirk links to its issue;
* the ingest keys have one spelling each: the names 0.89's intake branches used before they were
  reconciled (:data:`RENAMED_KEYS`) are rejected with the key to use instead.

The catalog never corrects published data silently: what CAMBER changes, and why, is the list of
data issues (``camber datasets info <id>``; rendered into ``docs/DATASETS.md`` by
``scripts/datasets_issues_doc.py``). CAMBER's *own* mistakes -- a mapping, an assumed design
parameter, a template rule -- are simply fixed in the mapping, config or code.

The encumbered-dataset denylist lives with the repository's site-neutrality guard, not in the
package; tests pass its patterns in through ``deny_patterns``.
"""

from __future__ import annotations

import codecs
import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from importlib.resources import files as _files

from ._quirks import validate_quirk
from ._readers import CLOCK_KINDS, ELAPSED_UNITS, EXTRAS, needs_extra
from ._units import canonical_unit

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
    "CDLA-Permissive-1.0": (True, False),
    "NIST-PD": (True, False),
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
ADAPTERS = ("wide_csv", "bdg2", "per_point")
ARCHIVES = ("zip", "tar")
#: How CAMBER handles a problem in the published data: correct it at ingest (a ``fix`` quirk, which
#: ``--no-corrections`` skips), leave it in place and say so, keep the affected runs / columns out
#: of scoring or analysis, or nothing beyond describing it.
HANDLINGS = ("fix", "annotate", "exclude", "none")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ISSUE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
#: Ingest keys the 0.89 intake branches spelled differently before they were reconciled:
#: ``{(level, old key): what to use instead}``; :func:`validate_catalog` rejects the old ones.
RENAMED_KEYS = {
    ("ingest", "tz_convert"): "source_timezone: 'offset' plus local_timezone",
    ("ingest", "synthetic_index"): "clock: {'kind': 'rows', 'freq': ..., 'start': ...}",
    ("ingest", "day_clock"): "clock: {'kind': 'day', 'day': ..., 'time': ..., ...}",
    ("ingest", "timestamp_unit"): "clock: {'kind': 'elapsed', 'unit': ..., 'origin': ...}",
    ("ingest", "timestamp_origin"): "clock: {'kind': 'elapsed', 'unit': ..., 'origin': ...}",
    ("run", "points"): "members: {raw column: member}",
}
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
    "HANDLINGS",
    "DatasetEntry",
    "RENAMED_KEYS",
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
    data_issues: tuple = ()
    licence_check: dict | None = None
    store_bytes_estimate: int | None = None
    manual: bool = False
    manual_instructions: str = ""
    contiguous: bool = True
    access_reason: str = ""

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
            data_issues=tuple(dict(i) for i in d.get("data_issues", ())),
            licence_check=d.get("licence_check"),
            store_bytes_estimate=d.get("store_bytes_estimate")
            or (d.get("subsets") or {}).get("default", {}).get("store_bytes_estimate"),
            manual=bool(d.get("manual", False)),
            manual_instructions=d.get("manual_instructions", ""),
            contiguous=bool(d.get("contiguous", True)),
            access_reason=d.get("access_reason", ""),
        )

    @property
    def research_only(self) -> bool:
        """True for the research-only tier (acknowledgement needed): the licence forbids commercial
        use or derivatives, or the entry states an ``access_reason`` for holding it there."""
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

    def store_bytes(self, name: str | None = None) -> int | None:
        """Estimated size of a subset once ingested into a store (``None`` if not recorded).

        Measured by ingesting the subset; the ``full`` subset of a large dataset is many times the
        ``default`` one, so a disk check must use the subset actually being ingested.
        """
        est = self.subset(name).get("store_bytes_estimate")
        return int(est) if est else None

    def data_issue(self, issue_id: str) -> dict:
        """The data issue called ``issue_id`` (``KeyError`` if absent)."""
        for i in self.data_issues:
            if i.get("id") == issue_id:
                return i
        raise KeyError(f"{self.id}: no data issue {issue_id!r}")

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
            **({"access_reason": self.access_reason} if self.access_reason else {}),
            "contiguous": self.contiguous,
            "known_issues": list(self.known_issues),
            "data_issues": [
                {k: i.get(k) for k in ("id", "title", "handling")} for i in self.data_issues
            ],
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
            "data_issues": [dict(i) for i in self.data_issues],
            "store_bytes_estimate": self.store_bytes_estimate,
        }
        if self.licence_check is not None:
            out["licence_check"] = self.licence_check
        if self.manual:
            out["manual"] = True
            out["manual_instructions"] = self.manual_instructions
        if not self.contiguous:
            out["contiguous"] = False
        if self.access_reason:
            out["access_reason"] = self.access_reason
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
    manual = bool(d.get("manual"))
    for f in d.get("files") or []:
        name = f.get("name") if isinstance(f, dict) else None
        if not name:
            errs.append(f"{did}: file without a name")
            continue
        if name in names:
            errs.append(f"{did}: duplicate file {name!r}")
        names[name] = f
        if manual and f.get("url") is None:
            pass  # a manual file may have no direct URL (the publisher's portal hands it out)
        elif not _https(f.get("url")):
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
    _check_transforms(did, ing, errs)
    _check_brick(did, ing, file_names, errs)
    for key, use in RENAMED_KEYS.items():
        if key[0] == "ingest" and key[1] in ing:
            errs.append(f"{did}: ingest.{key[1]} is not a key; use {use}")
    run_ids: set = set()
    equip_ids: set = set()
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
        _check_run_sources(did, str(rid), r, file_names[fname], fname, errs)
        eid = r.get("equip_id")
        if eid is not None:
            if not _RUN_RE.match(str(eid)) or "__" in str(eid) or eid in equip_ids:
                errs.append(f"{did}/{rid}: equip_id {eid!r} must be a unique plain name")
            equip_ids.add(eid)
        if not r.get("equip") or not r.get("class"):
            errs.append(f"{did}/{rid}: run needs 'equip' and 'class'")
        if "label" not in r:
            errs.append(f"{did}/{rid}: run needs a 'label' ('' for fault-free)")
        if "exclude" in r and not isinstance(r["exclude"], str):
            errs.append(f"{did}/{rid}: 'exclude' must name the data issue that excludes the run")
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


def _text_or_int(v) -> bool:
    return isinstance(v, (str, int)) and not isinstance(v, bool)


def _check_run_sources(did: str, rid: str, r: dict, f: dict, fname: str, errs: list) -> None:
    """A run reads one ``member`` or several ``members`` -- a list of tables joined on the
    timestamp, or ``{raw column: member}`` one-point files -- may keep only the rows ``where``
    columns hold a value (``{column: value or [values]}``), may name its own shipped ``mapping``
    with ``vars`` filling its ``{placeholders}``, and may override the entry's
    ``timestamp_format``, ``units``, ``encoding`` and ``sheet`` (see :mod:`._readers`)."""
    for key, use in RENAMED_KEYS.items():
        if key[0] == "run" and key[1] in r:
            errs.append(f"{did}/{rid}: {key[1]!r} is not a run key; use {use}")
    listed = f.get("members")
    many = r.get("members")
    wanted = [r["member"]] if r.get("member") else []
    if many is not None:
        if isinstance(many, dict):
            ok = bool(many) and all(isinstance(k, str) and k and v for k, v in many.items())
            vals = list(many.values())
        else:
            ok = isinstance(many, list) and bool(many) and all(many)
            vals = list(many) if isinstance(many, list) else []
        if not ok or r.get("member"):
            errs.append(
                f"{did}/{rid}: 'members' must be a non-empty list of tables or a "
                "{raw column: member} mapping, used instead of 'member'"
            )
        wanted = [m for m in vals if isinstance(m, str)]
    if wanted and f.get("archive") is None:
        errs.append(f"{did}/{rid}: 'member(s)' only apply to an archive file")
    for m in wanted:
        if listed is not None and m not in listed:
            errs.append(f"{did}/{rid}: member {m!r} is not in {fname}")
    mp = r.get("mapping")
    if mp is not None and not _package_has("mappings", mp):
        errs.append(f"{did}/{rid}: mapping {mp!r} is not shipped in camber/datasets/mappings/")
    vs = r.get("vars")
    if vs is not None and not (
        isinstance(vs, dict)
        and vs
        and all(isinstance(k, str) and _text_or_int(v) for k, v in vs.items())
    ):
        errs.append(f"{did}/{rid}: 'vars' must map placeholder names to text or integers")
    w = r.get("where")
    if w is not None:
        if isinstance(w, dict) and set(w) == {"column", "in"} and isinstance(w.get("in"), list):
            errs.append(
                f"{did}/{rid}: 'where' is {{column: value or [values]}}; "
                "{'column': c, 'in': [...]} is not a key"
            )
        elif not (
            isinstance(w, dict)
            and w
            and all(
                isinstance(k, str)
                and (
                    _text_or_int(v)
                    or (isinstance(v, list) and v and all(_text_or_int(x) for x in v))
                )
                for k, v in w.items()
            )
        ):
            errs.append(
                f"{did}/{rid}: 'where' must map column names to a value or a list of values"
            )
    _check_timestamp_format(f"{did}/{rid}", r.get("timestamp_format"), errs)
    for role, unit in (r.get("units") or {}).items():
        try:
            canonical_unit(unit)
        except ValueError as e:
            errs.append(f"{did}/{rid}: units[{role!r}]: {e}")


def _check_brick(did: str, ing: dict, file_names: dict, errs: list) -> None:
    """``ingest.brick`` names a shipped file (or archive member) and maps Brick classes to
    CAMBER equipment classes; ``group: "brick"`` runs need it. A ``group: "mapping"`` run is split
    by the mapping file's ``equipment`` map alone (no Brick model); a grouped run's ``target``
    lists the equipment under test, the only one(s) that carry its label."""
    b = ing.get("brick")
    grouped = [r.get("id") for r in ing.get("runs") or [] if r.get("group") == "brick"]
    for r in ing.get("runs") or []:
        if r.get("group") not in (None, "brick", "mapping"):
            errs.append(f"{did}/{r.get('id')}: group must be 'brick' or 'mapping'")
        if r.get("group") == "mapping" and not ing.get("mapping"):
            errs.append(f"{did}/{r.get('id')}: group 'mapping' needs ingest.mapping")
        tgt = r.get("target")
        if tgt is not None and (
            r.get("group") is None
            or not isinstance(tgt, list)
            or not tgt
            or not all(isinstance(t, str) and t for t in tgt)
        ):
            errs.append(
                f"{did}/{r.get('id')}: 'target' must list the grouped run's equipment under test"
            )
    if b is None:
        if grouped:
            errs.append(f"{did}: runs {grouped} group by Brick but ingest.brick is not set")
        return
    if not isinstance(b, dict) or b.get("file") not in file_names:
        errs.append(f"{did}: ingest.brick.file must name one of the entry's files")
        return
    if b.get("member") and file_names[b["file"]].get("archive") is None:
        errs.append(f"{did}: ingest.brick.member only applies to an archive file")
    members = file_names[b["file"]].get("members")
    if b.get("member") and members is not None and b["member"] not in members:
        errs.append(f"{did}: ingest.brick.member {b['member']!r} is not in {b['file']}")
    ec = b.get("equip_classes")
    if (
        not isinstance(ec, dict)
        or not ec
        or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in ec.items())
    ):
        errs.append(
            f"{did}: ingest.brick.equip_classes must map Brick classes to CAMBER equipment classes"
        )


def _check_transforms(did: str, ing: dict, errs: list) -> None:
    """CAMBER's own column semantics, applied at ingest in every mode (they are not corrections).

    ``timestamp_format`` pins how timestamps parse (a month-first export must never be guessed;
    ``"ISO8601"`` for stamps that mix precisions); ``clock`` builds the time of a source without a
    wall-clock column (an elapsed simulation clock, or a synthetic day / row clock);
    ``source_timezone`` / ``local_timezone`` move a dataset published in UTC, another zone or with
    per-row UTC offsets (``"offset"``) onto the site's wall clock; ``encoding`` (on the spec or a
    run) is a CSV's text encoding; ``recode`` maps a raw column's values (``{"SYS_CTL": {"2":
    0}}``: a 0/1/2 mode point read as occupied only in mode 1); ``derive`` adds a raw column: the
    ``sum`` of others (a dual-duct unit's supply airflow is its cold- plus hot-deck flows), a 0/1
    flag for another column being ``above`` a threshold (``["SF_CS", 0]``: the fan runs when its
    speed command is above zero), or a ``copy`` of one column (one building-wide schedule read by
    every zone of a grouped table).
    """
    for role, unit in (ing.get("units") or {}).items():
        try:
            canonical_unit(unit)
        except ValueError as e:
            errs.append(f"{did}: units[{role!r}]: {e}")
    _check_timestamp_format(f"{did}: ingest", ing.get("timestamp_format"), errs)
    _check_clock(did, ing, errs)
    src, local = ing.get("source_timezone"), ing.get("local_timezone")
    if src is not None and src != "offset" and not _valid_tz(src):
        errs.append(f"{did}: ingest.source_timezone must be 'offset' or an IANA zone")
    if local is not None and not _valid_tz(local):
        errs.append(f"{did}: ingest.local_timezone must be an IANA zone")
    if src is not None and local is None:
        errs.append(f"{did}: ingest.source_timezone needs a local_timezone to convert to")
    for where in [ing, *(ing.get("runs") or [])]:
        enc = where.get("encoding") if isinstance(where, dict) else None
        if enc is not None:
            try:
                codecs.lookup(str(enc))
            except LookupError:
                errs.append(f"{did}: unknown text encoding {enc!r}")
    for col, table in (ing.get("recode") or {}).items():
        if not isinstance(table, dict) or not table:
            errs.append(f"{did}: recode {col!r} must map source values to numbers")
            continue
        for k, v in table.items():
            try:
                float(k)
            except (TypeError, ValueError):
                errs.append(f"{did}: recode {col!r} key {k!r} is not a number")
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errs.append(f"{did}: recode {col!r} value for {k!r} must be a number")
    for dv in ing.get("derive") or []:
        if not isinstance(dv, dict) or not dv.get("column"):
            errs.append(f"{did}: derive needs a 'column'")
            continue
        above, src = dv.get("above"), dv.get("sum")
        ok_above = (
            isinstance(above, list)
            and len(above) == 2
            and isinstance(above[0], str)
            and isinstance(above[1], (int, float))
            and not isinstance(above[1], bool)
        )
        ok_sum = isinstance(src, list) and len(src) >= 2
        ok_copy = isinstance(dv.get("copy"), str) and bool(dv["copy"])
        if [ok_above, ok_sum, ok_copy].count(True) != 1:
            errs.append(
                f"{did}: derive {dv['column']!r} needs exactly one of a 'sum' of at least two raw "
                "columns, 'above': [column, threshold] or 'copy': column"
            )


def _check_timestamp_format(where: str, fmt, errs: list) -> None:
    if fmt is not None and (not isinstance(fmt, str) or ("%" not in fmt and fmt != "ISO8601")):
        errs.append(f"{where}: timestamp_format must be a strftime format string or 'ISO8601'")


def _valid_tz(name) -> bool:
    try:
        import pandas as pd

        pd.Timestamp("2020-01-01").tz_localize(str(name))
    except Exception:  # noqa: BLE001 - any failure means pandas cannot use the zone
        return False
    return True


def _iso_date(value) -> bool:
    try:
        _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return False
    return True


def _check_clock(did: str, ing: dict, errs: list) -> None:
    """``clock`` (see :mod:`._readers`): ``elapsed`` needs a ``unit`` and an ISO ``origin`` and
    excludes ``timestamp_format``; ``day`` needs its ``day`` and ``time`` columns, an ISO
    ``start`` and a positive ``every_days``; ``rows`` needs a pandas ``freq``."""
    clk = ing.get("clock")
    if clk is None:
        return
    kind = clk.get("kind") if isinstance(clk, dict) else None
    if kind not in CLOCK_KINDS:
        errs.append(f"{did}: ingest.clock.kind must be one of {CLOCK_KINDS}")
        return
    if kind == "elapsed":
        if clk.get("unit") not in ELAPSED_UNITS or ing.get("timestamp_format") is not None:
            errs.append(
                f"{did}: an elapsed clock's unit must be one of {ELAPSED_UNITS} "
                "(and it excludes timestamp_format)"
            )
        if not _iso_date(clk.get("origin", "")):
            errs.append(f"{did}: an elapsed clock needs an ISO 'origin'")
    elif kind == "day":
        try:
            every = int(clk.get("every_days", 1))
        except (TypeError, ValueError):
            every = 0
        if not (clk.get("day") and clk.get("time")) or every < 1:
            errs.append(
                f"{did}: a day clock needs 'day' and 'time' columns and a positive integer "
                "'every_days'"
            )
        if not _iso_date(clk.get("start", "2000-01-03")):
            errs.append(f"{did}: a day clock's 'start' must be an ISO date")
    else:
        ok = isinstance(clk.get("freq"), str) and bool(clk["freq"])
        if ok:
            try:
                import pandas as pd

                pd.tseries.frequencies.to_offset(clk["freq"])
            except (TypeError, ValueError):
                ok = False
        if not ok:
            errs.append(f"{did}: a rows clock needs a 'freq' (e.g. '1min')")
        if not _iso_date(clk.get("start", "2000-01-01")):
            errs.append(f"{did}: a rows clock's 'start' must be an ISO date")


def _check_targets(did: str, d: dict, errs: list) -> None:
    labels = d.get("labels") or {}
    types = set(labels.get("fault_types") or {})
    for det, target in (labels.get("targets") or {}).items():
        wanted = [target] if isinstance(target, str) else target
        if not isinstance(wanted, list) or not wanted:
            errs.append(f"{did}: target of {det!r} must be a fault type or a list of them")
            continue
        for t in wanted:
            if types and t not in types:
                errs.append(f"{did}: target {t!r} of {det!r} is not a declared fault type")


def _check_issue_fields(did: str, iss: dict, errs: list, *, has_doi: bool = True) -> str:
    iid = iss.get("id") if isinstance(iss, dict) else None
    if not isinstance(iss, dict) or not _ISSUE_RE.match(str(iid or "")):
        errs.append(f"{did}: data issue id {iid!r} must match {_ISSUE_RE.pattern}")
        return str(iid)
    where = f"{did}/issue {iid}"
    for k in ("title", "evidence", "handling_note"):
        if not str(iss.get(k) or "").strip():
            errs.append(f"{where}: missing {k!r}")
    cols = iss.get("columns")
    if not isinstance(cols, list) or not cols or not all(isinstance(c, str) and c for c in cols):
        errs.append(f"{where}: 'columns' must list the affected column(s)")
    if not re.search(r"\d", str(iss.get("evidence") or "")):
        errs.append(f"{where}: evidence must be quantitative (state the numbers)")
    doc = iss.get("contradicts") or {}
    if not str(doc.get("document") or "").strip():
        errs.append(f"{where}: 'contradicts.document' must name the documentation contradicted")
    cite = str(doc.get("citation") or "")
    if not _DOI_RE.search(cite) and (has_doi or not re.search(r"https://\S+", cite)):
        # a publisher that mints no DOI (a versioned repository) is cited by a pinned URL
        errs.append(
            f"{where}: 'contradicts.citation' must cite the documentation by DOI "
            "(or, for an entry without DOIs, by a pinned https URL)"
        )
    if iss.get("handling") not in HANDLINGS:
        errs.append(f"{where}: handling must be one of {HANDLINGS}")
    if iss.get("runs") is not None and not isinstance(iss.get("runs"), list):
        errs.append(f"{where}: 'runs' must be a list of run ids or globs")
    return str(iid)


def _check_issues(did: str, d: dict, run_ids: set, errs: list) -> None:
    """Data issues are described, evidenced, cited, and their handling is wired up."""
    ing = d.get("ingest") or {}
    issues: dict = {}
    for iss in d.get("data_issues") or []:
        iid = _check_issue_fields(did, iss, errs, has_doi=bool(d.get("dois")))
        if iid in issues:
            errs.append(f"{did}: duplicate data issue {iid!r}")
        issues[iid] = iss if isinstance(iss, dict) else {}
    fixes: set = set()
    for q in ing.get("quirks") or []:
        ref = q.get("issue") if isinstance(q, dict) else None
        if ref not in issues:
            errs.append(
                f"{did}: quirk {q.get('op') if isinstance(q, dict) else q!r} must link to a "
                f"described data issue (issue={ref!r})"
            )
            continue
        if q.get("action") == "fix":
            fixes.add(ref)
            if issues[ref].get("handling") != "fix":
                errs.append(f"{did}: fix quirk links to issue {ref!r}, whose handling is not 'fix'")
    excluded_runs: dict = {}
    for r in ing.get("runs") or []:
        if "exclude" in r:
            excluded_runs[r.get("id")] = r["exclude"]
            if issues.get(r["exclude"], {}).get("handling") != "exclude":
                errs.append(
                    f"{did}/{r.get('id')}: excluded by {r['exclude']!r}, which is not an "
                    "'exclude' data issue"
                )
    for iid, iss in issues.items():
        where = f"{did}/issue {iid}"
        if iss.get("handling") == "fix" and iid not in fixes:
            errs.append(f"{where}: handling 'fix' needs a fix quirk with issue={iid!r}")
        if iss.get("handling") != "exclude":
            continue
        ex = iss.get("exclude") or {}
        if not any(ex.get(k) for k in ("runs", "columns", "analyses")):
            errs.append(
                f"{where}: handling 'exclude' must say what it excludes (runs, columns or analyses)"
            )
        for rid in ex.get("runs") or []:
            if rid not in run_ids:
                errs.append(f"{where}: excluded run {rid!r} is not a run")
            elif excluded_runs.get(rid) != iid:
                errs.append(f"{where}: run {rid!r} must carry exclude={iid!r}")
        mp = ing.get("mapping")
        if ex.get("columns") and mp and _package_has("mappings", mp):
            aliases = {
                k.lower() for k in json.loads(package_text("mappings", mp)).get("aliases", {})
            }
            for c in ex["columns"]:
                if c.lower() in aliases:
                    errs.append(f"{where}: excluded column {c!r} is mapped in {mp}")


def _check_subsets(did: str, d: dict, file_names: dict, run_ids: set, errs: list) -> None:
    subsets = d.get("subsets") or {}
    for need in ("default", "full"):
        if need not in subsets:
            errs.append(f"{did}: needs a {need!r} subset")
    for sname, sub in subsets.items():
        est = sub.get("store_bytes_estimate")
        if not isinstance(est, int) or isinstance(est, bool) or est <= 0:
            errs.append(f"{did}/{sname}: store_bytes_estimate must be a positive integer (bytes)")
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


def _check_extras(did: str, d: dict, errs: list) -> None:
    """``requires_extras`` names known extras, and lists ``xlsx`` when any run reads a workbook."""
    extras = d.get("requires_extras") or []
    if not isinstance(extras, list):
        errs.append(f"{did}: requires_extras must be a list")
        return
    for x in extras:
        if x not in EXTRAS:
            errs.append(f"{did}: unknown extra {x!r} in requires_extras (known: {sorted(EXTRAS)})")
    for r in (d.get("ingest") or {}).get("runs") or []:
        need = needs_extra(r.get("member") or r.get("file") or "")
        if need and need not in extras:
            errs.append(
                f"{did}/{r.get('id')}: reads a workbook, so requires_extras must list {need!r}"
            )


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
    reason = d.get("access_reason")
    if access not in ACCESS:
        errs.append(f"{did}: access must be one of {ACCESS}")
    elif lic in LICENCES and access == "open" and is_research_only_licence(lic):
        errs.append(
            f"{did}: access 'open' contradicts licence {lic!r} (an NC or ND licence is always "
            "research_only)"
        )
    elif lic in LICENCES and access == "research_only" and not is_research_only_licence(lic):
        if not (isinstance(reason, str) and reason.strip()):
            errs.append(
                f"{did}: access 'research_only' with the open licence {lic!r} needs an "
                "access_reason (why CAMBER holds it research-only)"
            )
    if reason is not None and (
        not isinstance(reason, str)
        or not reason.strip()
        or access != "research_only"
        or is_research_only_licence(str(lic))
    ):
        errs.append(
            f"{did}: access_reason only states why an open-licence entry is held research_only"
        )
    if d.get("kind") not in KINDS:
        errs.append(f"{did}: kind must be one of {KINDS}")
    if not _https(d.get("landing_url")):
        errs.append(f"{did}: landing_url must be https")
    lc = d.get("licence_check")
    if lc is not None and not _https((lc or {}).get("url")):
        errs.append(f"{did}: licence_check.url must be https")
    for k in ("expect", "json_path"):
        if lc is not None and k in lc and not (isinstance(lc[k], str) and lc[k].strip()):
            errs.append(f"{did}: licence_check.{k} must be a non-empty string")
    if "manual" in d and not isinstance(d["manual"], bool):
        errs.append(f"{did}: manual must be true or false")
    if "contiguous" in d and not isinstance(d["contiguous"], bool):
        errs.append(f"{did}: contiguous must be true or false")
    if d.get("manual") and not str(d.get("manual_instructions") or "").strip():
        errs.append(
            f"{did}: a manual entry needs manual_instructions (where and how to download the files)"
        )
    try:
        _dt.date.fromisoformat(str(d.get("verified_on", "")))
    except ValueError:
        errs.append(f"{did}: verified_on must be an ISO date")
    file_names = _check_files(did, d, errs)
    run_ids = _check_ingest(did, d, file_names, errs)
    if (d.get("ingest") or {}).get("adapter") == "per_point":
        from ._perpoint import check_spec

        check_spec(did, d.get("ingest") or {}, file_names, d.get("subsets") or {}, errs)
    _check_subsets(did, d, file_names, run_ids, errs)
    _check_targets(did, d, errs)
    _check_issues(did, d, run_ids, errs)
    _check_extras(did, d, errs)
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
