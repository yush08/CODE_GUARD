# Feature status

Truthful inventory of what this backend does **today**, what is partial, and
what is not implemented. Nothing here is claimed without either (a) a passing
test on this machine or (b) a direct experiment.

**Evidence base**

| Command | Result on this machine |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q` | **99 passed, 4 skipped** |
| `.\.venv\Scripts\python.exe -m pytest -m windows -q` | **22 passed** |
| `.\.venv\Scripts\python.exe -m pytest -m db -q` | **4 skipped — pending PostgreSQL credentials** |
| `.\.venv\Scripts\python.exe scripts\check_compiler.py` | exit 0 — gcc/g++ 6.3.0, Job Objects available, not elevated |
| `python -m alembic upgrade head --sql` (offline) | exit 0 — migration SQL generates |
| `uvicorn app.main:app` boot | `/health` 200, `/system/status` 200, `/docs` 200, `openapi.json` → **21 paths / 27 operations** |

The 4 skipped tests are skipped **on purpose**: they need a real
`DATABASE_URL`. There is no SQLite/in-memory fallback anywhere in this repo.

---

## ✅ Implemented and verified

| # | Module | Status | Evidence |
| --- | --- | --- | --- |
| 1 | **Foundation** — config, settings, logging, error envelope, request ids | ✅ | `tests/test_config.py`, API tests: every failure path returns `{"error":{code,message,details},"request_id"}` |
| 2 | **Windows execution manager** (Job Objects via ctypes) | ✅ | 22 `-m windows` tests: job assignment, kill-on-close, process/memory caps, ACL hardening, workspace cleanup |
| 3 | **Submission pipeline** (queue → compile → run → classify → persist) | ✅ | `tests/test_judge_pipeline.py` (12 tests) covering ACCEPTED / WRONG_ANSWER / COMPILATION_ERROR / RUNTIME_ERROR / TLE / MLE / PLE / output cap / file events / cleanup / hardcoding |
| 4 | **Bounded concurrency + queue** | ✅ | `tests/test_execution_queue.py`; queue full → `503 EXECUTION_QUEUE_FULL` + row stored as `CANCELLED` (nothing lost silently) |
| 5 | **C/C++ compilation** (local GCC/MinGW, arg-list subprocess only) | ✅ | `scripts/check_compiler.py`, judge tests; no `shell=True` anywhere; compiler never downloaded |
| 6 | **Resource limits — per-limit truth table** | ✅ | See below; every limit reports `ENFORCED` / `MEASURED_ONLY` / `PARTIAL` / `UNAVAILABLE` |
| 7 | **Behavior monitoring** (chronological events with sources) | ✅ | Emits `PROCESS_START`, `PROCESS_EXIT`, `CHILD_PROCESS_CREATED`, `PROCESS_LIMIT_EVENT`, `TIME_LIMIT_EVENT`, `MEMORY_LIMIT_EVENT`, `WORKSPACE_FILE_CREATED`, `OUTPUT_LIMIT_EVENT`, `EXECUTION_ERROR`; sources `OBSERVED` / `INFERRED` / `SIMULATED` |
| 8 | **Behavior fingerprint** | ✅ | `tests/test_fingerprint_service.py`; nulls for unmeasured metrics (`measurements` map), never fabricated zeros |
| 9 | **Sequence-aware analysis** | ✅ | `tests/test_sequence_analyzer.py`; ordered events, rule matches with `explanation`, `SEQUENCE_RULES_PATH` JSON override |
| 10 | **Hardcoding / overfitting heuristic** | ✅ | `tests/test_hardcoding_analyzer.py`; `NO_SIGNAL` / `REVIEW_RECOMMENDED` / `INSUFFICIENT_DATA`, `confidence` **always `null`** |
| 11 | **Adaptive trust scoring** | ✅ logic | `tests/test_trust_service.py`; start 80 (0–100), deduped per `(workspace_label, submission_id, reason)`, auditable reasons, **never feeds the execution policy** |
| 12 | **Security events / alerts** | ✅ logic | Sequence + resource + hardcoding alerts with `PENDING → UNDER_REVIEW → CONFIRMED/DISMISSED` review flow |
| 13 | **Problems & test-case API** | ✅ code | CRUD + validation (limits vs `MAX_ALLOWED_*`), hidden-test protection in student-facing responses |
| 14 | **Submissions API** (`202` + polling, `/run` custom input) | ✅ code | Location header, non-blocking queueing, `/run` waits off the event loop and never affects trust |
| 15 | **Analysis APIs** (fingerprint, events, sequence, hardcoding, report, compare) | ✅ code | Read the judge-time snapshot (`judge_details_json`) with documented fallbacks |
| 16 | **Faculty APIs** (dashboard, submissions, alerts, review, trust-history, report) | ✅ code | Real aggregates only — empty DB = real zeros, no fabricated numbers |
| 17 | **DB models** (8 tables, UTC timestamps, indexes, FKs, cascade rules) | ✅ | `tests/test_migration_consistency.py` keeps models and migration in sync |
| 18 | **Alembic migration `0001_initial`** | ✅ offline | `python -m alembic upgrade head --sql` exits 0 |
| 19 | **OpenAPI docs** | ✅ | 21 paths / 27 operations; real enums exposed in the spec; `/docs` 200 |
| 20 | **Error envelope + request id** | ✅ | incl. Starlette unmatched-route 404s (test-covered) |
| 21 | **Health / system-status truthfulness** | ✅ | Reports compilers, Job Object, worker, elevation, DB state, capability list + warnings |

### Resource-limit truth table (verified)

| Limit | Mechanism | Reported | Verified by |
| --- | --- | --- | --- |
| Wall-clock deadline | `WaitForSingleObject(timeout)` + `TerminateJobObject(42)` | **ENFORCED** | Busy loop killed at deadline → `TIME_LIMIT_EXCEEDED` |
| Output size | byte-capped drain + `TerminateJobObject(44)` | **ENFORCED** | Output storm captured exactly `1048576` bytes → `RUNTIME_ERROR` |
| Memory | `JOB_OBJECT_LIMIT_JOB_MEMORY` | **ENFORCED** with job, else `MEASURED_ONLY` | Program itself saw refused allocations (`malloc stopped after 48 MB`) → `MEMORY_LIMIT_EXCEEDED` |
| Active processes | kernel cap `2×limit+4` **and** sampling monitor at exact `limit` | **ENFORCED** with job, else `MEASURED_ONLY` | Spawner tree killed (exit 43) → `PROCESS_LIMIT_EXCEEDED` |
| Child cleanup | `TerminateJobObject` + `KILL_ON_JOB_CLOSE` + orphan sweep | **ENFORCED** with job, else `PARTIAL` | Children observed → 0 |
| **CPU time** | `JOB_OBJECT_LIMIT_JOB_TIME` configured but **verified unreliable** | **MEASURED_ONLY** (always) | Control: no job → never killed. 500 ms budget → 1/3 killed at ~1.1 s CPU, 2/3 burned **5.9 s CPU**. CPU burners are stopped by the wall-clock deadline instead |
| Filesystem | workspace dirs + deny-ACL helper + workspace file events | **PARTIAL** | ACL denial test passes |
| Network | — | **NOT_AVAILABLE** | Nothing blocks network access |

---

## ⚠ Partial (implemented, with stated limits)

| Feature | What works | What does not |
| --- | --- | --- |
| **Auth / RBAC** | `AUTH_MODE=local_dev` actor seam (`X-Workspace-Label`, `X-Actor-Role`), conceptual STUDENT/FACULTY/ADMIN roles, structure ready for a real provider | **No credentials are verified.** No login, no tokens, no role enforcement on faculty routes. Server must stay on `127.0.0.1` |
| **Filesystem isolation** | Per-run workspace under `SANDBOX_ROOT`, cleanup confined to that root, real deny-ACL helper, workspace file-delta events | No filesystem virtualization; **`ACCESS_DENIED` events are reserved and never emitted**; OS-level file monitoring outside the workspace does not exist |
| **Process monitoring** | Polling monitor counts student processes (infrastructure like `conhost.exe` excluded) and kills at the limit | Children born **and** dying between polls can be missed; the kernel cap at `2×limit+4` is the backstop |
| **CPU-time enforcement** | CPU usage is measured and reported; `CPU_LIMIT_HIT_RATIO` classifies CPU-heavy runs post-hoc | Kernel kill is unreliable → never reported as `ENFORCED` |
| **Trust scoring** | Full formula, dedupe, audit trail, monitoring levels, alert gating | Scope is a local `workspace_label`, **not a verified student identity**; trust never weakens or alters core limits |
| **Sequence / hardcoding signals** | Explanations, evidence lists, review flow | Assistive only — **not proof of misconduct**; hardcoding `confidence` is permanently `null` |
| **Seed demo** | Problems + 6 really-judged fixture submissions | Needs a configured database (exits `2` with a clear message otherwise) |

---

## ❌ Not implemented (stated openly)

| Item | Status |
| --- | --- |
| Real authentication (login, password hashing, sessions/JWT) | **Not implemented** — local-dev seam only |
| Role-based authorization enforcement (faculty routes are unguarded) | **Not implemented** |
| Network isolation / egress blocking | **Not implemented** — Job Objects do not restrict network |
| Filesystem / registry virtualization, syscall filtering, container-grade sandboxing | **Not implemented** — Job Objects are explicitly *not* a full sandbox |
| `ACCESS_DENIED` behavior events | **Reserved**, never emitted |
| `SECURITY_VIOLATION` submission status | **Reserved**, no detector emits it |
| Kernel CPU-time enforcement | Configured but **verified unreliable** on this machine → reported `MEASURED_ONLY` |
| Multi-language support beyond C/C++ (Java, Python, …) | **Not implemented** |
| Docker/WSL/Linux execution paths | **Not implemented by design** (Windows-only requirement) |
| Frontend | **Out of scope** — developed separately in Replit |
| Cross-network access from a Replit-hosted frontend to `127.0.0.1` | **Not possible without a tunnel** on your machine; documented in `API_INTEGRATION.md` |

---

## ⏳ Pending until the PostgreSQL password is in `.env`

These are **complete in code** but cannot be executed on this machine yet —
they are marked pending rather than claimed as working:

1. **Live migration** — `python -m alembic upgrade head` against the real
   `codeguard` database (offline SQL generation already verified, exit 0).
2. **DB lifecycle tests** — the 4 `-m db` tests (connectivity, schema
   completeness, submission round-trip, analysis persistence).
3. **DB-backed API behavior** — problem CRUD, submission judging with
   persistence, analysis endpoints, faculty dashboard/alerts/trust-history,
   `scripts\seed_demo.py`.
4. **End-to-end demo** — `seed_demo.py --run-submissions` printing the six real
   statuses.

**Current behavior without a password:** `GET /api/v1/health` and
`GET /api/v1/system/status` return `200` and report
`database.configured=false` with `config_state` ∈
`{UNCONFIGURED_MISSING_URL, UNCONFIGURED_PLACEHOLDER}`; every other endpoint
returns `503` with `error.code = "DATABASE_NOT_CONFIGURED"`. That is a
deliberate, clearly labelled mode — not a crash and not a fallback.

---

## Known environment quirks (documented, not bugs)

1. **Antivirus first-launch stall** — Windows Defender real-time protection
   (enabled here) blocks `CreateProcess` of a freshly compiled student binary
   in the project sandbox for **1.7–17 s**; the same binary from `%TEMP%` takes
   0–16 ms, and re-launching is instant. Handled by (a) starting the resource
   deadline only after the process exists, and (b) reporting the stall as
   `spawn_ms` instead of execution time. Optional mitigation: a Defender
   exclusion for the repo/`SANDBOX_ROOT` (requires admin — the backend never
   asks for elevation).
2. **`conhost.exe` in every job** — Windows console hosts are excluded from
   student process counts so legitimate programs are never penalized.
3. **`TotalProcesses` bookkeeping** — job accounting can show the total number
   of processes created (including ones already exited); it is reported as a
   measurement, not as "processes still running".
