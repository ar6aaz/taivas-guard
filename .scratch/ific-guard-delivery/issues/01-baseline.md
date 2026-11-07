# 01 Baseline: commit, fix stray text, fetch shared data

Status: resolved
Type: task
Size: S
Blocked by: none

## Why

Everything built so far is uncommitted, and worktrees start from commits, so no agent can see it yet. Every other agent ticket waits on this one.

## Steps

1. A human reviews the uncommitted work and approves committing it.
2. Remove the stray `[118;1:3u` from `schema.sql` (line 121) and `ARCHITECTURE.md` (line 126). Check with a teammate first if it is their file in progress.
3. In `docs/services-architecture.md` section 9, correct the `ARCHITECTURE.md` line reference from 124 to 126, or drop the line number.
4. Commit. Decide whether agents branch from `main` or from an integration branch.
5. Download the issues once, for all agents to share:

```bash
mkdir -p data/shared && cd data/shared
curl -fLO https://www.itu.int/sns/ific10/ific3074.zip
curl -fLO https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3075.zip
curl -fLO https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3076.zip
cp ../../files/ific3083.zip .
for z in *.zip; do unzip -oq "$z"; done
```

## Acceptance

- `git status` is clean apart from ignored files.
- `uv run pytest` passes on a fresh worktree of the baseline.
- `data/shared/` holds `ific3074.mdb`, `ific3083.mdb`, `brific3075.mdb` and `brific3076.mdb`. Confirm the database names, since these come from the zips. Expected zip sizes: 60,837,631, 19,001,626, 3,427,636 and 2,406,450 bytes.

## Answer

Verified on Windows on 2026-11-07. The baseline was already committed at
`bc9c594` on `services-foundation-v2`, including the stray-text fix. Initial
working tree was clean; no extra baseline commit was needed.

Installed the locked dependencies with a project-local uv executable under
ignored `data/tools/`. Python 3.12.0 was already installed but not on PATH.
Baseline suite: **41 passed** (`.venv/Scripts/python.exe -m pytest`).

Downloaded all four archives once, checked ZIP CRC and Access headers through
`store_zip`, imported their provenance into `data/ops.db`, and copied the
verified MDBs to `data/shared/`. All archive sizes match the expected values.

| File | SHA-256 |
|---|---|
| ific3074.zip | c8adf75044bca9d6a2be4213d051805f1b0a88d716fb184bcb1cd812216da721 |
| ific3083.zip | 8c44121e9a3a7b1db10e76b7227d481e3b2464e65d22e0e827bf43fdab92c1df |
| brific3075.zip | 6f63a3dee548422cc722fd95adf7ce99f13abd8c786459d535c73e3320ed92f7 |
| brific3076.zip | ba9d0605a4a5494162e17650c290401d84deaef29030cbdbb61e4b00c834c4c2 |

The earlier macOS shared-data path in the plan is obsolete for this checkout;
use this repository's `data/shared/`. Data and local tooling remain ignored.
Tests were run in this clean baseline checkout, not a separate worktree.

Additional upstream verification: `schema.sql` creates all 12 tables in DuckDB.
