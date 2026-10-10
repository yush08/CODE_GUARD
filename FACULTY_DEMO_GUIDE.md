# CodeGuard — Faculty Demonstration Guide

## A. Project Introduction

**CodeGuard** is a behavior-aware secure online judge for C and C++ programming exercises. Unlike conventional online judges that only report pass/fail results, CodeGuard observes and records the **runtime behavior** of every submission:

- System-call-level event streams (process creation, file access, resource limits)
- Behavioral fingerprints (execution time, CPU usage, memory, process tree)
- Sequence-aware pattern analysis against transparent rules
- Hardcoding heuristics that flag overfitting to known test cases
- Adaptive trust scoring per workspace

All execution happens in a **Windows-native sandbox** using Job Objects for resource control. The system is designed for academic environments where instructors need to understand *how* a program behaves, not just whether it produces correct output.

---

## B. Problem Statement

Conventional online judges (e.g., Codeforces, Kattis, LeetCode) treat execution as a black box:
- Input → [black box] → Output → Pass/Fail

This is insufficient for security education and academic integrity because:
1. **Cheating via hardcoding** — Students can memorize test case outputs without writing correct algorithms
2. **Infinite loops / resource abuse** — No visibility into *why* a program timed out
3. **Malicious behavior** — File system access, process spawning, network attempts go undetected
4. **Partial credit / debugging** — Instructors cannot distinguish "wrong logic" from "off-by-one" from "infinite loop"

CodeGuard opens the black box by recording and analyzing the **observable execution behavior** of every submission.

---

## C. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        REACT FRONTEND (Vite)                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Faculty Console │  │ Dev Workspace   │  │   Monitoring    │  │
│  │  Dashboard       │  │  Compiler       │  │  Simulator      │  │
│  │  Submissions     │  │  Simulator      │  │  System Health  │  │
│  │  Comparison      │  └─────────────────┘  └─────────────────┘  │
│  │  Alerts          │                              ▲              │
│  │  Analytics       │                              │              │
│  │  Trust History   │                              │              │
│  └────────┬─────────┘                              │              │
│           │ REST API (JSON)                        │              │
└───────────┼────────────────────────────────────────┼──────────────┘
            ▼                                        │
┌─────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Problem API  │  │ Submission   │  │ Analysis / Faculty    │  │
│  │              │  │ API          │  │ API                    │  │
│  │ • CRUD       │  │              │  │                        │  │
│  │ • Test cases │  │ • Run        │  │ • Dashboard           │  │
│  │ • Visibility │  │ • Submit     │  │ • Alerts              │  │
│  └──────────────┘  │ • Queue      │  │ • Trust History       │  │
│                    └──────┬───────┘  │ • Reports             │  │
│                           ▼          └──────────────────────┘  │
│                  ┌──────────────────────┐                      │
│                  │ Execution Worker     │                      │
│                  │ (Windows Job Objects)│                      │
│                  │ • GCC/MinGW compile  │                      │
│                  │ • Sandboxed exec     │                      │
│                  │ • Behavior monitor   │                      │
│                  └──────────┬───────────┘                      │
│                            ▼                                    │
│                  ┌──────────────────────┐                      │
│                  │ PostgreSQL Database  │                      │
│                  │ (Problems, Subs,     │                      │
│                  │  Events, Alerts,     │                      │
│                  │  Trust, Fingerprints)│                      │
│                  └──────────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

**Key Components:**

| Component | Technology | Purpose |
|-----------|------------|---------|
| Frontend | React + TypeScript + Vite + Tailwind | Faculty dashboard, compiler, monitoring |
| API | FastAPI (Python 3.12) | REST endpoints, OpenAPI schema |
| Database | PostgreSQL 17 + SQLAlchemy | Persistent storage, migrations (Alembic) |
| Compiler | MinGW GCC/G++ 6.3.0 | C/C++ compilation |
| Execution | Windows Job Objects | Process isolation, resource limits |
| Monitoring | ETW / Custom ETW provider | Process, file, resource events |
| Analysis | Custom Python services | Fingerprinting, sequence analysis, hardcoding detection |

---

## D. Demonstration Sequence (5–8 Minutes)

> **Prerequisites:** Backend running on `http://127.0.0.1:8000`, Frontend on `http://localhost:5173`

### 1. Admin Dashboard (1 minute)
**Open:** `http://localhost:5173/`

**Talking points:**
- "This is the faculty landing page — real backend data, no fabricated charts"
- KPIs: Total submissions, acceptance rate, pending alerts, trust score, CPU/fingerprint coverage
- Each KPI is clickable (drill-down to filtered views)
- Faculty review summary: compile errors, wrong answers, timeouts, pending alerts
- Recent backend submissions with direct links to details
- Security alerts panel (shows 2 real alerts from timeout test)
- Backend connectivity + database status badges

### 2. Security Alerts (1 minute)
**Click** "ALERTS PENDING 2" KPI → Opens `/alerts?review_status=PENDING`

**Talking points:**
- Two real alerts from the infinite-loop timeout test (submission #13)
- **TIME_LIMIT_TERMINATION** — Sequence rule: `TIME_LIMIT_EVENT → PROCESS_EXIT` (medium risk)
- **POSSIBLE_HARDCODING** — Identical output across different inputs/expected answers
- Both show backend-generated explanations, severity, review status (PENDING)
- Filter dropdown works (try "CONFIRMED" → empty state with helpful message)
- Read-only — no fake "Resolve" buttons (no backend review endpoint exists)
- Links to submission #13 detail page

### 3. Submissions Table (1 minute)
**Navigate:** `/submissions`

**Talking points:**
- 15 real submissions (mix of custom runs and judged submissions)
- Search by ID, status, workspace, problem, language
- Status filter dropdown (all backend statuses)
- Each row links to submission detail
- Checkbox selection enables "Compare selected" for comparison page
- Status badges use consistent color coding (green=accepted, yellow=wrong, red=error)

### 4. Submission Comparison (1 minute)
**Open:** `/compare?ids=11,12` (ACCEPTED vs WRONG_ANSWER for A+B problem)

**Talking points:**
- Side-by-side execution metrics: wall time, CPU, peak memory
- Fingerprint comparison: identical peak memory/process count, different CPU/wall time
- Real backend fingerprint comparison endpoint: `/analysis/fingerprints/compare`
- Event timelines for both submissions (observed events with source)
- Clear disclaimer: "Comparison highlights measurable differences only... not proof of misconduct"
- No fabricated confidence scores or cheating verdicts

### 5. Compiler Workflow (2 minutes)
**Open:** `/compiler`

**Steps:**
1. **Select problem** — Click "A + B" (shows 2 public / 4 hidden tests, 1000ms limit)
2. **Choose language** — C or C++
3. **Write code** — Paste correct C solution
4. **Custom Run** — Enter stdin `2 3` → Click **Run** → Shows stdout `5`, exit 0, 61ms
5. **Submit** — Click **Submit** → Queued → **ACCEPTED** (6/6 tests pass)
6. **Submit incorrect** — Paste code that outputs `a + b + 1` → **WRONG_ANSWER** (0/6)
7. **Submit invalid** — Paste syntax error → **COMPILATION_ERROR** with GCC diagnostics
8. **Detail link** — Opens submission detail with test results, events, resource metrics

**Talking points:**
- Run ≠ Submit: Run uses custom stdin, Submit judges against stored test cases
- Hidden tests remain hidden (student-facing API returns only public samples)
- Real compilation output, stderr, exit codes, execution metrics
- Problem catalog loaded from real backend (3 seeded problems)

### 6. System Health & Limitations (1 minute)
**Open:** `/health`

**Talking points:**
- 13 capabilities with honest status labels:
  - **ACTIVE**: Problem management, compilation, fingerprinting, sequence analysis, trust scoring
  - **PARTIAL**: Auth/RBAC (local_dev mode, no real credentials), Filesystem isolation
  - **NOT_AVAILABLE**: Network isolation (Job Objects don't block network)
  - **RESERVED**: SECURITY_VIOLATION status (no detector emits it)
  - **MEASURED_ONLY**: CPU time (kernel per-job CPU limit unreliable; wall-clock is reliable)
- Warnings panel: "Job Objects are not a full sandbox"
- No misleading claims — limitations are front and center

### 7. Behavior Simulator (30 seconds)
**Open:** `/simulation`

**Talking points:**
- 7 deterministic scenarios for teaching
- SIMULATION badge + disclaimer: "No process, file operation, measurement, or security finding came from a real execution"
- Scenario selector: Normal, Infinite loop, Memory-heavy, Process creation, File access, Hardcoding, Suspicious sequence
- Event timeline advances with simulated time
- Events clearly labeled SIMULATED, never mixed with backend data

### 8. Trust & Risk History (30 seconds)
**Open:** `/trust`

**Talking points:**
- Per-workspace adaptive trust scoring
- Current score, monitoring level, open alerts
- Chronological history with score changes and reasons
- Explicit disclaimer: "This is not user authentication and never changes execution limits"

---

## E. Novelty — What Makes CodeGuard Different

| Feature | Conventional Judge | CodeGuard |
|---------|-------------------|-----------|
| **Output verification** | Exact match / custom checker | Exact match (public + hidden tests) |
| **Execution visibility** | None | Full event stream (process, file, resource) |
| **Behavioral fingerprint** | No | Yes — CPU, memory, process tree, syscalls |
| **Sequence analysis** | No | Yes — ordered event patterns with rules |
| **Hardcoding detection** | No | Heuristic review signals (not verdicts) |
| **Trust scoring** | No | Adaptive per-workspace history |
| **Security alerts** | No | Faculty-reviewable signals |
| **Sandbox** | Linux containers / cgroups | Windows Job Objects (native) |
| **CPU enforcement** | cgroups CPU quota | Wall-clock deadline (CPU measured only) |
| **Educational simulator** | No | Deterministic browser scenarios |

**Key differentiator:** CodeGuard treats *observable runtime behavior* as first-class data for faculty review, not just a pass/fail signal.

---

## F. Limitations — Honest Disclosure

| Area | Status | What This Means |
|------|--------|-----------------|
| **Authentication** | `local_dev` mode | No real credentials verified; `X-Workspace-Label` header accepted but not authenticated |
| **CPU time enforcement** | MEASURED_ONLY | Kernel per-job CPU limit unreliable; wall-clock deadline is the reliable stopper |
| **Filesystem isolation** | PARTIAL | Per-run workspace dirs + deny-ACL helper; no virtualization; ACCESS_DENIED not emitted |
| **Network isolation** | NOT_AVAILABLE | Job Objects don't restrict network access |
| **SECURITY_VIOLATION** | RESERVED | Status exists in model but no detector emits it |
| **Hardcoding verdicts** | REVIEW SIGNAL ONLY | Heuristics produce `REVIEW_RECOMMENDED` / `NO_SIGNAL` — never a cheating accusation |
| **Alert review workflow** | READ-ONLY | No backend mutation endpoint for confirm/dismiss |
| **Windows Job Objects** | NOT A FULL SANDBOX | No filesystem/network virtualization; partial resource control |

**These limitations are displayed in the System Health page and throughout the UI.**

---

## G. Viva Questions (15 Likely Questions)

| # | Question | Concise Answer |
|---|----------|----------------|
| 1 | **How does CodeGuard prevent cheating?** | It doesn't "prevent" cheating — it generates review signals (hardcoding heuristics, sequence anomalies, resource anomalies) for faculty to investigate. No automated verdicts. |
| 2 | **Is the sandbox secure?** | Windows Job Objects provide partial isolation (process tree, memory, process count). Not a full sandbox: no filesystem virtualization, no network isolation. Suitable for academic use, not production multi-tenant hosting. |
| 3 | **Why Windows Job Objects, not Docker/cgroups?** | Project requirement: Windows-native execution environment demonstrating OS-level isolation primitives. Job Objects are the Windows equivalent of cgroups. |
| 4 | **How does the hardcoding heuristic work?** | Two signals: (A) Public tests pass but hidden tests fail systematically; (B) Identical output hash across tests with distinct inputs AND distinct expected answers. Produces `REVIEW_RECOMMENDED`, not a verdict. |
| 5 | **What happens on infinite loop?** | Wall-clock deadline (default 3s) terminates the process tree via Job Object. Generates `TIME_LIMIT_TERMINATION` alert. CPU time is measured but not enforced. |
| 6 | **Can students see hidden test cases?** | No. Student-facing `/problems/{id}` returns only public sample tests. Hidden tests only visible via faculty `/problems/{id}/test-cases` endpoint. |
| 7 | **How is trust score calculated?** | Per-workspace adaptive score. Starts at 100. Sequence alerts, hardcoding signals, resource violations decrease score. Score changes are auditable with reasons. Never weakens resource limits. |
| 8 | **Is the simulator running real code?** | No. It's a deterministic browser-side state machine with 7 scenarios. Events are labeled SIMULATED. Never mixed with backend data. |
| 9 | **How are behavioral fingerprints compared?** | Backend endpoint `/analysis/fingerprints/compare?left_id=X&right_id=Y` returns comparable/identical/different/unavailable fields. UI shows side-by-side with clear "Unavailable" for missing metrics. |
| 10 | **Can this run in production?** | Designed for academic demonstration. Limitations (no real auth, partial isolation, MEASURED_ONLY CPU) make it unsuitable for untrusted multi-tenant production use. |
| 11 | **How are submissions queued?** | FastAPI background task queue with configurable concurrency (default 2) and queue depth (default 50). Worker process picks up jobs, executes in Job Object, stores results. |
| 11 | **What if the database is unavailable?** | Health endpoint reports `connectivity: UNREACHABLE`. All DB-dependent endpoints return 503 with `DATABASE_UNAVAILABLE` error envelope. No fabricated data. |
| 12 | **How are compilation errors reported?** | GCC stderr captured and returned in submission detail. Frontend shows compiler output, exit code, duration. Status = `COMPILATION_ERROR`. |
| 13 | **Can faculty add their own sequence rules?** | Yes — `SEQUENCE_RULES_PATH` environment variable points to JSON file with custom rules (rule_id, pattern, risk_level, explanation, creates_alert). |
| 14 | **What's the difference between Run and Submit?** | **Run** = custom stdin, single execution, not judged, `is_custom_run=true`. **Submit** = queued, judged against all stored test cases (public + hidden), `is_custom_run=false`. |
| 15 | **How do you know the system isn't lying about limitations?** | System Health page reads `/api/v1/system/status` which reports capability statuses from a hardcoded vocabulary (ACTIVE/MEASURED_ONLY/PARTIAL/NOT_AVAILABLE/RESERVED). The backend self-reports honestly; no external auditor. |

---

## H. Startup Commands (Windows PowerShell)

### Backend
```powershell
# From D:\PROJECTS\OS
cd D:\PROJECTS\OS
# Activate virtual environment
.\.venv\Scripts\Activate.ps1
# Run migrations (if needed)
python -m alembic upgrade head
# Seed demo problems (idempotent)
python scripts\seed_demo.py
# Start backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend
```powershell
# From D:\PROJECTS\OS\os_frontend
cd D:\PROJECTS\OS\os_frontend
# Install dependencies (first time only)
pnpm install
# Start dev server
$env:PORT='5173'; $env:BASE_PATH='/'; pnpm --filter @workspace/codeguard run dev
# Or production build
$env:PORT='5173'; $env:BASE_PATH='/'; pnpm --filter @workspace/codeguard run build
```

### Health Checks
```powershell
# Backend health
curl.exe http://127.0.0.1:8000/api/v1/health

# System status (capabilities)
curl.exe http://127.0.0.1:8000/api/v1/system/status

# Problems catalog
curl.exe http://127.0.0.1:8000/api/v1/problems?page=1&page_size=10
```

### Run Tests
```powershell
# Backend tests
cd D:\PROJECTS\OS
.\.venv\Scripts\python.exe -m pytest -q

# Frontend typecheck
cd D:\PROJECTS\OS\os_frontend
pnpm --filter @workspace/codeguard run typecheck

# Frontend production build
$env:PORT='5173'; $env:BASE_PATH='/'; pnpm --filter @workspace/codeguard run build
```

---

## Final Notes for Faculty

- **All data shown is real backend data** — no fabricated charts, no fake submissions
- **Limitations are displayed honestly** — not hidden in footnotes
- **The simulator is clearly separated** — SIMULATED badge on every component
- **No security theater** — if a capability says PARTIAL or NOT_AVAILABLE, that's the truth
- **Ready for questions** — architecture, threat model, and limitations are all transparent

---

*Generated as part of CodeGuard stabilization checkpoint (commit `bc90c93`).*