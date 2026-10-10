/**
 * CodeGuard API client — the single place where the frontend talks to the
 * FastAPI backend.
 *
 * Contract source of truth: the backend OpenAPI document served at
 * `{origin}/openapi.json` and the FastAPI routers under `app/api/v1`.
 * Endpoint paths, request bodies and response shapes below were transcribed
 * from that document — nothing is guessed from component names.
 *
 * Configuration: `VITE_API_BASE_URL` (default `http://localhost:8000/api/v1`).
 * The base is normalised so the `/api/v1` prefix can never be duplicated.
 */

import type {
  AlertSummary,
  DashboardResponse,
  EventResponse,
  FingerprintComparison,
  FingerprintResponse,
  HardcodingAnalysisResponse,
  HealthResponse,
  Language,
  Page,
  ProblemCreate,
  ProblemDetail,
  ProblemSummary,
  ProblemUpdate,
  ReportResponse,
  SequenceAnalysisResponse,
  SubmissionAccepted,
  SubmissionDetail,
  SubmissionSummary,
  SystemStatusResponse,
  TestCaseAdmin,
  TestCaseCreate,
  TrustHistoryResponse,
} from './api-types';

export const API_DEFAULT = 'http://localhost:8000/api/v1';

/** Default per-request timeout (ms). Compilation runs can be slower. */
const DEFAULT_TIMEOUT_MS = 20_000;
/** Submission polling interval (ms) — deliberately gentle on the server. */
const POLL_INTERVAL_MS = 750;
/** Submission polling budget (ms). */
const POLL_TIMEOUT_MS = 120_000;

function normalizeBase(raw: string | undefined): string {
  let base = (raw || API_DEFAULT).trim().replace(/\/+$/, '');
  if (!base) return API_DEFAULT;
  // Guarantee exactly one version prefix, whatever the operator configured.
  if (!/\/api\/v\d+$/.test(base)) base = `${base}/api/v1`;
  return base;
}

/** Resolved API base, e.g. `http://localhost:8000/api/v1`. */
export const API_BASE = normalizeBase(import.meta.env.VITE_API_BASE_URL as string | undefined);

/** Origin of the backend (used for `/openapi.json` and `/docs` links). */
export const API_ORIGIN = (() => {
  try {
    return new URL(API_BASE).origin;
  } catch {
    return '';
  }
})();

export type ApiState = 'checking' | 'connected' | 'disconnected';

export type QueryParams = Record<string, string | number | boolean | undefined | null>;

/** Normalised error for every failed request (HTTP, network or timeout). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;
  readonly requestId: string | null;

  constructor(
    message: string,
    options: {
      status: number;
      code: string;
      details?: Record<string, unknown> | null;
      requestId?: string | null;
    },
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.details = options.details ?? null;
    this.requestId = options.requestId ?? null;
  }
}

/** Result of probing the backend: liveness, DB state and capability list. */
export type BackendStatus = {
  state: ApiState;
  baseUrl: string;
  specUrl?: string;
  operations?: number;
  error?: string;
  health?: HealthResponse;
  system?: SystemStatusResponse;
};

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function fallbackCode(status: number): string {
  switch (status) {
    case 400:
      return 'VALIDATION_ERROR';
    case 404:
      return 'NOT_FOUND';
    case 409:
      return 'CONFLICT';
    case 413:
      return 'SOURCE_TOO_LARGE';
    case 422:
      return 'VALIDATION_ERROR';
    case 429:
      return 'RATE_LIMITED';
    case 500:
      return 'INTERNAL_ERROR';
    case 502:
      return 'BAD_GATEWAY';
    case 503:
      return 'SERVICE_UNAVAILABLE';
    case 504:
      return 'GATEWAY_TIMEOUT';
    default:
      return status >= 500 ? 'INTERNAL_ERROR' : 'HTTP_ERROR';
  }
}

type ErrorEnvelope = {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
  request_id?: string;
  detail?: unknown;
};

function toApiError(status: number, payload: unknown, fallbackRequestId: string | null): ApiError {
  let code = fallbackCode(status);
  let message = `The backend returned HTTP ${status}.`;
  let details: Record<string, unknown> | null = null;
  let requestId = fallbackRequestId;

  if (payload && typeof payload === 'object') {
    const envelope = payload as ErrorEnvelope;
    if (envelope.request_id) requestId = envelope.request_id;
    if (envelope.error && typeof envelope.error === 'object') {
      code = envelope.error.code ?? code;
      message = envelope.error.message ?? message;
      details = envelope.error.details ?? null;
    } else if (Array.isArray(envelope.detail)) {
      // FastAPI's default validation payload (no envelope).
      code = 'VALIDATION_ERROR';
      const first = envelope.detail[0] as { msg?: string; loc?: (string | number)[] } | undefined;
      const where = (first?.loc ?? []).slice(1).join('.');
      message = first?.msg ? `${first.msg}${where ? ` (${where})` : ''}` : 'The request failed validation.';
    } else if (typeof envelope.detail === 'string') {
      message = envelope.detail;
    }
  } else if (typeof payload === 'string' && payload.trim()) {
    message = payload.trim().slice(0, 300);
  }

  return new ApiError(message, { status, code, details, requestId });
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  query?: QueryParams;
  signal?: AbortSignal;
  timeoutMs?: number;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const url = new URL(`${API_BASE}${path.startsWith('/') ? path : `/${path}`}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
    }
  }

  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    signal.addEventListener('abort', onAbort);
  }
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (timedOut) {
      throw new ApiError(`The backend did not answer within ${Math.round(timeoutMs / 1000)} seconds.`, {
        status: 0,
        code: 'TIMEOUT',
      });
    }
    if (signal?.aborted || isAbortError(error)) throw error;
    throw new ApiError(`Could not reach the backend at ${API_BASE}.`, { status: 0, code: 'NETWORK_ERROR' });
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    throw toApiError(response.status, payload, response.headers.get('X-Request-ID'));
  }
  return payload as T;
}

/**
 * Human-readable, non-technical explanation of a failed request.
 * Never includes stack traces, environment values or credentials.
 */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'NETWORK_ERROR':
        return `Backend unreachable — ${API_BASE} did not respond. Start the FastAPI server and press retry.`;
      case 'TIMEOUT':
        return `${error.message} The server may still be compiling; retry in a moment.`;
      case 'DATABASE_NOT_CONFIGURED':
        return `Database unavailable — ${error.message}${
          typeof error.details?.reason === 'string' ? ` (${error.details.reason})` : ''
        } Set DATABASE_URL in the backend .env, then retry.`;
      case 'DATABASE_UNAVAILABLE':
        return `Database unreachable — ${error.message}`;
      case 'EXECUTION_QUEUE_FULL':
        return `The execution queue is full — ${error.message}`;
      case 'SOURCE_TOO_LARGE':
        return `Source rejected — ${error.message}`;
      case 'NOT_FOUND':
        return error.message;
      case 'VALIDATION_ERROR':
        return `Request rejected — ${error.message}`;
      default:
        break;
    }
    const suffix = error.requestId ? ` [request ${error.requestId}]` : '';
    return `${error.code}: ${error.message}${suffix}`;
  }
  if (error instanceof Error && isAbortError(error)) return 'Request cancelled.';
  return error instanceof Error ? error.message : 'Unexpected error while calling the backend.';
}

// ---------------------------------------------------------------------------
// Backend probe
// ---------------------------------------------------------------------------

/**
 * Probe liveness + database state, then confirm the OpenAPI contract.
 * The backend explicitly separates application health from database health;
 * both are reported separately and never inferred from each other.
 */
export async function probeBackend(signal?: AbortSignal): Promise<BackendStatus> {
  let health: HealthResponse;
  try {
    health = await request<HealthResponse>('/health', { signal, timeoutMs: 8_000 });
  } catch (error) {
    if (isAbortError(error)) throw error;
    return { state: 'disconnected', baseUrl: API_BASE, error: describeApiError(error) };
  }

  let system: SystemStatusResponse | undefined;
  try {
    system = await request<SystemStatusResponse>('/system/status', { signal, timeoutMs: 8_000 });
  } catch {
    system = undefined; // health answered; status is optional detail
  }

  let specUrl: string | undefined;
  let operations: number | undefined;
  if (API_ORIGIN) {
    try {
      const response = await fetch(`${API_ORIGIN}/openapi.json`, {
        headers: { Accept: 'application/json' },
        ...(signal ? { signal } : {}),
      });
      if (response.ok) {
        const spec = (await response.json()) as { paths?: Record<string, unknown> };
        if (spec && typeof spec.paths === 'object') {
          specUrl = `${API_ORIGIN}/openapi.json`;
          operations = Object.keys(spec.paths).length;
        }
      }
    } catch {
      specUrl = undefined; // contract confirmation is best-effort
    }
  }

  return { state: 'connected', baseUrl: API_BASE, health, system, specUrl, operations };
}

// ---------------------------------------------------------------------------
// Submission lifecycle
// ---------------------------------------------------------------------------

/** Statuses after which polling must stop. */
export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'ACCEPTED',
  'WRONG_ANSWER',
  'COMPILATION_ERROR',
  'RUNTIME_ERROR',
  'TIME_LIMIT_EXCEEDED',
  'MEMORY_LIMIT_EXCEEDED',
  'PROCESS_LIMIT_EXCEEDED',
  'SECURITY_VIOLATION',
  'CANCELLED',
  'INTERNAL_ERROR',
]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export function listProblems(
  params: { page?: number; page_size?: number; search?: string } = {},
  signal?: AbortSignal,
): Promise<Page<ProblemSummary>> {
  return request<Page<ProblemSummary>>('/problems', { query: { page: 1, page_size: 20, ...params }, signal });
}

/** POST /submissions/run — one execution with custom stdin (no judging). */
export function runCustom(
  body: { language: Language; source_code: string; input?: string; workspace_label?: string },
  signal?: AbortSignal,
): Promise<SubmissionDetail> {
  return request<SubmissionDetail>('/submissions/run', {
    method: 'POST',
    body: { input: '', ...body },
    timeoutMs: 60_000,
    signal,
  });
}

/** POST /submissions — queue judging against a problem's test cases (202). */
export function createSubmission(
  body: { problem_id: number; language: Language; source_code: string; workspace_label?: string },
  signal?: AbortSignal,
): Promise<SubmissionAccepted> {
  return request<SubmissionAccepted>('/submissions', { method: 'POST', body, signal });
}

export function getSubmission(id: number | string, signal?: AbortSignal): Promise<SubmissionDetail> {
  return request<SubmissionDetail>(`/submissions/${encodeURIComponent(String(id))}`, { signal });
}

export function listSubmissions(
  params: { page?: number; page_size?: number; workspace_label?: string; status?: string } = {},
  signal?: AbortSignal,
): Promise<Page<SubmissionSummary>> {
  return request<Page<SubmissionSummary>>('/submissions', {
    query: { page: 1, page_size: 20, ...params },
    signal,
  });
}

/**
 * Poll a queued submission until it reaches a terminal status.
 * Uses a single gentle interval and stops as soon as judging finishes.
 */
/**
 * GET /faculty/submissions — every stored row (no workspace-label filter).
 * `GET /submissions` scopes to the caller's own label by default.
 */
export function listAllSubmissions(
  params: { page?: number; page_size?: number; status?: string; problem_id?: number; workspace_label?: string } = {},
  signal?: AbortSignal,
): Promise<Page<SubmissionSummary>> {
  return request<Page<SubmissionSummary>>('/faculty/submissions', {
    query: { page: 1, page_size: 20, ...params },
    signal,
  });
}

export async function pollSubmission(
  id: number,
  options: { signal?: AbortSignal; onStatus?: (detail: SubmissionDetail) => void } = {},
): Promise<SubmissionDetail> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let latest = await getSubmission(id, options.signal);
  options.onStatus?.(latest);
  while (!isTerminalStatus(latest.status)) {
    if (Date.now() > deadline) return latest;
    if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
    latest = await getSubmission(id, options.signal);
    options.onStatus?.(latest);
  }
  return latest;
}

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

export function getSubmissionReport(id: number | string, signal?: AbortSignal): Promise<ReportResponse> {
  return request<ReportResponse>(`/analysis/submissions/${encodeURIComponent(String(id))}/report`, { signal });
}

export function listSubmissionEvents(
  id: number | string,
  params: { page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<Page<EventResponse>> {
  return request<Page<EventResponse>>(`/analysis/submissions/${encodeURIComponent(String(id))}/events`, {
    query: { page: 1, page_size: 50, ...params },
    signal,
  });
}

export function getFingerprint(id: number | string, signal?: AbortSignal): Promise<FingerprintResponse> {
  return request<FingerprintResponse>(`/analysis/submissions/${encodeURIComponent(String(id))}/fingerprint`, {
    signal,
  });
}

export function getSequenceAnalysis(id: number | string, signal?: AbortSignal): Promise<SequenceAnalysisResponse> {
  return request<SequenceAnalysisResponse>(`/analysis/submissions/${encodeURIComponent(String(id))}/sequence`, {
    signal,
  });
}

export function getHardcodingAnalysis(
  id: number | string,
  signal?: AbortSignal,
): Promise<HardcodingAnalysisResponse> {
  return request<HardcodingAnalysisResponse>(`/analysis/submissions/${encodeURIComponent(String(id))}/hardcoding`, {
    signal,
  });
}

/**
 * GET /analysis/fingerprints/compare — informational, pairwise comparison of
 * two submissions' stored fingerprints. The backend returns which shared
 * numeric fields are identical, similar, or different. This is NOT a
 * plagiarism verdict.
 */
export function compareFingerprints(
  leftId: number,
  rightId: number,
  signal?: AbortSignal,
): Promise<FingerprintComparison> {
  return request<FingerprintComparison>('/analysis/fingerprints/compare', {
    query: { left_id: leftId, right_id: rightId },
    signal,
  });
}

// ---------------------------------------------------------------------------
// Faculty surfaces
// ---------------------------------------------------------------------------

export function getDashboard(signal?: AbortSignal): Promise<DashboardResponse> {
  return request<DashboardResponse>('/faculty/dashboard', { signal });
}

export function listAlerts(
  params: { page?: number; page_size?: number; severity?: string; review_status?: string } = {},
  signal?: AbortSignal,
): Promise<Page<AlertSummary>> {
  return request<Page<AlertSummary>>('/faculty/alerts', {
    query: { page: 1, page_size: 10, ...params },
    signal,
  });
}

export function getTrustHistory(
  workspaceLabel: string,
  limit = 50,
  signal?: AbortSignal,
): Promise<TrustHistoryResponse> {
  return request<TrustHistoryResponse>('/faculty/trust-history', {
    query: { workspace_label: workspaceLabel, limit },
    signal,
  });
}

// ---------------------------------------------------------------------------
// Problem & test-case management (faculty/admin)
// ---------------------------------------------------------------------------

export function createProblem(
  payload: ProblemCreate,
  signal?: AbortSignal,
): Promise<ProblemDetail> {
  return request<ProblemDetail>('/problems', {
    method: 'POST',
    body: payload,
    signal,
  });
}

export function updateProblem(
  problemId: number,
  payload: ProblemUpdate,
  signal?: AbortSignal,
): Promise<ProblemDetail> {
  return request<ProblemDetail>(`/problems/${problemId}`, {
    method: 'PATCH',
    body: payload,
    signal,
  });
}

export function deleteProblem(
  problemId: number,
  signal?: AbortSignal,
): Promise<void> {
  return request<void>(`/problems/${problemId}`, {
    method: 'DELETE',
    signal,
  });
}

export function getProblem(
  problemId: number,
  signal?: AbortSignal,
): Promise<ProblemDetail> {
  return request<ProblemDetail>(`/problems/${problemId}`, { signal });
}

export function listProblemTestCases(
  problemId: number,
  signal?: AbortSignal,
): Promise<TestCaseAdmin[]> {
  return request<TestCaseAdmin[]>(`/problems/${problemId}/test-cases`, { signal });
}

export function addTestCase(
  problemId: number,
  payload: { input_data: string; expected_output: string; is_public: boolean; is_generated: boolean; label?: string | null },
  signal?: AbortSignal,
): Promise<TestCaseAdmin> {
  return request<TestCaseAdmin>(`/problems/${problemId}/test-cases`, {
    method: 'POST',
    body: payload,
    signal,
  });
}

export function deleteTestCase(
  problemId: number,
  caseId: number,
  signal?: AbortSignal,
): Promise<void> {
  return request<void>(`/problems/${problemId}/test-cases/${caseId}`, {
    method: 'DELETE',
    signal,
  });
}
