# IFIC Guard delivery: work distribution

Goal: finish the Traficom challenge deliverables with several agents working in parallel, without them colliding.

Read first: `ARCHITECTURE.md` (the analysis), `docs/services-architecture.md` (the services, current status, known gaps), `docs/challenge/` (the brief).

## Notes

### Tickets

Status values come from `docs/agents/triage-labels.md`. Size is a rough relative guide: S, M or L.

| # | Ticket | Owner | Size | Blocked by | Priority |
|---|---|---|---|---|---|
| 01 | Baseline: commit, fix stray text, fetch shared data | Done | S | none | Required |
| 02 | Ask Traficom the open questions | Human | S | none | Required |
| 03 | Space 3074 evaluation export | Done, merged | M | none | Required |
| 04 | M4 bands from Traficom's API | Done, `agent/04-m4-bands` | M | none | Required |
| 05 | Station loader and overlap rule | Done, merged | M | none | Required |
| 06 | pfd engine and limit masks | Done, by `ar6aaz` | L | none | Required |
| 07 | pfd rule on real filings | Done, merged in `integration/wave-3` | M | 06 | Required |
| 08 | Terrestrial adapter, diff and filter | Done, merged in `integration/wave-3` | L | none | Required |
| 09 | P.1546 engine and T/R 25-08 triggers | Done, `agent/09-p1546` | L | none | Required |
| 10 | Terrestrial findings and 3076 export | Done, merged in `integration/wave-3` | M | 03, 08, 09 | Required |
| 11 | Neighbour trends and anomalies | Done, merged in `integration/wave-3` | S | 08 | Required |
| 12 | Review UI | Done, React in `frontend/`, by `ar6aaz` | M | none | Wanted |
| 13 | Explanations and draft letters | Done, `agent/13-llm-drafts` | M | none | Required |
| 14 | Final run, evaluation files and demo | Done at `ba6f09a`, except for pressing Accept by hand before the demo; see ticket 18 for what's still open | M | all others | Required |
| 15 | Older IFIC issues | Done for version 10 only, merged in `integration/wave-3` | M | 03 | Required |
| 16 | Protect TX as well as RX frequencies | Done, merged in `integration/wave-3` | M | none | Required |
| 17 | AP30, AP30A and AP30B plan screening | Later, `needs-info` | L | none | Required by the brief, gap declared for now |
| 18 | Pending work before submission (checklist) | Coordinator | L | none | Mixed, see ticket |
| 19 | Link pfd results to special-interest stations | Open, on `develop` | L | none | Top priority for the user |
| 20 | Terrestrial criterion above 470 MHz | Open, on `develop` | S | none | Required: Traficom answered Q10 |

"Required" follows the brief: datasets marked `**` are mandatory, it asks for GSO and non-GSO pfd, the side quest including trends, and for all existing issues to be evaluable. A UI is not named in the brief.

### Order

The team set no deadline for this plan, so the aim is the full scope, in dependency order. Nothing is planned for cutting.

```
Now        02 (runs alongside everything; answers feed later tickets)
Wave 1     03  04  05  06  08  09  12  13   in parallel
Wave 2     07  10  11  15                   as their blockers finish
Wave 3     14
```

With fewer agents than tickets, start in this order: 03, 08, 09, 06, 04, 05, 13, 12. The evaluated issues come first, then the calculation that gives space cases their evidence.

### How agents work

The coordinator is one agent or person who assigns tickets, merges and updates this file.

1. **One worktree and branch per ticket:** `agent/NN-slug`, from the latest baseline. Commit there. Never commit to `main`, and do not push unless the coordinator says so.
2. **The coordinator assigns tickets.** Worktrees each hold their own copy of `.scratch/`, so self-claiming by editing a status file would not be seen by other agents. Report results in your final message; the coordinator records them here.
3. **Stay inside your ticket's "Owns" list.** If you need a change elsewhere, make the smallest additive change and name it in your report.
4. **Shared data is read-only.** `data/` and `files/` are git-ignored, so a worktree does not contain them. Use the shared copy with an absolute path: the `data/shared/` folder of your machine's main checkout (each machine keeps its own copy, so a worktree needs that checkout's absolute path), which holds `ific3074`, `ific3083`, `brific3075` and `brific3076`, each as `.zip` and `.mdb`. Do not download these from ITU again.
5. **Never send real email.** Keep `IFIC_EMAIL_BACKEND` at `console` or `file`.
6. **Do not use Docker Compose or fixed ports 8001-8003 in parallel work.** Use the test client, or pick an unused port. Ticket 14 runs the full stack.
7. **Do not invent regulatory values.** Limits, trigger values and clause numbers must come from a source you read, and carry a citation. If you cannot get the source, stop and report `needs-info`.

### Definition of done, every ticket

- `uv run pytest` passes, with new tests for the behaviour added.
- The ticket's acceptance checks were actually run, and the report gives their output.
- Assumptions are visible in the output, and in `Case.details` where they affect a result.
- Findings say "possible concern" unless a calculation shows a limit is exceeded.
- The report lists what changed, what was verified, what was not, and any contract changes.

### Contracts between tickets

**Adding a rule.** Implement `Rule` from `ific_guard/screening/rules.py` in a new module, and add one line to `space_rules()`. That one line is the only expected conflict, and the coordinator resolves it at merge.

**`Hit` and `Case`.** Do not add database columns. Put results in `details`, so that UI and LLM work is not blocked by schema changes.

**Keys in `Case.details`** for anything calculated. This follows the Finding record in ARCHITECTURE.md section 5. Tickets 07, 10, 12, 13 and 14 all depend on it:

```json
{
  "method": "pfd_gso | pfd_ngso | p1546 | screening",
  "calc_inputs": {},
  "assumptions": ["..."],
  "worst_point": {"lat": 0.0, "lon": 0.0, "angle_deg": 0.0},
  "value_db": 0.0,
  "limit_db": 0.0,
  "margin_db": 0.0,
  "mask_id": "...",
  "station_ids": ["..."],
  "citations": [{"doc": "RR", "clause": "Art. 21, Table 21-4", "page_or_url": "..."}],
  "source": {"domain": "space", "issue_no": 3074, "notice_ids": ["..."], "sha256": "..."}
}
```

Screening rules without a calculation use `method: "screening"` and leave the numeric keys out.

**Severity.** Tickets keep using `high`, `medium` and `low`. Scoring per ARCHITECTURE.md section 4.7 belongs to ticket 07, in one function that ticket 10 reuses.

### Who owns which code

| Path | Owner |
|---|---|
| `ific_guard/sync/`, `ific_guard/notifier/`, `ific_guard/service.py`, `ific_guard/events.py` | Stable. Change only to fix a bug |
| `ific_guard/screening/space.py` | 07, and 05 for additive reads |
| `ific_guard/screening/engine.py` | 10 (terrestrial routing) |
| `ific_guard/screening/m4.py` (new), `tools/`, `data/national/` | 04 |
| `ific_guard/screening/stations.py`, `rule_stations.py` (new) | 05 |
| `ific_guard/engines/pfd.py`, `data/masks/` (new) | 06 |
| `ific_guard/screening/rule_pfd.py`, `scoring.py` (new) | 07 |
| `ific_guard/terrestrial/` (new) | 08, 10, 11 in separate modules |
| `ific_guard/engines/p1546.py` (new) | 09 |
| `ific_guard/export.py`, `outputs/` (new) | 03 |
| `frontend/` | 12 |
| `ific_guard/llm/`, `data/refdocs/` (new) | 13 |

## Decisions so far

- **DuckDB is deferred.** Rules keep reading the `.mdb` through adapters until after submission, because no ticket needs the store to meet a deliverable. This departs from ARCHITECTURE.md section 4.2; ticket 14 records it there.
- **The evaluated issues are Space 3074 and Terrestrial 3076.** 3083 is for development only.
- **The terrestrial diff uses 3075 to 3076**, since the brief asks for consecutive issues.
- **Deterministic code decides.** The LLM only explains and drafts (ARCHITECTURE.md section 2).
- **Baseline verified (2026-11-07):** services and stray-text fix were already committed at `bc9c594`; 41 baseline tests pass. All four verified archives and MDBs are in this checkout's ignored `data/shared/`. See [ticket 01](issues/01-baseline.md). The old macOS shared-data path does not apply here.
- **Sequential implementation:** the user requested issues one by one. Work continues in the existing `services-foundation-v2` checkout without parallel agents. The user subsequently authorized committing and pulling this branch; nothing has been pushed. Ticket 02 remains a human question list; ticket 03 uses a clearly provisional export format.
- **Space 3074 export complete:** [ticket 03](issues/03-space-3074-export.md) produced 51 possible concerns (10 high, 41 medium), identical CSV/JSON across two runs, five source-row checks, and 45 passing tests. Export re-evaluates the source artifact so mutable cross-issue review state cannot contaminate historical results. AP30-family coverage remains a declared gap.
- **Integration branch:** `integration/wave-2` adds 04, 09 and 13 to `services-foundation-v2`, which already has 03, 05, 06 and 12.
- **The deadline does not constrain this plan** (team decision, 2026-11-07).
- **Issues arrive two ways:** from ITU on a cron schedule, or uploaded as an `.mdb` or a `.zip` containing one. Both are built (`IFIC_SYNC_CRON`, `POST /issues/upload`).
- **Terrestrial neighbours are Russia, Sweden, Estonia, Norway and Denmark** (team decision). ITU codes `RUS`, `S`, `EST`, `NOR`, `DNK`, confirmed in `ref_adm`, in setting `neighbour_adms`. Sweden's code is `S`, not `SWE`. Treated as a terrestrial scope only, because the brief says a satellite from anywhere can reach Finland.

- **Wave 2 merged (2026-11-07):** `integration/wave-3` has 07, 08, 10, 11 and 15, with ticket 10's scoring placeholder replaced by `scoring.py`. 534 tests pass with `IFIC_SHARED_DATA` set. Not pushed. Tracked `outputs/` predate some of this and ticket 14 regenerates them. 07 and 10 were each recomputed once by the coordinator from source rows.
- **Space database versions:** only version 10 screens. Versions 8, 7 and 5 exist (samples 2925, 2809, 2559) and are rejected with a clear error, so "all existing IFICs" is not yet met. Coordinator's recommendation, awaiting the user's decision.
- **Open questions added for ticket 02:** whether T/R 25-08 applies on the Russian border and any Finland-Russia agreement; the criterion at 793-795 MHz; 10% or 1% time; whether notifier-declared FIN coordination keeps 5 notices at medium; a height for mobile stations with none; the `pwr_ds_max` unit and `orbit_lnk`, `long_asc`, `phase_ang` meanings from the primary Space Preface; which columns supply the X and Y mask parameters (5,429 records unevaluated).
- **TX frequencies protected:** [ticket 16](issues/16-protect-tx-frequencies.md) adds no cases on 3074 or 3076, but 3074's station links rise from 2,839 to 4,975, and two 3076 cases rise from low to medium. The user decided a TX-only match may raise severity, so they stay at medium (2026-11-07).

## Fog

- **Ticket 03 follow-up completed:** issue-local `case_occurrence` snapshots now persist during screening. Processing-order and re-screening tests pass; 68 tests total. Real backfill retained 51 occurrences for 3074 and 39 for 3083. Ticket 08 adapter work is in progress.

- **Evaluation file format** for 3074 and 3076 (ticket 02). Ticket 03 builds a provisional format.
- **What Sweden, Norway and Denmark contribute.** In Terrestrial 3075, 3076 and 3083, none of them notifies any station in `fxm_terra` or `fmtv_terra`. Only Russia and Estonia do. See ticket 08.
- **Whether teammates are already building** the pfd engine, P.1546 or the UI. If so, remove those tickets rather than duplicating.
- **Whether Octave is acceptable** for P.1546 in the demo environment (ticket 09).
