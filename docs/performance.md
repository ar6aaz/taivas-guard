# Scalable IFIC processing: measurements

Measured on 7 November 2026 using Python 3.12, DuckDB 1.5.6 and access-parser
0.0.6. These are observed timings on the development machine, not promised
latency targets. CLI elapsed times include opening the cache, but exclude shell
and Python interpreter startup.

## Inputs

- Public Space IFIC 3074 download: https://www.itu.int/sns/ific10/ific3074.zip
  MDB SHA-256: `a9755213b6cdb7a26a1ca1dcf95ba69a362d3bcc2cb088e91b6b8e01e4790e31`.
- Public terrestrial IFIC 3076 download: https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3076.zip
  MDB SHA-256: `7358853076caab70e2a18c1d86cc1d22be40a28d0a17202aa6ad35729fce234d`.
- Repository synthetic national workbook: 100,000 records, of which 96,400 have
  usable RX frequency ranges. The 3,600 broadcast records without RX are
  explicitly transmit-only. No unknown receiver frequencies remained.

First-time downloads with a minimal user agent returned HTML rejection pages;
valid archives were retrieved with a full browser-compatible user-agent string.
Check that a download is actually a ZIP before extracting it.

## Real-file runs

| Stage | Space 3074 | Terrestrial 3076 |
|---|---:|---:|
| First uncached import | 110.43 s | 4.26 s |
| Re-import unchanged source | 0.81 s | 0.51 s |
| Screening, including candidate CSV output | 0.59 s | 0.47 s |
| Normalized assignments | 34,365 | 4,057 |
| Imported decoded rows, across selected tables | 1,024,515 | 12,978 |
| Candidate assignments | 7,971 | 33 |

The terrestrial run used an explicitly selected 1,000 km radius prefilter.
Distance is a lower bound to a rectangle enclosing Finland, not exact border
distance. That radius is a modelling parameter, not a coordination limit.
The space run used exact frequency overlap and the conservative GSO visibility
test. Both runs used the synthetic national workbook and no adjacency gap.

The national workbook's first import took 32.06 s; reuse took 0.61 s. Cache hits
still hash source bytes, check metadata and open the database. They do not reopen
the workbook or decode the MDB tables.

Every real space candidate's overlap count was independently recomputed using
the original NumPy full-scan expression. All 7,971 counts matched. The candidate
set retained all 220 normalized groups with Finland listed in `provn`; a selected
candidate's on-demand station identities also reconciled to its count.

## Reproducible benchmarks

After installing the package:

```powershell
python tools/benchmark_overlap.py --stations 100000 --groups 10000
python tools/benchmark_pipeline.py --stations 100000 --groups 10000
python -m pytest -q
```

The overlap-only benchmark observed 1.132 s for exhaustive comparisons and
0.0107 s for the sorted-boundary implementation, including sorting: about 106x
faster for this step, with identical counts. This is not an end-to-end speedup.

The synthetic decoded-table pipeline benchmark observed:

| Stage | Time |
|---|---:|
| Import 100,000 normalized national CSV records | 1.668 s |
| Import 10,000 IFIC-shaped groups and supporting rows | 0.538 s |
| Reuse that IFIC import without calling the reader | 0.004 s |
| Screen, including national index construction | 0.063 s |
| Repeat screening with the same in-memory national index | 0.049 s |

The pipeline fixture tests the real store interface but deliberately excludes
MDB decoding. Do not use these fixture import times as predictions for Access
files.

## What these measurements cover

They cover ingestion, content-cache reuse, typed normalization, frequency
screening, conservative geographic prefilters and candidate CSV export.
They do not cover PFD/P.1546 propagation, regulatory assessments, M4/passive-band
screening, LLM calls or complete analysis of all satellite emissions. Candidate
counts are not confirmed interference findings or challenge accuracy scores.

MDB decoding is still the principal first-import cost for the large space file.
AccessParser materializes each table; a native decoder could be added through
the reader seam if that cold cost remains unacceptable. Cached imports avoid
paying it again. Use `--verbose` to distinguish decoder timings from bulk-storage
timings when profiling another issue.
