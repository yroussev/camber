# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.1.0] — unreleased

First public pre-release.

### Added

- **Ingest** — per-point CSV, wide CSV, and a Project-Haystack `hisRead` client;
  per-point data-quality scoring with an auditable cleaning trail; valve/damper
  unit normalization (0–1 vs 0–100).
- **Semantic model** — vendor-neutral `Role` vocabulary, `MappingProvider`, an
  entity model with equipment templates and completeness validation, and
  `resolve()` to assemble role-named frames.
- **FDD** — rule engine with ASHRAE Guideline 36 AFDD and PNNL Building Re-tuning
  diagnostics (simultaneous heat/cool, reheat, SAT/CHW reset, economizer, OA
  fraction incl. under-ventilation, boiler lockout, boiler short-cycling, HW-loop
  low-ΔT, overcooling, setback, static
  and pump resets, chiller efficiency (kW/ton), chiller staging/cycling, multi-chiller
  over-staging (fleet), cooling-tower approach, condenser-water reset, CHW/HW pump
  riding-the-curve + VFD-minimum, leaking valves); impact prioritization and fault
  lifecycle; an
  FDD-accuracy evaluation harness.
- **Sequence-of-Operations conformance** — a declarative clause engine (`camber.soo`):
  gated predicates over roles (`when <gate> then expect <predicate>`) that measure
  operated-vs-designed behavior per clause as a conformance %, with optional
  time-based persistence (forgive transient excursions), JSON-authorable
  (`examples/soo/`) and emitting Findings into the same prioritization/report/triage.
- **M&V** — change-point inverse models (2P–5P + heating/cooling-zero), the LBNL
  TOWT model, fit statistics with fractional savings uncertainty, CUSUM, weather
  normalization, and rate/energy-aware resampling.
- **Domain analytics** — Std-55 comfort (PMV/PPD), utility cost, carbon, water
  (irrigation budget, cooling tower, leak detection), load profiling, PV, lighting.
- **Storage** — Parquet time-series store (entity-keyed, hive-partitioned) with
  tag-filtered reads, rollups, and retention pruning.
- **Interop** — Brick model import (derive role mappings) and Haystack/Brick export.
- **Integration & API** — findings → CMMS tickets with a pluggable notifier; a
  read-only HTTP API over the store.
- **Reporting** — ASHRAE/ACCA Standard 211 audit deliverables (text/HTML).
- **Examples** — runnable LBNL FDD and Building Data Genome 2 examples (public
  CC-BY datasets, fetched on demand), plus a data-free synthetic demo.
- Docker image and GitHub Actions CI (pytest on Python 3.10 / 3.11).

[0.1.0]: https://github.com/yroussev/camber/releases
