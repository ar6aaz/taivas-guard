# 12 Review UI

Status: resolved
Type: task
Size: M
Blocked by: none
Owns: `frontend/`

## Goal

A Streamlit page where an expert can work through cases, as in ARCHITECTURE.md section 4.9. It talks only to the REST APIs, and never touches the database, so the services stay the single source of truth.

## Do

1. Ranked case list with filters for severity, rule, administration, status and issue.
2. Case detail: why it was raised, notices, bands, stations, comment deadline, then calculation inputs, assumptions, margin and citations where present. Handle `method: "screening"` cases with no numbers.
3. Accept or reject with a note, through `PATCH /cases/{id}`. Read the API key from the environment, and never show it.
4. A status strip from `GET /status` on the sync service: latest issue per domain, whether it is stale, and the next scheduled run.
5. An upload control that sends an `.mdb` or `.zip` to `POST /issues/upload`, then shows the issue being screened. Show the API's error text for a rejected file.
6. A clear statement that a case is a possible concern for review, unless it carries a calculated exceedance.
7. Build against the `details` contract in `map.md`, with sample data until tickets 07 and 10 merge.

## Acceptance

- Run it against live services loaded with IFIC 3083: list, open, review a case, and confirm via the API that the review was saved.
- An unavailable service shows a readable message, not a stack trace.
- Screenshots in your report.

## Not in scope

Log-in, the trends panel unless ticket 11 has merged, and editing draft letters until ticket 13 merges.

## Answer

Built as a React app in `frontend/`, not Streamlit: frontend decisions sit with this side of the team, by team agreement on 2026-11-07. Do not build `app.py`. It covers the goals above, including upload and the status strip, and was run against live services with real files. See `frontend/README.md`. New rules with no calculation appear without UI work; give a new rule a readable name in `RULE_LABELS` in `frontend/src/api/http.ts`.
