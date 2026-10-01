"""Read open-fdd data into CAMBER, and hand CAMBER's findings back as JSON (provisional, 0.99).

`open-fdd <https://github.com/bbartling/open-fdd>`_ collects and stores building trends at the
edge. This module lets CAMBER's drift, M&V and sensor-trust checks run on that data, at a files
and processes boundary only: it reads open-fdd's documented package format
(``openfdd_package_v1``) or historian Parquet layout, imports no open-fdd code, calls no open-fdd
API and writes nothing back. See docs/INTEROP-OPENFDD.md.

- :func:`read_package` / :func:`read_historian` -- files -> CAMBER role frames, with every column's
  mapping (or the reason it was not mapped). The site time zone and the unit system are required.
- :func:`ingest_package` -- into a ParquetStore or a portfolio workspace (lifecycle-registered,
  audited, with provenance), optionally writing a starting run config (:func:`package_config`).
- :func:`load_crosswalk` / :func:`crosswalk_table` -- the versioned open-fdd -> CAMBER role
  crosswalk (``crosswalk.json``).
- :func:`findings_document` / :func:`run_findings` -- the engine-labelled findings JSON (draft
  schema ``findings-exchange`` 0.1) a separate process reads back.

**Provisional**: names, the crosswalk and the findings schema may change in a minor release.
"""

from __future__ import annotations

from ._crosswalk import Crosswalk, CrosswalkRow, load_crosswalk
from ._findings import (
    FINDINGS_SCHEMA,
    FINDINGS_SCHEMA_VERSION,
    findings_document,
    run_findings,
)
from ._historian import read_historian
from ._ingest import (
    OpenFddIngestResult,
    crosswalk_table,
    ingest_package,
    openfdd_meta,
    package_config,
    suggested_rules,
)
from ._reader import ColumnMapping, OpenFddEquipment, OpenFddPackage, read_package

__all__ = [
    "read_package",
    "read_historian",
    "ingest_package",
    "package_config",
    "suggested_rules",
    "openfdd_meta",
    "load_crosswalk",
    "crosswalk_table",
    "findings_document",
    "run_findings",
    "Crosswalk",
    "CrosswalkRow",
    "ColumnMapping",
    "OpenFddEquipment",
    "OpenFddPackage",
    "OpenFddIngestResult",
    "FINDINGS_SCHEMA",
    "FINDINGS_SCHEMA_VERSION",
]
