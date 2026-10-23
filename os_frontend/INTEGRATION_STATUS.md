# CodeGuard frontend/backend integration status

Date: 2026-10-22 (updated)

## Overall status

The React frontend in `os_frontend/artifacts/codeguard` is connected to the existing FastAPI
backend (`/api/v1`) without redesigning the UI. The dark/neon design, grid, typography, Monaco
editor, sidebar/tabs/cards, animations and routes are unchanged; all data on screen comes from
real API responses. No mock live data is mixed with real telemetry, and no endpoint or field is
invented.

This session verified the app against a **live, database-configured backend**:

- `GET /api/v1/health` → `database.configured=true`, `config_state=CONFIGURED`, `connectivity=REACHABLE`
- Custom run (`POST /submissions/run`) → real compiled/ executed result rendered (exit code, wall/CPU
  time, peak memory, `spawn_ms`, compiler banner, stdout, per-limit ENFORCED/MEASURED_ONLY states)
- Judged submission (`POST /submissions` + `GET /submissions/{id}` polling) → terminal
  `WRONG_ANSWER`, `0/6 passed`, per-test rows with hidden tests masked
- Problem manager CRUD round trip (create → appears in list → preview → delete) against real rows
- The database-unconfigured path (503 `DATABASE_NOT_CONFIGURED`) and the offline path
  (backend stopped) were exercised earlier in this integration and remain handled honestly

No database password was requested, stored, or exposed by the frontend.

## Features connected

| Route | Behaviour |
| --- | --- |
| `/compiler` | Monaco workspace. Problem catalog from `GET /problems`; Run → `POST /submissions/run`; Submit → `POST /submissions` + polling; signals tab renders the stored report; Run/Submit disabled while the backend is unreachable |
| `/submissions` | All stored submissions (`GET /faculty/submissions`) with search, status filter, pagination, multi-select |
| `/compare` | Pairwise comparison via `GET /analysis/fingerprints/compare` for selected submissions |
| `/alerts` | Security alerts (`GET /faculty/alerts`) |
| `/analytics` | Behavior analytics from submission reports |
| `/trust` | Trust/risk history (`GET /faculty/trust-history`) |
| `/problems` | Problem manager: list/create/update/delete, preview dialog, test-case manager (list/add/delete) |
| `/admin` | Faculty dashboard metrics, alerts, trust history, capability table, warnings, DB state |
| `/simulation` | Unchanged deterministic simulator, always labelled `SIMULATION / DEMO DATA` |
| `/health` | System health: compilers, worker, capabilities, warnings, DB state, retry |
| `/reports` | Local simulation table + on-demand backend feeds (submissions / alerts / dashboard) |
| `/submissions/:id` | Local simulation record, or the stored backend submission with report + events + test results |

Top bar always shows backend reachability (`CONNECTED` / `DISCONNECTED`) and database state
(`DB READY` / `DB UNAVAILABLE`) separately, plus a manual retry button.

## API endpoints used by the frontend

Single typed client: `src/lib/codeguard-api.ts` (typed DTOs in `src/lib/api-types.ts`).

- `GET /api/v1/health`, `GET /api/v1/system/status`, best-effort `GET /openapi.json`
- `GET /problems`, `GET /problems/{id}`, `POST /problems`, `PUT /problems/{id}`, `DELETE /problems/{id}`
- `GET /problems/{id}/test-cases`, `POST /problems/{id}/test-cases`, `DELETE /problems/{id}/test-cases/{caseId}`
- `POST /submissions/run`, `POST /submissions`, `GET /submissions`, `GET /submissions/{id}`
- `GET /faculty/submissions`, `GET /faculty/alerts`, `GET /faculty/dashboard`, `GET /faculty/trust-history`
- `GET /analysis/submissions/{id}/report`, `/events`, `/fingerprint`, `/sequence`, `/hardcoding`
- `GET /analysis/fingerprints/compare`

## Unsupported / partial

- Per-limit enforcement states come from the backend; `cpu time` stays `MEASURED_ONLY` and is shown
  as such. Job Objects are not a full sandbox — the UI repeats that disclaimer.
- Sequence/hardcoding/trust panels render "No analysis returned" until the backend actually returns
  a report for that submission; nothing is synthesized.
- Local-demonstration workspaces (browser-local simulations) stay clearly labelled `SIMULATION /
  DEMO DATA` and are never mixed with backend records.
- Local-dev mode has no authentication (the problem manager states this); RBAC is backend work.
- A frontend hosted on Replit cannot reach `http://localhost:8000` without a public URL or tunnel.

## Files changed (this session)

- `os_frontend/artifacts/codeguard/src/App.tsx`
  - Applied the CSS hooks the design already defined but JSX never used (`emptyState`,
    `contractHint`, `problemFooter`, `sectionToggle`, `tabRow(s)`, `searchBox`), so empty states,
    footers, toggles and the workspace tab strip render at their intended sizes.
  - Problem manager table: `problem-table-head` / `problem-row` / `problem-actions` / `report-table`
    classes so the six-column table lays out correctly (the generic report row defines five columns).
  - `test-case-manager` class on the test-case dialog body.
  - Added missing container styles (`form`, `preview`, `sampleTests`, `testCaseManager`).
- `os_frontend/artifacts/codeguard/src/index.css`
  - New section styling the problem-manager hooks with existing tokens only: `.empty-icon`,
    `.report-table`, `.problem-*`, `.difficulty-badge`, `.modal-*`, `.form-*`, `.preview-*`,
    `.sample-test*`, `.test-case-manager`, `.console-prompt`, plus a mobile rule.

Previously committed integration work (this repository's HEAD): the typed API client, DTO file,
Run/Submit wiring, submission detail route, reports feed, admin dashboard, `.env.example`, and
`.env` entries in `os_frontend/.gitignore`.

## Verification

Commands run in this session (all from `D:\PROJECTS\OS\os_frontend\artifacts\codeguard`):

```
pnpm exec tsc -p tsconfig.json --noEmit     # exit 0
PORT=5173 BASE_PATH=/ pnpm exec vite build  # exit 0, dist/public written
```

Browser verification (headless Edge driving `http://localhost:5173` against the backend on
`http://127.0.0.1:8000`), with screenshots:

- Compiler page: catalog loads (4 problems), no catalog error, footer/empty-state typography fixed
- Run: `ACCEPTED`, submission #42 custom run, `EXIT 0`, `WALL 46 ms`, `CPU 30 ms`, `PEAK MEM 5.2 MB`,
  `SPAWN 141 ms`, `COMPILE ok · gcc 6.3.0 · 2329 ms`, real stdout, limits row with `cpu time=MEASURED_ONLY`
- Submit: polled to terminal `WRONG_ANSWER`, `0/6 passed`, public/hidden per-test rows
- Submission detail `/submissions/43`: status, metrics, 6 test rows, 12 events, real fingerprint
- Reports feed: 10 real backend submission rows, no feed errors
- Problem manager: table layout correct, create → save → list, preview dialog, test-case dialog
  (6 cases) and add-test-case form; created problem deleted afterwards (DB left unchanged)
- No page/JS errors in any run; Monaco mounts (its AMD loader is fetched from jsdelivr —
  pre-existing behaviour, unchanged)

Earlier in this integration (same codebase): database-unconfigured mode (503 surfaces on catalog,
Run/Submit, dashboard, feeds, submission detail), backend offline mode (chip `DISCONNECTED`, app
still renders, Run/Submit disabled, retry works, no mock fallback), per-workspace and per-program
state isolation, and localStorage persistence across reloads.

Backend tests were last run in the backend phase of this project: `pytest -q` → 99 passed /
4 skipped, and `pytest -m windows` → 22 passed. They were not re-run in this session.

## API base configuration

`os_frontend/artifacts/codeguard/.env.example`:

```
VITE_API_BASE_URL=http://localhost:8000/api/v1
PORT=5173
BASE_PATH=/
```

Copy to `.env` (git-ignored) for local overrides. The client normalises the value, so both
`http://localhost:8000` and `http://localhost:8000/api/v1` resolve to exactly one `/api/v1` prefix.
Never put the PostgreSQL password in frontend env vars — the backend reads `DATABASE_URL`.

## Running both apps

Backend (Python 3.12 venv, port 8000):

```
cd D:\PROJECTS\OS
.\.venv\Scripts\python.exe -m alembic upgrade head          # first run only
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend (pnpm workspace, port 5173):

```
cd D:\PROJECTS\OS\os_frontend
pnpm install                                                  # Windows: pnpm install --ignore-scripts (preinstall uses sh)
cd artifacts\codeguard
$env:PORT='5173'; $env:BASE_PATH='/'; pnpm exec vite --host 127.0.0.1
```

CORS: the backend allowlist already includes `http://localhost:5173`; no frontend URL is hardcoded
in the backend.
