# CodeGuard frontend/backend integration status

Date: 2026-10-10

## Overall status

The frontend in `os_frontend/artifacts/codeguard` is connected to the existing FastAPI backend contract under `/api/v1` without redesigning the UI. The remaining Reports page and submission-detail integration work has been completed in the existing implementation.

The current backend process on `127.0.0.1:8000` is reachable, but it is running in database-unconfigured mode:

- `GET /api/v1/health` returned `200` with `database.configured=false`, `config_state=UNCONFIGURED_MISSING_URL`, and reason `DATABASE_URL is not set.`
- DB-backed endpoints correctly return `503 DATABASE_NOT_CONFIGURED` until the backend `.env` contains the real PostgreSQL password and migrations are applied.

No database password was requested or stored in the frontend.

## Features connected

- Backend status probe using `GET /api/v1/health`, `GET /api/v1/system/status`, and best-effort `/openapi.json` inspection.
- Problem catalog loading with honest database-unconfigured error handling.
- Custom execution flow using `POST /api/v1/submissions/run` when the database is configured.
- Judged submission flow using `POST /api/v1/submissions` plus polling `GET /api/v1/submissions/{id}`.
- Submission detail route `/submissions/:id` for:
  - local simulation records; and
  - numeric backend submission IDs.
- Submission detail data loading for:
  - submission detail;
  - combined analysis report; and
  - chronological behavior events.
- Reports page backend feed selector with on-demand loading for:
  - all faculty submissions;
  - security alerts; and
  - faculty dashboard data.
- Admin/monitoring dashboard reads real dashboard, alert, and trust-history responses where available.
- Existing local simulation history remains separate from backend records.
- Environment example added/verified for `VITE_API_BASE_URL=http://localhost:8000/api/v1`.

## API endpoints used by the frontend

Verified in the live OpenAPI path list:

- `GET /api/v1/health`
- `GET /api/v1/system/status`
- `GET /api/v1/problems`
- `POST /api/v1/submissions/run`
- `POST /api/v1/submissions`
- `GET /api/v1/submissions`
- `GET /api/v1/submissions/{submission_id}`
- `GET /api/v1/analysis/submissions/{submission_id}/report`
- `GET /api/v1/analysis/submissions/{submission_id}/events`
- `GET /api/v1/analysis/submissions/{submission_id}/fingerprint`
- `GET /api/v1/analysis/submissions/{submission_id}/sequence`
- `GET /api/v1/analysis/submissions/{submission_id}/hardcoding`
- `GET /api/v1/faculty/dashboard`
- `GET /api/v1/faculty/submissions`
- `GET /api/v1/faculty/alerts`
- `GET /api/v1/faculty/trust-history`

## Unsupported or limited features

- Real authentication/login and RBAC are not implemented by the backend; faculty routes are local-dev surfaces only.
- Network isolation is not implemented by the backend.
- Job Objects are not presented as a complete sandbox.
- CPU-time enforcement is displayed as measured-only when reported by the backend.
- `SECURITY_VIOLATION` and `ACCESS_DENIED` are reserved/not emitted according to backend documentation.
- DB-backed frontend flows cannot be fully end-to-end verified until `DATABASE_URL` is configured with the real PostgreSQL password and migrations are applied.

## Files changed

- `os_frontend/artifacts/codeguard/src/App.tsx`
  - Fixed incomplete JSX in the submission-detail page.
  - Reports page uses the verified `GET /api/v1/faculty/submissions` feed through the centralized client.
  - Submission detail renders real submission/report/event/test-result data and honest empty/error states.
- `os_frontend/artifacts/codeguard/src/lib/codeguard-api.ts`
  - Contains centralized typed API calls including `listAllSubmissions` for `GET /api/v1/faculty/submissions`.
- `os_frontend/artifacts/codeguard/.env.example`
  - Contains `VITE_API_BASE_URL=http://localhost:8000/api/v1` and no secrets.
- `os_frontend/INTEGRATION_STATUS.md`
  - This document.

## Environment configuration

Frontend `.env` location:

```powershell
D:\PROJECTS\OS\os_frontend\artifacts\codeguard\.env
```

Example value:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

The frontend `.env` is ignored by `os_frontend/.gitignore`. Do not put PostgreSQL credentials in frontend environment files.

## Start commands

Run backend from `D:\PROJECTS\OS`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run frontend from `D:\PROJECTS\OS\os_frontend`:

```powershell
$env:PORT='5173'
$env:BASE_PATH='/'
pnpm --filter @workspace/codeguard run dev
```

Build frontend from `D:\PROJECTS\OS\os_frontend`:

```powershell
$env:PORT='5173'
$env:BASE_PATH='/'
pnpm --filter @workspace/codeguard run build
```

## Commands run and results

Dependency install/check:

```powershell
pnpm install
```

Result: already up to date, completed successfully with pnpm `12.9.1`.

Type checks:

```powershell
pnpm --filter @workspace/codeguard run typecheck
pnpm run typecheck
```

Result: both passed.

Production build:

```powershell
$env:PORT='5173'; $env:BASE_PATH='/'; pnpm --filter @workspace/codeguard run build
```

Result: passed. Vite built `dist/public` successfully.

Backend/API checks performed:

- Port check found an existing listener on `127.0.0.1:8000`; no second backend was started.
- `GET http://127.0.0.1:8000/api/v1/health` returned `200` and `DATABASE_URL is not set.`
- `GET http://127.0.0.1:8000/api/v1/system/status` returned compiler, Job Object, worker, capability, and database status data.
- `GET http://127.0.0.1:8000/openapi.json` listed 21 paths including `GET /api/v1/faculty/submissions`.
- `GET /api/v1/faculty/submissions?page=1&page_size=10` returned `503 DATABASE_NOT_CONFIGURED`, confirming the route exists and is DB-backed.
- `POST /api/v1/submissions/run` with a small C program returned `503 DATABASE_NOT_CONFIGURED`, as expected in the current backend mode.

## Tests skipped or not fully run

- Browser UI walkthrough was not completed because the database is not configured; DB-backed feeds and submission execution correctly return `DATABASE_NOT_CONFIGURED`.
- C/C++ live execution, submission polling, submission-detail loading for a real stored submission, and dashboard data population remain pending until the backend database is configured.
- Backend pytest was not rerun in this session; historical results in backend docs were not re-claimed as newly executed.

## Database configuration still required

After adding the real PostgreSQL password to backend `.env`, run from `D:\PROJECTS\OS`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\seed_demo.py --run-submissions
```

Then restart the backend and reload the frontend.

## Final end-to-end verification steps

After database configuration:

1. Start backend and frontend with the commands above.
2. Confirm frontend status shows backend connected and database ready.
3. Load the problem catalog.
4. Run a small valid C program.
5. Run a small valid C++ program.
6. Try a compilation failure.
7. Try a runtime failure.
8. Try a safe wall-clock timeout sample.
9. Submit against a real problem and confirm polling reaches a terminal status.
10. Open `/submissions/{id}` and verify detail, report, events, limits, and test rows.
11. Open `/reports`, load each backend feed, and verify real rows/empty states.
12. Open `/admin` and verify dashboard, alerts, and trust-history states.
13. Stop the backend and verify the frontend shows backend-offline state without crashing.
