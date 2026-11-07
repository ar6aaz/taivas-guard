# 15 Older IFIC issues

Status: ready-for-agent
Type: task
Size: M
Blocked by: 03
Owns: `ific_guard/sync/sources.py`, reads in `ific_guard/screening/space.py` that differ by database version, `tests/test_older_issues.py`

## Goal

The brief says "all existing IFICs and forthcoming IFICs shall be able to be evaluated". Only issues 3074-3083 have been exercised so far.

## What is known

- Space listing pages exist per year from 1998: `sns/wic/demowic{YY}.html`. The 2026 page offers "Version 10" databases from `sns/ific10/`.
- `discover_space` looks back at most 3 years, and its parser was written against the 2026 page.
- Terrestrial's page lists only the last 10 issues. Older ones use a known URL pattern, untested far back.
- ITU's space page says older `.mdb` material is no longer on that page, and is available on BR IFIC DVDs from ITU.
- `POST /issues/upload` already accepts any issue as a file, with explicit `domain` and `issue_no`.

## Do

1. Sample the older pages sparingly, a few per era, and record what formats, URLs and database versions exist. Do not bulk-download.
2. Make discovery reach back as far as `IFIC_MIN_ISSUE` needs, without failing the whole sync because one old page differs.
3. Screen one sample issue per database version you find. Read `srs_ooak.version_no`, and record it on the run or the case evidence, since field meanings can change between versions.
4. If a version cannot be read, reject it with a clear message that names the version, rather than producing empty results that look like "no concerns".
5. Write up what is supported in `docs/services-architecture.md`, as a table of issue range, how to get it, and whether it screens.

## Acceptance

- A test proves an unsupported database version is reported as unsupported, not as zero cases.
- A real sample from each version found, with its result, in your report.
- A full sync with the default `IFIC_MIN_ISSUE` behaves as before.

## Not in scope

Screening every historical issue, and obtaining DVD-only material.
