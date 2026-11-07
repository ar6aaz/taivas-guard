# 14 Final run, evaluation files and demo

Status: ready-for-agent
Type: task
Size: M
Blocked by: 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 15, 16
Owner: the coordinator

## Goal

Everything the brief's section 07 asks for, produced from the merged code in one clean run.

## Do

1. Merge finished branches in dependency order, resolving the one-line `space_rules()` conflicts, and run `uv run pytest` after each merge.
2. From an empty database, run the full stack with `docker compose up --build`, with `IFIC_MIN_ISSUE=3074`. Confirm that the cron-driven first sync downloads Space 3074 and Terrestrial 3076, that both reach `screen_status: done`, and that a digest is written to a file. Then repeat on another empty database using `POST /issues/upload` with the `.zip` for one and the `.mdb` for the other, and compare the results. They must match.
3. Export `outputs/ific_3074/` and `outputs/ific_3076/`, in Traficom's format if ticket 02 got an answer.
4. Check a sample of cases from each rule by hand, and report false positives and misses as far as they can be judged. Do not claim accuracy that was not measured.
5. Update the docs:
   - `docs/services-architecture.md` section 9: current status and gaps
   - `ARCHITECTURE.md`: record the DuckDB deferral and anything else that changed
   - A `README.md`: what it does, how to run it, and inputs and outputs, as the brief requests
6. Prepare the demo: a new issue being picked up, a case with evidence, an expert review, a draft letter, and the digest email.

## Acceptance

| Brief section 07 | Evidence |
|---|---|
| Demo | Rehearsed once from a clean start |
| Processes IFIC files using Finnish data | The clean run above |
| Processed 3074 and 3076 | Files in `outputs/`, with manifests |
| Input and output information | README and manifests |
| Code repository | Pushed, with the user's approval |
| Clarifying documentation | The docs above |

Every known limitation is stated in the README.

## Not in scope

New features. Pushing or submitting without the user's approval.
