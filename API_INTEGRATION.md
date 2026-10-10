# CodeGuard API Integration Guide

Integration reference for the separately developed frontend (Replit).
Everything below is generated from / checked against the live OpenAPI spec
(`GET /openapi.json`): **21 paths, 27 operations**, all under `/api/v1`.

Interactive documentation while the server runs: `http://127.0.0.1:8000/docs`

---

## Contents

1. [Base URL & networking (read this first)](#1-base-url--networking-read-this-first)
2. [CORS](#2-cors)
3. [Headers & the local-dev identity seam](#3-headers--the-local-dev-identity-seam)
4. [Error envelope](#4-error-envelope)
5. [Pagination](#5-pagination)
6. [Endpoints](#6-endpoints)
   - [Health & system](#61-health--system)
   - [Problems & test cases](#62-problems--test-cases)
   - [Submissions](#63-submissions)
   - [Analysis (the four novelty modules)](#64-analysis-the-four-novelty-modules)
   - [Faculty](#65-faculty)
7. [Judging flow (submit → poll → analyse)](#7-judging-flow-submit--poll--analyse)
8. [Enumerations](#8-enumerations)
9. [Limits you must respect](#9-limits-you-must-respect)
10. [Database-unconfigured mode (503s)](#10-database-unconfigured-mode-503s)

---

## 1. Base URL & networking (read this first)

The backend binds **`127.0.0.1`** on the machine that runs it (local-dev mode,
no authentication). Consequences for a frontend hosted on Replit:

- A browser page served by **Replit cannot reach `localhost:8000` of your
  Windows machine** — `localhost` there means the Replit container itself.
- What *does* work:
  - **Same machine / same browser**: open the frontend from
    `http://localhost:xxxx` on this PC (e.g. Vite/Next dev server started on
    this machine) and call `http://127.0.0.1:8000/api/v1/...`.
  - **Replit-hosted frontend → your PC**: not reachable without an explicit
    tunnel from your machine (e.g. `ngrok http --scheme http 8000`) **plus** its
    public origin added to `CORS_ORIGINS`. This is a network reality, not
    something the backend can turn on for you.
- Do not hardcode a host: read the base URL from an environment/config value in
  your frontend, defaulting to `http://127.0.0.1:8000`.

All API paths in this document are relative to that base URL.

---

## 2. CORS

Allowed origins come **only** from `CORS_ORIGINS` in `.env` (comma separated),
default:

```
http://localhost:3000,http://localhost:5173,http://localhost:4173
```

There is no hardcoded frontend URL anywhere in the backend. If your dev server
runs on another port, add it to `CORS_ORIGINS` and restart. `X-Request-ID` is
exposed to the browser.

---

## 3. Headers & the local-dev identity seam

| Header | Required | Meaning |
| --- | --- | --- |
| `Content-Type: application/json` | on POST/PATCH bodies | standard JSON |
| `X-Workspace-Label` | optional | Scopes a request to a workspace label (trust/history scoping). Body fields override it where both exist. |
| `X-Actor-Role` | optional | `STUDENT` \| `FACULTY` \| `ADMIN` — conceptual roles only. |
| `X-Request-ID` | optional | You supply it, the server echoes it back in the response header **and** in the error body. |

> **`AUTH_MODE=local_dev` means no credential is verified.** The seam exists so
> real authentication/RBAC can be added later; today the headers are advisory.
> Do not build security-sensitive UI that pretends otherwise.

---

## 4. Error envelope

Every failure path (400/404/409/422/503, including unknown routes) returns:

```json
{
  "error": {
    "code": "DATABASE_NOT_CONFIGURED",
    "message": "The database is not configured; this operation requires persistent storage.",
    "details": {"reason": "DATABASE_URL is not set."}
  },
  "request_id": "8f4c1d2a9b0e4f11"
}
```

- `error.code` — stable, machine-readable; branch your UI on this.
- `error.message` — human-readable, secret-free; safe to display.
- `error.details` — may contain field-level validation info, e.g.
  `{"field": "language"}`, `{"reason": ...}`, or, for `422`,
  `{"issues": [{"location": "body.language", "message": "...", "type": "..."}]}`.
- `request_id` — also returned in the `X-Request-ID` response header.

Codes you can actually receive:

| HTTP | `error.code` | When |
| --- | --- | --- |
| `404` | `NOT_FOUND` | Unknown submission/problem/alert id, missing fingerprint, unknown route |
| `413` | `SOURCE_TOO_LARGE` | `source_code` exceeds `MAX_SOURCE_SIZE_BYTES` |
| `422` | `VALIDATION_ERROR` | Body/query validation (Pydantic) or unsupported/problem-mismatched language |
| `503` | `DATABASE_NOT_CONFIGURED` | DB not configured (see §10) |
| `503` | `DATABASE_UNAVAILABLE` | DB configured but unreachable (probe failed) |
| `503` | `EXECUTION_QUEUE_FULL` | Judge queue at capacity — retry later |
| `503` | `COMPILER_NOT_AVAILABLE` | No usable compiler for the requested language |
| `500` | `EXECUTION_ENGINE_ERROR` / `INTERNAL_ERROR` | Unexpected failure (includes `request_id`) |

---

## 5. Pagination

Every list endpoint accepts `page` (≥1, default `1`) and `page_size`
(default `20`, max `100`; events max `200`) and returns:

```json
{
  "items": [ /* endpoint-specific objects */ ],
  "pagination": { "page": 1, "page_size": 20, "total": 42, "total_pages": 3 }
}
```

---

## 6. Endpoints

Legend for **DB**: ❗ = needs a configured database (503 otherwise), ✅ = works
without one.

### 6.1 Health & system

| Method | Path | DB | Response |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | ✅ | `{status, app, version, database:{configured, config_state, connectivity, reason}, database_required_for_full_function}` |
| `GET` | `/api/v1/system/status?refresh=false` | ✅ | Full capability report (below) |

`system/status` payload highlights:

```jsonc
{
  "app": "CodeGuard", "version": "0.1.0", "environment": "development",
  "os": {"system": "Windows", "release": "11", "version": "10.0.26200", "machine": "AMD64"},
  "python": {"version": "3.12.5", ...},
  "auth_mode": "local_dev",
  "running_as_admin": false,
  "database": {"configured": false, "config_state": "UNCONFIGURED_MISSING_URL",
               "connectivity": "NOT_ATTEMPTED", "reason": "...", "display_url": null},
  "compilers": [{"language":"c","compiler":"gcc","available":true,
                 "path":"C:\\MinGW\\bin\\gcc.EXE","version":"gcc.exe ... 6.3.0","detail":"Detected via PATH lookup."}],
  "supported_languages": ["c", "cpp"],
  "job_object": {"available": true, "detail": "Job object created and configured."},
  "execution_worker": {"running": true, "active_executions": 0, "queued_jobs": 0,
                       "max_concurrent_executions": 2, "max_queued_jobs": 50},
  "capabilities": [ /* id, name, status, detail — see table below */ ],
  "warnings": [ /* human-readable honesty notes */ ]
}
```

`capabilities[].status` values: `ACTIVE`, `MEASURED_ONLY`, `PARTIAL`,
`NOT_AVAILABLE`, `RESERVED`. Use this to render a truthful status panel — for
example `resource_limits.detail` explicitly states that **CPU time is
MEASURED_ONLY** (the kernel CPU-time cap was verified unreliable; CPU burners
are stopped by the wall-clock deadline).

`?refresh=true` re-runs the **compiler and Job Object** capability probes
immediately instead of using their cached results. The database probe has its
own short cache (`DB_PROBE_TTL_SECONDS`) and timeout (`DB_PROBE_TIMEOUT_SECONDS`)
and is never attempted at startup.

### 6.2 Problems & test cases

| Method | Path | Notes | Status |
| --- | --- | --- | --- |
| `GET` | `/api/v1/problems?page&page_size` | Page of `ProblemSummary`; **public samples only** — hidden test data is never included | ❗ |
| `POST` | `/api/v1/problems` | `201` + `ProblemDetail`. Validates limits against `MAX_ALLOWED_*` | ❗ |
| `GET` | `/api/v1/problems/{id}` | `ProblemDetail` (includes sample cases, counts) | ❗ |
| `PATCH` | `/api/v1/problems/{id}` | Partial update → `ProblemDetail` | ❗ |
| `DELETE` | `/api/v1/problems/{id}` | `204 No Content` | ❗ |
| `GET` | `/api/v1/problems/{id}/test-cases` | **All** cases incl. hidden (faculty/admin surface — there is no role gate in local-dev mode) | ❗ |
| `POST` | `/api/v1/problems/{id}/test-cases` | `201` + `TestCaseAdmin`. Body: `input_data`, `expected_output`, `is_public`, `is_generated`, `label?` | ❗ |
| `DELETE` | `/api/v1/problems/{id}/test-cases/{case_id}` | `204 No Content` | ❗ |

`ProblemCreate` fields: `title` (required), `slug` (optional), `description`,
`input_format`, `output_format`, `constraints`, `difficulty`, `visibility`,
`supported_languages`, `time_limit_ms`, `memory_limit_mb`, `process_limit`
(limits optional → defaults from `.env`), plus an optional inline
`test_cases[]`.

`ProblemDetail` adds `description`, `input_format`, `output_format`,
`constraints`, `supported_languages`, `sample_tests[]` (**public examples
only — hidden tests are never included**), `test_case_counts`, `created_at`,
`updated_at`.

### 6.3 Submissions

#### `POST /api/v1/submissions` — queue a judged run → `202 Accepted`

```jsonc
{
  "problem_id": 1,
  "language": "c",                 // "c" | "cpp"
  "source_code": "#include <stdio.h>\nint main(){...}",
  "workspace_label": "student-42"  // optional (falls back to X-Workspace-Label)
}
```

Response `202` (also sets `Location: /api/v1/submissions/{id}`):

```jsonc
{
  "id": 17,
  "status": "QUEUED",
  "detail_url": "/api/v1/submissions/17",
  "message": "Submission queued for execution."
}
```

Queue full → `503 EXECUTION_QUEUE_FULL` and the row is stored as `CANCELLED`
with `error_summary="Not executed: the execution queue was full."` (so nothing
disappears silently).

#### `GET /api/v1/submissions?page&page_size&problem_id&workspace_label`

Page of `SubmissionSummary`: `id, problem_id, language, status,
workspace_label, is_custom_run, execution_time_ms, cpu_time_ms,
peak_memory_bytes, exit_code, error_summary, created_at, completed_at`.

#### `GET /api/v1/submissions/{id}` → `SubmissionDetail`

`SubmissionSummary` **plus** `source_code`, `compile_output`, `test_results[]`.

`test_results[]` = `TestResultPublic`:

```jsonc
{
  "id": 301, "test_index": 1, "test_is_public": true, "test_is_generated": false,
  "status": "ACCEPTED",
  "execution_time_ms": 12, "memory_bytes": 3481600, "exit_code": 0,
  "error_summary": null,
  "output_summary": "5\n"     // null for hidden tests (student-facing masking)
}
```

> Hidden-test **expected output and actual output are never exposed** to
> student-facing responses; `output_summary` is `null` for hidden cases and
> only the status/metrics are shown.

#### `POST /api/v1/submissions/run` — custom stdin (no test evaluation)

```jsonc
{ "language": "c", "source_code": "...", "input": "3 4\n", "workspace_label": "student-42" }
```

Returns `SubmissionDetail` directly (it waits up to
`RUN_WAIT_TIMEOUT_SECONDS`, off the event loop). If judging takes longer, you
get the current non-final status — poll `GET /submissions/{id}`.
**Custom runs never affect the trust score.** Output appears in
`test_results[0].output_summary`.

### 6.4 Analysis (the four novelty modules)

All need a finished submission (❗ DB). They read the snapshot persisted at
judge time (`judge_details_json`), so results stay stable even if rules change
later; `sequence` falls back to recomputing from stored events and `hardcoding`
to recomputing from stored test hashes.

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/api/v1/analysis/submissions/{id}/fingerprint` | `FingerprintResponse` |
| `GET` | `/api/v1/analysis/submissions/{id}/events?page&page_size` | `Page[EventResponse]` |
| `GET` | `/api/v1/analysis/submissions/{id}/sequence` | `SequenceAnalysisResponse` |
| `GET` | `/api/v1/analysis/submissions/{id}/hardcoding` | `HardcodingAnalysisResponse` |
| `GET` | `/api/v1/analysis/submissions/{id}/report` | `ReportResponse` (combined) |
| `GET` | `/api/v1/analysis/fingerprints/compare?left_id&right_id` | comparison object |

**Fingerprint**

```jsonc
{
  "submission_id": 17,
  "analysis_type": "FINGERPRINT",
  "measurement_source": "OBSERVED",
  "fingerprint": { /* metrics: cpu/memory/output/durations/event counts... */ },
  "measurements": { "peak_memory_bytes": "MEASURED", "some_metric": "UNAVAILABLE" },
  "notes": ["Null means 'not measured', never zero."]
}
```

**Events** (`EventResponse`)

```jsonc
{ "id": 9001, "event_type": "PROCESS_START", "occurred_at": "2026-10-10T09:15:02Z",
  "source": "OBSERVED", "severity": "INFO", "details": { "pid": 59296, "spawn_ms": 17 } }
```

**Sequence analysis**

```jsonc
{
  "analysis_type": "SEQUENCE_ANALYSIS", "risk_level": "MEDIUM",
  "rule": "rapid_process_churn",
  "observed_events": ["PROCESS_START", "CHILD_PROCESS_CREATED", "PROCESS_EXIT"],
  "explanation": "Plain-language reason for a human reviewer.",
  "source": "INFERRED", "rules_evaluated": 6,
  "matches": [ { "rule_id": "...", "risk_level": "MEDIUM", "explanation": "...",
                 "creates_alert": true, "matched_at_index": 2,
                 "matched_event_types": ["PROCESS_START"], "evidence": [ ... ] } ]
}
```

**Hardcoding heuristic** — status is `NO_SIGNAL` | `REVIEW_RECOMMENDED` |
`INSUFFICIENT_DATA`, and `confidence` is **always `null` by design**:

```jsonc
{
  "analysis_type": "HARDCODING_HEURISTIC",
  "status": "REVIEW_RECOMMENDED",
  "evidence": ["Output is correct on visible tests but differs structurally ..."],
  "confidence": null,
  "explanation": "...", "supporting_tests": [ ... ],
  "notes": ["Assistive signal for human review, not proof of misconduct."]
}
```

**Compare** → `{left_submission_id, right_submission_id, comparison:
{comparable_fields, unavailable_fields, identical_fields, similar_fields,
different_fields, explanation}}` — informational, never a plagiarism verdict.

**Report** → `{generated_at, submission, problem, compilation{attempted,
success, compiler, compiler_version, compiler_path, flags, duration_ms,
output, failure_reason}, test_summary{total, accepted, wrong_answer,
runtime_error, time_limit_exceeded, memory_limit_exceeded,
process_limit_exceeded, results[]}, resource_measurements{policy, enforcement,
spawn_ms, job, acl, cleanup, submission_metrics, measurements, note}, analysis
{fingerprint, sequence_analysis, hardcoding_analysis}, alerts[], trust{},
limitations[]}`

`resource_measurements.enforcement` is the per-limit truth map, e.g.:

```jsonc
{
  "wall_clock_deadline": "ENFORCED",
  "output_size_limit": "ENFORCED",
  "memory_limit": "ENFORCED",              // MEASURED_ONLY when Job Objects unavailable
  "active_process_limit": "ENFORCED",      // MEASURED_ONLY when Job Objects unavailable
  "child_process_cleanup": "ENFORCED",     // PARTIAL when Job Objects unavailable
  "cpu_time_limit": "MEASURED_ONLY"        // always: verified unreliable in the kernel
}
```

`limitations[]` repeats the honesty notes (Job Objects are not a full sandbox,
null means unmeasured, signals are assistive, trust is scoped to a label).

### 6.5 Faculty

All ❗ (503 until the database is configured). No role gate in local-dev mode —
protect these routes with a real reverse proxy/auth layer if you expose them.

| Method | Path | Query params | Returns |
| --- | --- | --- | --- |
| `GET` | `/api/v1/faculty/dashboard` | — | `DashboardResponse`: `submissions`, `alerts`, `resource_metric_coverage`, `recent_submissions[]`, `recent_alerts[]`, `note` ("empty database = real zeros") |
| `GET` | `/api/v1/faculty/submissions` | `page, page_size, problem_id, workspace_label` | `Page[SubmissionSummary]` |
| `GET` | `/api/v1/faculty/alerts` | `page, page_size, severity, review_status, submission_id` | `Page[AlertSummary]` |
| `GET` | `/api/v1/faculty/alerts/{id}` | — | `AlertDetail` (+ `evidence_json`, `reviewed_by`, `review_note`, `reviewed_at`) |
| `PATCH` | `/api/v1/faculty/alerts/{id}` | — | body: `{review_status, review_note?, reviewed_by?}` → `AlertDetail` |
| `GET` | `/api/v1/faculty/trust-history` | `workspace_label` (required), `limit` (≤200) | `TrustHistoryResponse` |
| `GET` | `/api/v1/faculty/reports/{workspace_label}` | — | Aggregated report for that label |

Reviewing an alert changes trust only for `CONFIRMED` (−15) / `DISMISSED`
(+15) decisions and appends an auditable `TrustHistory` row; the adjustment is
deduped per `(workspace_label, submission_id, reason)` so re-reviewing never
double counts.

`TrustHistoryResponse` includes `current_score`, `monitoring_level`
(`NORMAL` ≥70, `ELEVATED` 50–69, `HIGH` <50), `open_alerts`, `formula`
(explaining the scoring constants), and `entries[]`
(`previous_score`, `new_score`, `reason`, `related_submission_id`).

---

## 7. Judging flow (submit → poll → analyse)

```js
const BASE = process.env.CODEGUARD_BASE_URL ?? "http://127.0.0.1:8000";

// 1) submit
const accepted = await fetch(`${BASE}/api/v1/submissions`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Workspace-Label": "student-42",   // optional identity seam (not auth)
  },
  body: JSON.stringify({
    problem_id: 1,
    language: "c",
    source_code: source,
    workspace_label: "student-42",
  }),
}).then(r => r.json());            // check r.ok / r.error for failures

// 2) poll until terminal
const TERMINAL = new Set([
  "ACCEPTED", "WRONG_ANSWER", "COMPILATION_ERROR", "RUNTIME_ERROR",
  "TIME_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED", "PROCESS_LIMIT_EXCEEDED",
  "SECURITY_VIOLATION", "CANCELLED", "INTERNAL_ERROR",
]);

let detail;
for (let i = 0; i < 120; i++) {         // ~60 s budget
  detail = await fetch(`${BASE}${accepted.detail_url}`).then(r => r.json());
  if (TERMINAL.has(detail.status)) break;
  await new Promise(res => setTimeout(res, 500));
}

// 3) show results (hidden-test outputs are masked server-side)
for (const t of detail.test_results) {
  console.log(t.test_index, t.status, t.execution_time_ms, t.output_summary);
}

// 4) analysis for the reviewer view
const seq = await fetch(`${BASE}/api/v1/analysis/submissions/${detail.id}/sequence`).then(r => r.json());
const hard = await fetch(`${BASE}/api/v1/analysis/submissions/${detail.id}/hardcoding`).then(r => r.json());
console.log(seq.risk_level, seq.explanation, hard.status, hard.confidence); // confidence === null
```

Non-terminal statuses while polling: `QUEUED`, `COMPILING`, `RUNNING`,
`ANALYZING`.

---

## 8. Enumerations

**Submission status** (also in OpenAPI):

```
QUEUED, COMPILING, RUNNING, ANALYZING,
ACCEPTED, WRONG_ANSWER, COMPILATION_ERROR, RUNTIME_ERROR,
TIME_LIMIT_EXCEEDED, MEMORY_LIMIT_EXCEEDED, PROCESS_LIMIT_EXCEEDED,
SECURITY_VIOLATION, CANCELLED, INTERNAL_ERROR
```

`SECURITY_VIOLATION` exists in the model but **no detector emits it** — the
status is reserved and documented, never faked.

**Test status**: `ACCEPTED, WRONG_ANSWER, RUNTIME_ERROR, TIME_LIMIT_EXCEEDED,
MEMORY_LIMIT_EXCEEDED, PROCESS_LIMIT_EXCEEDED`

**Language**: `c`, `cpp` · **Difficulty**: see OpenAPI ·
**Visibility**: see OpenAPI · **Review status** (alerts): `PENDING`, `UNDER_REVIEW`, `CONFIRMED`,
`DISMISSED`

**Event types**: `PROCESS_START, PROCESS_EXIT, CHILD_PROCESS_CREATED,
PROCESS_LIMIT_EVENT, TIME_LIMIT_EVENT, MEMORY_LIMIT_EVENT,
WORKSPACE_FILE_CREATED, OUTPUT_LIMIT_EVENT, EXECUTION_ERROR`
(`ACCESS_DENIED` is reserved and never emitted).

**Event source**: `OBSERVED` (really measured), `INFERRED` (derived),
`SIMULATED` (test/demo data only).

**Severity**: `INFO, LOW, MEDIUM, HIGH`.

---

## 9. Limits you must respect

| Limit | Value (`.env`) | Violation |
| --- | --- | --- |
| Source size | `MAX_SOURCE_SIZE_BYTES` (100 000) | `413 SOURCE_TOO_LARGE` |
| Problem input/output/output-cap | `MAX_ALLOWED_*` | `422` at creation time |
| `page_size` | ≤ 100 (≤ 200 for events) | `422` |
| Judge concurrency / queue | `MAX_CONCURRENT_EXECUTIONS`, `MAX_QUEUED_JOBS` | `503 EXECUTION_QUEUE_FULL` |

Compilation and execution never block the HTTP event loop: submission is
non-blocking (`202`), `POST /submissions/run` waits in a worker thread.

---

## 10. Database-unconfigured mode (503s)

While `DATABASE_URL` is missing or still the `YOUR_PASSWORD` placeholder:

- ✅ `GET /api/v1/health` and `GET /api/v1/system/status` → `200`, reporting
  `database.configured=false` and `config_state` ∈
  `{UNCONFIGURED_MISSING_URL, UNCONFIGURED_PLACEHOLDER}`.
- ❗ every other endpoint → `503` with:

```json
{
  "error": {
    "code": "DATABASE_NOT_CONFIGURED",
    "message": "The database is not configured; this operation requires persistent storage.",
    "details": {"reason": "DATABASE_URL is not set."}
  },
  "request_id": "..."
}
```

Render that as a "backend database not configured yet" banner — it is a real
state, not an error to retry in a loop. There is no SQLite fallback by design.
Once `.env` has the real password: `python -m alembic upgrade head`, restart,
and these endpoints start returning data.
