# 03 Space 3074 evaluation export

Status: resolved
Type: task
Size: M
Blocked by: none
Owns: `ific_guard/export.py`, `outputs/`, one `export` command in `ific_guard/cli.py`, `tests/test_export.py`

## Goal

Export Space IFIC 3074's cases as files an evaluator can read. This is required for submission.

A trial run already screened 3074 through `POST /issues/upload` with no errors, in under a minute at about 700 MB peak memory.

**Watch out:** cases are deduplicated across issues. That trial reported 51 new cases for 3074, but 3083 was processed in the same database, so a family seen in both issues was not counted as new for 3074. The true 3074 count may be higher. **The export must reflect everything the rules find in that issue**, whatever else the database holds, and must not depend on the order issues arrive. A clean database for one issue is not enough, because the system must keep running with many issues.

## Do

1. Import `data/shared/ific3074.zip` with `python -m ific_guard.cli import` into a database under your worktree, then run screening.
2. Add a way to record which issues each case was found in, additively, so a per-issue export is exact. A separate table is acceptable here, as an exception to the map's rule against new columns.
3. Add `python -m ific_guard.cli export --domain space --issue 3074 --out outputs/ific_3074/` writing:
   - `cases.csv`: one row per case, flat columns, ranked
   - `cases.json`: full `details`
   - `manifest.json`: source URL, file SHA-256, code version, config values, rule IDs, run time
4. Keep the format in one place, since ticket 02 may change it. Ticket 10 reuses this exporter for 3076.
5. Report what 3074 contains: how many AP30, AP30A and AP30B notices exist, and whether current scope (`ntf_rsn` in A and C) skips them. The brief puts those sections in scope.

## Acceptance

- The 3074 export has the same cases whether 3083 is processed before it, after it, or not at all. A test proves this.
- The export runs twice with identical content apart from timestamps.
- Every case in `cases.csv` traces to notice IDs and the file SHA-256.
- A hand check of 5 cases against the `.mdb`: reported in your final message with the rows you compared.
- AP30 coverage is stated plainly: handled, absent from 3074, or a gap.

## Not in scope

New rules, UI work, and `outputs/` for terrestrial.

## Answer

Implemented and verified on 2026-11-07. Added `ific_guard/export.py`, the
`export` CLI command, four regression tests and `outputs/ific_3074/`.

- Actual Space 3074: **51 cases**, 10 high and 41 medium.
- Operational pipeline: `screen_artifact(1)` returned
  `{'artifact_id': 1, 'status': 'done', 'opened': 51, 'changed': 0}`.
- Two exports produced byte-identical CSV/JSON. Runtime: 70.53 / 54.73 s.
- Full suite: **45 passed**, with one dependency deprecation warning.
- Five source-row comparisons and AP30 observations are recorded in
  [verification.md](../../../outputs/ific_3074/verification.md).
- AP30-family support remains explicitly a gap. All three AP30B-specific
  tables and `plan_pub` are empty in this MDB; `notice` contains only
  provisions 9.6, 9.1/IA, 11.2 and RS49.

The exporter re-evaluates the selected imported artifact rather than reading
mutable cross-issue `Case` rows. This prevents later issues from contaminating
the 3074 evaluation and leaves review decisions and notifications unchanged.
`--sha256` pins a revision. Manifests include source and MDB hashes, code
hashes, a secret-free screening configuration, rule IDs and known gaps.
The format is provisional until ticket 02 is answered. Terrestrial export
currently fails explicitly; ticket 10 must add its rule evaluation.

No new screening rules or database columns were introduced. Raw ITU files
remain ignored; only derived findings and verification documentation are
included in outputs. No commits, pushes or external messages were sent.

## Follow-up: per-issue occurrence persistence

Completed the requirements added by the upstream plan on 2026-11-07.
`case_occurrence` is an additive table linking each source artifact to a case,
with a complete issue-local hit snapshot. Screening replaces only that
artifact's occurrences in the same transaction as its case updates. A later
issue cannot overwrite an earlier issue's evidence; empty re-screen results
remove stale occurrences without deleting the expert's review case.

The exporter still evaluates the selected artifact directly, preserving its
side-effect-free behavior. Regression tests exercise the real screening path
with 3074 alone, 3074 before 3083, and 3083 before 3074. All three produce
byte-identical 3074 CSV/JSON and retain the correct per-issue evidence.
An additional test covers repeated screening and empty replacement results.

Validation: **68 tests passed**. On the actual imported databases, screening
3083 followed by 3074 persisted **39 and 51 occurrences**, respectively.
Existing installations need `python -m ific_guard.cli init-db` once to create
the additive table, then re-screen existing artifacts to backfill occurrences.
