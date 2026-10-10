# CodeGuard — Behavior-Aware Secure Online Judge (Windows OS-Level Isolation)

Backend for **CodeGuard**: an online judge that compiles and runs student C/C++
submissions on Windows under **Job Object** resource limits, records what the
program *did* while it ran (processes, files, limits), and turns that record
into four analysis signals: a **behavior fingerprint**, **sequence-aware event
analysis**, a **hardcoding/overfitting heuristic**, and an **adaptive trust
score** for faculty review.

The frontend is developed separately (Replit). This repository contains the
API, the execution engine, migrations, tests, and the docs.

> **Honesty rule used throughout this repo:** every limit reports its real
> state — `ENFORCED` (the kernel/engine actually stops it), `MEASURED_ONLY`
> (we observe and report it, we do not reliably stop it), `UNAVAILABLE`,
> `RESERVED`. Nothing is described as enforced unless it was verified on this
> machine. See [Feature status](FEATURE_STATUS.md) and
> [Truthful capability table](#truthful-capability-table).

---

## Contents

- [Requirements](#requirements)
- [Quick start (PowerShell)](#quick-start-powershell)
- [Configuration](#configuration)
- [Run the server](#run-the-server)
- [Tests](#tests)
- [Seed demo data](#seed-demo-data)
- [API documentation](#api-documentation)
- [Architecture](#architecture)
- [Execution model](#execution-model)
- [Truthful capability table](#truthful-capability-table)
- [Honest limitations](#honest-limitations)
- [Environment observations](#environment-observations)
- [Demo scenarios](#demo-scenarios)
- [Troubleshooting](#troubleshooting)

---

## Requirements

| Component | Verified on this machine |
| --- | --- |
| OS | Windows 11, 10.0.26200 (x64). No Docker/WSL/containers/Linux paths anywhere. |
| Python | 3.12.5 (venv at `.venv\`) |
| Key libs | FastAPI 0.143.0, Uvicorn, Pydantic v2, SQLAlchemy 2.x, Alembic, psycopg (PostgreSQL driver), pytest 9.1.1, httpx, psutil, pywin32, python-dotenv/pydantic-settings |
| Compiler | GCC 6.3.0 (MinGW) at `C:\MinGW\bin\gcc.exe` / `g++.exe`, found on `PATH`. **Never auto-downloaded** — set `COMPILER_C_PATH`/`COMPILER_CPP_PATH` if yours lives elsewhere. |
| Database | PostgreSQL 17, database `codeguard`, `localhost:5432`, user `postgres`. **Password is supplied by you in `.env` — it is not in this repo.** |

Check the execution prerequisites at any time:

```powershell
.\.venv\Scripts\python.exe scripts\check_compiler.py
```

Prints compiler discovery, Job Object availability, and whether the process is
elevated (CodeGuard is designed to run as a **normal user**; student code is
never executed as administrator). Exit code `0` = prerequisites OK.

---

## Quick start (PowerShell)

Run everything from the repository root (`D:\PROJECTS\OS`).

```powershell
# 1) Virtual environment + dependencies (once)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) Configuration (once)
copy .env.example .env
#    -> edit .env and replace YOUR_PASSWORD with your real PostgreSQL password
#    -> .env is git-ignored; never put credentials in .env.example

# 3) Database schema (needs a reachable database)
.\.venv\Scripts\python.exe -m alembic upgrade head

# 4) Verify execution prerequisites (compilers, Job Objects, elevation)
.\.venv\Scripts\python.exe scripts\check_compiler.py

# 5) Start the API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 6) Test suite (in another terminal)
.\.venv\Scripts\python.exe -m pytest -q

# 7) Demo data (after step 3)
.\.venv\Scripts\python.exe scripts\seed_demo.py
.\.venv\Scripts\python.exe scripts\seed_demo.py --run-submissions
```

### Without a database password (current state of this machine)

If `DATABASE_URL` is missing or still the `YOUR_PASSWORD` placeholder, the app
starts in an explicitly labelled **database-unconfigured development mode**:

- `GET /api/v1/health` and `GET /api/v1/system/status` return `200` and report
  `database.configured=false`, `config_state` (`UNCONFIGURED_MISSING_URL` or
  `UNCONFIGURED_PLACEHOLDER`), and the reason.
- Every DB-dependent endpoint (`/problems`, `/submissions`, `/analysis/*`,
  `/faculty/*`) returns **`503`** with the standard error envelope and a clear
  message.
- There is **no SQLite/in-memory fallback and no silent retry loop** — by
  design. The connection is probed lazily with a TTL cache, never at startup.

That mode is what this repo currently runs in; DB-dependent tests are marked
and skip with a "pending credentials" reason.

---

## Configuration

All settings live in `.env` (copy from [`.env.example`](.env.example), which is
commented line-by-line). Highlights:

| Variable | Meaning |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://postgres:PASSWORD@localhost:5432/codeguard` |
| `CORS_ORIGINS` | Comma-separated browser origins for the separate frontend. **No hardcoded frontend URL anywhere in the code.** |
| `SANDBOX_ROOT` | Root for per-run workspaces (default `./sandbox`). Cleanup never leaves this root. |
| `DEFAULT_EXECUTION_TIMEOUT_SECONDS`, `DEFAULT_MEMORY_LIMIT_MB`, `DEFAULT_ACTIVE_PROCESS_LIMIT` | Fallback limits when a problem doesn't define its own. |
| `MAX_ALLOWED_*` | Hard upper bounds; problem creation validates against them. |
| `MAX_CONCURRENT_EXECUTIONS`, `MAX_QUEUED_JOBS`, `RUN_WAIT_TIMEOUT_SECONDS` | Bounded judge concurrency/queue. |
| `COMPILER_C_PATH`, `COMPILER_CPP_PATH`, `COMPILER_C_FLAGS`, `COMPILER_CPP_FLAGS` | Compiler discovery/flags (`-O2 -std=c11`, `-O2 -std=c++14`). |
| `SEQUENCE_RULES_PATH` | Optional JSON file overriding the built-in sequence rules. |
| `AUTH_MODE` | `local_dev` (current): **no credentials are verified**; server must stay bound to `127.0.0.1`. |

---

## Run the server

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verified boot on this machine (`--port 8123`):

- `GET /api/v1/health` → `200`
- `GET /api/v1/system/status` → `200` (capabilities, warnings, compiler/job/worker state)
- `GET /docs` → `200`, `GET /openapi.json` → `200` — **21 paths, 27 operations**
- Unknown route → `404` in the standard error envelope with a `request_id`

Interactive docs: <http://127.0.0.1:8000/docs>

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q                # whole suite
.\.venv\Scripts\python.exe -m pytest -m windows -q     # Windows-only engine tests
.\.venv\Scripts\python.exe -m pytest -m db -q          # DB tests (skip until .env has a password)
```

**Results actually executed on this machine:**

| Command | Result |
| --- | --- |
| `pytest -q` | **99 passed, 4 skipped** (5–9 s) |
| `pytest -m windows -q` | **22 passed** |
| `pytest -m db -q` | **4 skipped** — `PENDING: PostgreSQL credentials are not configured yet …` |

The 4 skipped tests are DB lifecycle tests; they are never rerouted to another
database. Nothing in this repo claims a live PostgreSQL connection has been
verified.

---

## Seed demo data

```powershell
.\.venv\Scripts\python.exe scripts\seed_demo.py                   # problems only
.\.venv\Scripts\python.exe scripts\seed_demo.py --run-submissions # + 6 real judged runs
```

- Creates the demo problem **A + B** (`a-plus-b`) with deterministic public
  cases (`2 3 -> 5`, `10 10 -> 20`) plus hidden/generated cases.
- `--run-submissions` compiles and executes six fixture programs and prints
  their real statuses, e.g. `sum_correct.c -> ACCEPTED`,
  `infinite_loop.c -> TIME_LIMIT_EXCEEDED`,
  `process_spawner.c -> PROCESS_LIMIT_EXCEEDED`.
- Exit codes: `0` success, `2` database not configured/reachable (prints
  "No SQLite/in-memory fallback"), `3` schema missing (prints the alembic
  command).

---

## API documentation

- Interactive: `/docs` · Raw spec: `/openapi.json`
- Integration guide for the frontend: [`API_INTEGRATION.md`](API_INTEGRATION.md)
- What works, what is partial, what is not implemented: [`FEATURE_STATUS.md`](FEATURE_STATUS.md)

Everything lives under `/api/v1`:

```
/api/v1/health                       GET    app + database health
/api/v1/system/status                GET    truthful capability report (?refresh=true re-probes)
/api/v1/problems                     GET/POST                    CRUD + test cases
/api/v1/problems/{id}                GET/PATCH/DELETE
/api/v1/problems/{id}/test-cases     GET/POST/DELETE             (hidden tests included)
/api/v1/submissions                  GET/POST                    submit = 202 + polling
/api/v1/submissions/run              POST                        custom stdin, waits for the result
/api/v1/submissions/{id}             GET                         full detail incl. per-test results
/api/v1/analysis/submissions/{id}/fingerprint|events|sequence|hardcoding|report
/api/v1/analysis/fingerprints/compare                            GET
/api/v1/faculty/dashboard|submissions|alerts|trust-history|reports/{label}
/api/v1/faculty/alerts/{id}          GET/PATCH                   faculty review decisions
```

---

## Architecture

```
app/
  api/v1/            routers: health, system, problems, submissions, analysis, faculty
  core/              config (DATABASE_URL state machine), constants, auth seam, logging, exceptions
  db/                Base + engine/session (lazy probe, TTL cache, no startup connect)
  models/            Problem, TestCase, Submission, TestResult, BehaviorFingerprint,
                     BehaviorEvent, SecurityAlert, TrustHistory  (UTC timestamps, indexes, FKs)
  repositories/      problem, submission, alert, stats
  schemas/           Pydantic v2 response/request models (real enums in OpenAPI)
  services/          the engine + the four novelty modules
  workers/           execution_worker: queue -> judge -> persist (+ judge_details_json)
migrations/          Alembic (0001_initial, verified with `--sql` offline generation)
scripts/             check_compiler.py, seed_demo.py
tests/               99 executable tests + 4 db-marked pending tests, fixtures/programs/*.c(pp)
```

Execution pipeline for one submission:

```
POST /submissions (202)
  -> bounded queue (MAX_QUEUED_JOBS; full queue = clear rejection, not unbounded growth)
  -> execution_worker (bounded MAX_CONCURRENT_EXECUTIONS, no event-loop blocking)
      -> compile (arg-list subprocess, never shell string concatenation)
      -> per-test run_program():
            create Job Object -> write stdin -> spawn (CREATE_NO_WINDOW,
            breakaway fallback) -> assign to job -> capture stdout/stderr with
            byte cap -> ResourceMonitor sampling -> WaitForSingleObject deadline
            -> TerminateJobObject -> cleanup -> measurements
      -> behavior events + fingerprint + sequence analysis + hardcoding heuristic
      -> adaptive trust update (deduped, auditable, never weakens core limits)
      -> persist Submission/TestResult/BehaviorEvent/... + judge_details_json
  -> GET /submissions/{id} or /analysis/... reads the stored record
```

---

## Execution model

Per-problem limits (`time_limit_ms`, `memory_limit_mb`, `process_limit`,
`max_output_bytes`) are resolved into an `ExecutionPolicy` that is **never**
influenced by the trust score — trust only chooses monitoring/reporting
behaviour, never the limits.

Subprocess handling: compiler and student programs are launched with
argument lists only (`subprocess.Popen([...])`), never through `shell=True`
or any string concatenation of untrusted input.

---

## Truthful capability table

Measured on this machine (evidence from the test suite and from direct
experimentation; the same numbers appear in `GET /api/v1/system/status`).

| Limit / control | Mechanism | Reported state | Evidence |
| --- | --- | --- | --- |
| Wall-clock deadline | `WaitForSingleObject(timeout)` + `TerminateJobObject(exit 42)` | **ENFORCED** | Busy loop stopped at the deadline, `timed_out=true`, exit 42, classified `TIME_LIMIT_EXCEEDED` |
| Output size | Drain thread caps at `MAX_OUTPUT_SIZE_BYTES`, then `TerminateJobObject(exit 44)` | **ENFORCED** | Output storm captured exactly `1048576` bytes, exit 44, classified `RUNTIME_ERROR` ("Output limit exceeded") |
| Memory | `JOB_OBJECT_LIMIT_JOB_MEMORY` (job-wide commit cap) | **ENFORCED** when the job is assigned, otherwise `MEASURED_ONLY` | 64 MB budget: the program itself observed refused allocations (`malloc stopped after 48 MB`) and exited 2 -> `MEMORY_LIMIT_EXCEEDED` |
| Active processes | Kernel cap `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` set to `2*limit+4` (conhost headroom) **plus** a sampling monitor that counts only student processes and terminates the tree at `limit` | **ENFORCED** when the job is assigned, otherwise `MEASURED_ONLY` | Process spawner terminated with exit 43, `kill_reason=process_limit` -> `PROCESS_LIMIT_EXCEEDED` |
| Child-process cleanup | `TerminateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + orphan sweep | **ENFORCED** when the job is assigned, else `PARTIAL` | Children observed `2-3 -> 0` after cleanup |
| **CPU time** | `JOB_OBJECT_LIMIT_JOB_TIME` is configured, **but** repeated experiments showed it firing late or not at all | **MEASURED_ONLY** (CPU-bound code is stopped by the wall-clock deadline) | Control with no job: never killed. Job without a CPU limit: never killed. Job with a 500 ms budget: killed in 1/3 runs at ~1.1 s CPU, while 2/3 runs burned **5.9 s of CPU** against the 500 ms budget |
| Filesystem isolation | Per-run workspace under `SANDBOX_ROOT` + deny-ACL helper + workspace file-event observation | **PARTIAL** | Real ACL denial test passes; but there is **no filesystem virtualization** and `ACCESS_DENIED` events are reserved, not emitted |
| Network isolation | — | **NOT_AVAILABLE** | Job Objects do not restrict network access; nothing else blocks it |
| `SECURITY_VIOLATION` status | — | **RESERVED** | The enum value exists; no detector emits it |
| Admin elevation | `running_as_admin` is reported by `/system/status` | Reported truthfully | Student code is never spawned elevated |

---

## Honest limitations

1. **Job Objects are not a full sandbox.** They give real kernel resource
   limits and tree kill guarantees — not filesystem virtualization, not network
   isolation, not registry/system-call filtering.
2. **CPU time is not reliably enforced by the kernel on this machine** (see the
   table above). CPU burners are still stopped by the wall-clock deadline, and
   CPU usage is measured and reported.
3. **Short-lived child processes can be missed** between monitor polls; the
   kernel cap at `2*limit+4` is the hard backstop for runaway process counts.
4. **`ACCESS_DENIED` and `SECURITY_VIOLATION` are reserved, not emitted.**
5. **`AUTH_MODE=local_dev` verifies no credentials.** The actor seam
   (`X-Workspace-Label`, `X-Actor-Role`) and RBAC-ready structure exist; real
   login/roles do not. Keep the server on `127.0.0.1`.
6. **Analysis outputs are assistive signals**, not verdicts: sequence matches
   and hardcoding findings explain themselves for human review, and hardcoding
   `confidence` is always `null` by design. Trust scope is a local
   `workspace_label`, which is not a verified student identity.
7. **Database-dependent functionality is unverified on this machine** until a
   real password is in `.env`: live `alembic upgrade head`, DB lifecycle tests
   (4 pending), and DB-backed endpoints (503).

---

## Environment observations

Two Windows-specific behaviours were measured here and are handled explicitly:

1. **Antivirus first-use analysis stalls `CreateProcess`.** Windows Defender
   real-time protection is enabled. Launching a *freshly compiled* student
   binary from the project sandbox was observed to block inside `CreateProcess`
   for **1.7 s (simple program) up to 12–17 s (output-heavy program)**, while
   the same binary launched from `%TEMP%` took 0–16 ms. Re-launching the same
   `.exe` is instant.
   - This stall happens **before** the process exists, so it does **not**
     consume the student's execution budget: the resource deadline starts only
     once the process is running.
   - It is reported separately as `spawn_ms` (in the `PROCESS_START` event and
     in the report's `resource_measurements.spawn_ms`), and `duration_ms`
     measures execution only.
   - Mitigation (optional, requires administrator): add a Defender exclusion
     for the repository / `SANDBOX_ROOT` path. CodeGuard never asks for
     elevation itself.
2. **`conhost.exe` appears in every console job.** Windows attaches a console
   host to each console process; CodeGuard excludes infrastructure processes
   from the student process count so a legitimate program is never punished for
   it.

---

## Demo scenarios

**A. Full walkthrough (requires the database password)**

1. `.\.venv\Scripts\python.exe scripts\check_compiler.py` → prerequisites OK.
2. `.\.venv\Scripts\python.exe -m alembic upgrade head` → tables created.
3. `.\.venv\Scripts\python.exe scripts\seed_demo.py --run-submissions` → problem + 6 judged fixtures.
4. Start uvicorn, open `/docs`, then:
   - `GET /api/v1/faculty/dashboard` → aggregates over the seeded rows.
   - `GET /api/v1/faculty/trust-history?workspace_label=demo-workspace` →
     trust deltas with reasons (`CLEAN_ACCEPTED`, `TIME_LIMIT_VIOLATION`,
     `PROCESS_LIMIT_VIOLATION`, `SEQUENCE_ALERT_MEDIUM`, ...).
   - `GET /api/v1/analysis/submissions/{id}/report` → combined report with the
     limitation list.

**B. Judge a submission by hand**

```powershell
# 1) create a problem + its test cases (bodies in /docs)
$body = @{
  problem_id    = 1
  language      = "c"
  source_code   = "#include <stdio.h>`nint main(){long a,b;scanf(`"%ld %ld`",&a,&b);printf(`"%ld\n`",a+b);return 0;}"
  workspace_label = "student-42"
} | ConvertTo-Json

# 2) submit -> 202 + Location header
$acc = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/submissions `
  -ContentType "application/json" -Body $body

# 3) poll until the status is terminal
do {
  Start-Sleep -Milliseconds 500
  $sub = Invoke-RestMethod -Uri "http://127.0.0.1:8000$($acc.detail_url)"
} while ($sub.status -in @('QUEUED','COMPILING','RUNNING','ANALYZING'))
$sub.status            # e.g. ACCEPTED

# 4) inspect the recorded behavior
$events = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/analysis/submissions/$($sub.id)/events"
$events.items | Select-Object event_type, source, severity
```

**C. No database at all (works today)**

1. Start uvicorn → `GET /api/v1/system/status` reports capabilities truthfully.
2. `POST /api/v1/submissions` → `503` with the error envelope, not a crash.

**D. Custom run with custom input**

`POST /api/v1/submissions/run` executes the program with the given stdin, waits
for the result, and does not touch trust scoring.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `503 database-unconfigured` on data endpoints | `DATABASE_URL` missing or still `YOUR_PASSWORD` in `.env` |
| `alembic` "schema missing" from `seed_demo.py` | Run `.\.venv\Scripts\python.exe -m alembic upgrade head` first |
| Compilation fails: `gcc not found` | Add MinGW to `PATH` or set `COMPILER_C_PATH` in `.env` (`scripts\check_compiler.py` diagnoses) |
| Tests hang or the judge is slow | Antivirus first-launch scan (see [Environment observations](#environment-observations)) |
| Job Object `available=false` | Engine still runs in measurement-only mode; `/system/status` reports `MEASURED_ONLY` instead of `ENFORCED` |
| Frontend (Replit) can't reach the API | The backend binds `127.0.0.1` on your machine — a Replit app cannot reach `localhost` of your PC. See the networking section of `API_INTEGRATION.md` |
| `422` body you expected to be `503` | Parameter validation runs before the DB dependency on DB routes; by design (documented in tests) |

---

## Repository layout of docs

- [`README.md`](README.md) — this file: setup, run, tests, honest capabilities
- [`API_INTEGRATION.md`](API_INTEGRATION.md) — endpoints, shapes, polling flow, CORS/networking for the Replit frontend
- [`FEATURE_STATUS.md`](FEATURE_STATUS.md) — implemented / partial / not implemented
- [`.env.example`](.env.example) — the configuration contract
