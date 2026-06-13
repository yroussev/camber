# Store performance at portfolio scale

The Parquet store (`camber.store.ParquetStore`) is designed so a portfolio of hundreds of
buildings over years of interval data lives under one root and a query touches only the data it
needs. This note explains the three mechanisms that keep reads fast, how to measure them, and
the one cost that does grow with portfolio size.

## Layout

One tidy long-form dataset, hive-partitioned by `site` then `year`:

```
<root>/site=DemoSite/year=2024/part-*.parquet
```

A query for one site reads only that site's directory; a query for one year reads only that
year's subdirectory.

## The three scale mechanisms

1. **Partition pruning on `site` and `year`.** Filters on `site` skip other buildings'
   directories. Crucially, a `start`/`end` time range is translated into bounds on the `year`
   *partition* field as well as the `ts` data column (`_build_filter`), so a one-month query
   across a multi-year store opens only the relevant year partition(s) instead of scanning every
   year. Proven in `tests/test_store_scale.py` via `dataset.get_fragments(filter=…)`.

2. **Column projection.** Reads pull only the columns they need from Parquet. `points()`
   (catalog enumeration) projects just `site`/`equip`/`role` and never reads the `ts`/`value`
   payload; `read_role_frame` projects `ts`/`role`/`value` for the one equipment requested.

3. **Fast-path pivot.** `read_role_frame` uses a plain `pivot` when each `(ts, role)` is unique
   and only falls back to the slower mean-aggregating `pivot_table` when the store actually holds
   duplicate observations.

4. **Cached catalog.** `points()` enumerates distinct `(site, equip, role)` keys from a small
   `_catalog.json` written alongside the dataset, so it needs no partition scan. Writes
   *invalidate* the catalog (an O(1) file removal — they never rewrite it, so bulk ingestion
   stays cheap); the first `points()` after a write burst rebuilds it once with a projected
   scan and caches the result. (Arrow's dataset discovery ignores leading-`_` paths, so the
   file is invisible to reads. `rebuild_catalog()` materializes it for a store that predates
   the cache.)

## Measuring it

A synthetic generator + benchmark ships in the package:

```sh
python -m camber.store.bench --sites 50 --equips 10 --days 30 --freq 1h
```

It builds a portfolio and times the hot paths (`points`, single-equipment read, time-ranged
read, rollup). `benchmark()` / `synth_portfolio()` are importable for custom runs.

## Measured behaviour

On a developer laptop (numbers are illustrative, not a guarantee):

| portfolio | rows | write | single-equip read | time-ranged read | `points()` cold → warm |
|---|---:|---:|---:|---:|---:|
| 150 sites × 10 equips × 4 roles × 30 d hourly | 4.32 M | ~8.8 s | ~36 ms | ~240 ms | ~3.5 s → ~22 ms |

**The headline property:** a **single-equipment read stays roughly flat as the portfolio grows**
(tens of ms) — partition pruning + projection mean it opens one building's data regardless of how
many other buildings exist. This is the access pattern the rules/resolve layer uses, so
per-equipment analytics scale.

**Catalog enumeration** (`points()`) is served from the cached catalog in ~tens of ms once warm.
The first call after a write burst rebuilds it once (a projected scan — ~3.5 s here); writes
themselves stay cheap because they only invalidate the catalog, never rewrite it (an earlier
rewrite-on-every-write design made a bulk load ~5× slower — the invalidate-on-write design keeps
write throughput intact). Further mitigations if even the cold rebuild matters: scope it with
`points(site=…)`, or roll up + prune (`rollup` / `write_rollup` / `prune`) so old partitions are
smaller.
