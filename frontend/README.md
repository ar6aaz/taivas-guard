# IFIC Guard frontend

The review UI for IFIC Guard: the latest ITU release and its results, checking a file on demand, run history, and expert review of cases. Written in React and TypeScript, and it talks to the services in `ific_guard/`.

## Run it

Start the services first (see `docs/services-architecture.md`, section 8). They need Python below 3.14:

```bash
uv sync --python 3.12
cp .env.example .env                 # then set IFIC_API_KEY
uv run python -m ific_guard.cli init-db
uv run uvicorn ific_guard.sync.app:app --port 8001
uv run uvicorn ific_guard.screening.app:app --port 8002
```

Then the frontend:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, live services
npm run dev:sample   # sample data, no backend needed
npm test             # API mapping, file checks, pfd maths
npm run build        # type-check and production build
```

## How it reaches the services

The services don't allow cross-origin calls, so the Vite dev server proxies them:

| Browser path | Service | Override |
|---|---|---|
| `/api/sync/*` | `http://localhost:8001` | `IFIC_SYNC_URL` |
| `/api/screening/*` | `http://localhost:8002` | `IFIC_SCREENING_URL` |

**The API key stays on the server.** The proxy adds `X-API-Key` from `IFIC_API_KEY`, read from the environment or the repo's `.env`. It never reaches the browser, but anyone who can open the frontend can save reviews, so keep the port private. Real use needs sign-in and a production proxy; this only covers development and the demo.

Without a matching key the app still reads, and saving shows an error that explains the fix.

## What maps to what

`src/api/http.ts` is the only file that knows the API's shapes. Pages use the `Api` interface in `src/api/types.ts`.

| UI | API | Notes |
|---|---|---|
| Latest issue, Runs | `GET /issues`, `/status`, `/runs` on sync | Revisions of an issue collapse to the newest |
| Findings list | `GET /cases?limit=1000` on screening | Filtered and ranked in the browser, because the API has no issue filter |
| Finding detail | `GET /cases/{id}` | Reads the `Case.details` keys in `.scratch/ific-guard-delivery/map.md`, including calculated results once rules provide them |
| Accept, reject | `PATCH /cases/{id}` | |
| Analyse a file | `POST /issues/upload` on sync | The browser checks the file first and shows upload progress, then the run page follows `screen_status` |
| Check ITU now | `POST /sync` | |
| Screen it again | `POST /issues/{id}/screen` | Offered when a chosen file matches a stored issue's SHA-256 |

A case belongs to every stored issue from `first_issue` to `last_issue`, because the API doesn't record exactly which issues it appeared in. For an exact per-issue result, use `python -m ific_guard.cli export`.

## Gaps in the API that affect the UI

Each gap shows as a clear message or a missing control, not invented data.

| Gap | What the UI does |
|---|---|
| No case history or reviewer identity | Shows the last-updated time only |
| No letter field | Generates a draft from a fixed template; edits are saved in that browser only |
| No confidence or funnel data | Hides confidence, and shows cases by rule instead of a funnel |
| Release date missing after an import or upload | Shows "Not recorded", and the monitor can report the domain as overdue |

## Sample mode

`npm run dev:sample` uses `src/api/mock.ts`: real results from a one-off screen of IFIC 3083, 3074 and 3076, with a banner on every page. Upload is simulated there, and says so. To rebuild the data, see `scripts/export_sample.py` (needs `access-parser pandas openpyxl`).

## Not built yet

- Notifications view (`GET /notifications` on the notifier)
- Comparing issues, and neighbour trends for the terrestrial side quest
- Letters as `.docx`, and AI-written explanations
