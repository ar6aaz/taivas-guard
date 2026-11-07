# Taivas Guard

Cached ITU IFIC ingestion and conservative frequency candidate screening.

The active Python package is `ific_guard`. The earlier scripts under
`docs/research-discarded/spike/` are research snapshots with machine-specific
paths; the new commands do not run those scripts.

## Install

Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[mdb,dev]"
```

On macOS/Linux, use `.venv/bin/python` instead.

## Import once and screen from the cache

Extract the public ITU downloads to local `.mdb` files first. Keep the original
inputs unchanged for audit purposes. Downloads are not automated by the app.

From PowerShell at the repository root:

```powershell
$ificPython = '.venv\Scripts\python.exe'
$stationInput = 'outputs/finland-synthetic-20261107/finland_synthetic_stations_100000.xlsx'

$national = & $ificPython -m ific_guard import-stations $stationInput | ConvertFrom-Json
$space = & $ificPython -m ific_guard --verbose import-ific 'C:\path\ific3074.mdb' --domain space --issue 3074 | ConvertFrom-Json

& $ificPython -m ific_guard screen --source $space.source_key --stations $national.source_key --output outputs/candidates3074.csv
& $ificPython -m ific_guard export-parquet --source $space.source_key --output outputs/assignments3074.parquet
```

The default database is `.cache/ific.duckdb`. Put `--db PATH`, `--threads N`, or
`--verbose` **before** the subcommand. Imports print JSON containing the
`source_key`, `cache_hit`, row counts and elapsed time; progress goes to stderr.
Analysis commands open the completed cache read-only, allowing multiple read-only
processes. Coordinate imports separately: DuckDB does not support concurrent
independent writer processes or readers in other processes while a writer holds
the database open.

Running an import again hashes the file and reuses the stored rows. MDB decoding,
workbook parsing and normalization are skipped on a cache hit. Cache keys include
the source SHA-256, domain, issue number, reader version, importer format version
and input options. Import and normalization commit atomically. Failed imports
leave no partial cache entry.

For larger national datasets, import normalized CSV with columns `station_id`,
`f_low_mhz`, `f_high_mhz`, and optional `latitude_deg`, `longitude_deg`. The
challenge workbook reader uses its English headers on row 11 and data from row
13. It supports explicit Hz/kHz/MHz/GHz units and decimal commas. Raw rows and
their source row numbers remain in the database. Missing bandwidth is marked for
review, never replaced with zero. Broadcast rows explicitly lacking RX are
classified as transmit-only.

## Candidate results and matching receiver IDs

`screen` writes one row per candidate assignment, not every assignment/station
pair. `matched_assignment_count` counts receiver **rows**, including separate
assignments that share a station ID. Zero-gap screening uses strict interval
overlap; touching edges do not match. `--gap-mhz 0.025` also includes assignments
within exactly 25 kHz of the filing range.

Retrieve station identities only for a selected candidate:

```powershell
& $ificPython -m ific_guard matches --source $space.source_key --stations $national.source_key --assignment 'NOTICE_ID:GROUP_ID' --output outputs/matching-receivers.csv
```

Space assignment IDs combine verbatim notice and group IDs. Terrestrial IDs use
`terrakey`. Individual matched rows also include their original workbook/CSV row
number so duplicate station IDs remain distinguishable.

Space screening includes transmitting GSO/NGSO groups. It retains groups listed
for Finland even without station overlap or GSO visibility. Missing frequencies
or relevant metadata produce review reasons. Invalid or missing national receiver
frequencies prevent a healthy zero-result interpretation: otherwise eligible
filings remain review candidates. GSO visibility uses a deliberately enlarged
rectangle enclosing Finland; unknown longitude is retained for review. NGSO
geometry is not used as an exclusion test. Terrestrial screening covers foreign
land-mobile classes FB/ML/FP/FL. Optional `--max-distance-km N` rejects stations
whose minimum distance to an enclosing Finland rectangle already exceeds N.
This is a conservative lower bound, not an exact border distance or regulatory
threshold; no radius is assumed by default. Unknown coordinates remain for review.
CSV rows retain the gap, radius and screening version for reproducibility.
Explicit suppressed assignments are excluded from
new interference candidates unless retained through the Finland-listed rule.

These are **screening candidates, not interference or compliance findings**.
This version does not implement PFD/P.1546 calculations, M4/FIN satellite-band
screening, regulatory limit decisions, or correspondence generation. Those must
consume the narrowed candidate set in a later analysis stage. DuckDB can use
multiple threads for queries; this version does not introduce a worker pool for
RF calculations that do not yet exist in the active package.

## Compare publications

Import another issue and pass both returned source keys:

```powershell
& $ificPython -m ific_guard changes --previous OLD_SOURCE_KEY --current NEW_SOURCE_KEY --output outputs/publication-changes.csv
```

IFIC publications are updates, not complete inventory snapshots. In publication mode explicit
ADD/MODIFY/SUPPRESS or A/M/S/W actions take precedence. Absent rows are labelled
`not_republished`, not withdrawn. Previously unseen rows without an explicit
action are labelled `published`, not assumed to be newly operational. Only use
`--complete-snapshots` when both inputs really are complete cumulative snapshots.

Space fingerprints include all imported notice/group/beam/emission/orbit and
coordination rows linked to a notice. A changed notice conservatively invalidates
its groups, including emission-only changes. Terrestrial fingerprints include
the assignment, linked location and antenna rows. Re-evaluate results when
national data, analysis rules or parameters change too; a publication diff alone
does not establish that an old calculation remains valid.

## Performance and checks

```powershell
& $ificPython -m pytest -q
& $ificPython tools/benchmark_overlap.py --stations 100000 --groups 10000
& $ificPython tools/benchmark_pipeline.py --stations 100000 --groups 10000
```

The overlap benchmark compares the production sorted-boundary index against the
original full-scan expression and requires identical counts. Sorting costs
O(S log S), queries O(G log S), and index memory is O(S). Screening fetches IFIC
groups in batches and keeps only one national frequency index per open store.
Detailed station identities are queried and streamed on demand.

The pipeline benchmark uses synthetic decoded tables through the actual store
interface. It checks cache reuse without invoking the decoder and identical
repeated results. It **does not measure real MDB decoding**.

The first MDB import still depends on `access-parser`, which materializes one
table at a time and has documented format limitations. This is not a fully
streaming MDB decoder. Only selected screening-relevant tables are imported;
all decoded rows in those tables are retained in `ific_raw`. Typed assignments
live in `ific_assignment`, national rows in `ific_station`, and manifests in
`ific_source`. Original MDB files are not copied into the cache. Bump
`FORMAT_VERSION` when reader/normalization semantics change.

Real public Space IFIC 3074 and Terrestrial IFIC 3076 MDBs were also exercised.
Space import stored 1,024,515 decoded rows and normalized 34,365 groups. On the
development machine, the first uncached import took 110 seconds, a cached import
0.81 seconds and candidate screening against 100,000 national records 0.59 seconds.
All 7,971 candidate overlap counts matched the original brute-force expression,
and all 220 Finland-listed groups were preserved. See `docs/performance.md` for
stage definitions, terrestrial measurements and limits of these numbers.

## Existing material

`ARCHITECTURE.md` and `schema.sql` describe the broader proposed system. The active
cache owns its `ific_*` tables and does not execute that draft schema. The
synthetic workbook and its rebuild scripts are documented in
`tools/synthetic-data/README.md`.
