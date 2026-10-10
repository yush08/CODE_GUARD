/**
 * TypeScript mirrors of the CodeGuard FastAPI response schemas.
 *
 * Every type here was transcribed from the live backend OpenAPI document
 * (`GET /openapi.json`) and the Pydantic models in `app/schemas/*`. Optional
 * properties mark fields the backend serialises as `null`.
 *
 * Nothing in this file invents a field: if the backend does not return it,
 * it does not exist here.
 */

export type Language = 'c' | 'cpp';

export type SubmissionStatus =
  | 'QUEUED'
  | 'COMPILING'
  | 'RUNNING'
  | 'ANALYZING'
  | 'ACCEPTED'
  | 'WRONG_ANSWER'
  | 'COMPILATION_ERROR'
  | 'RUNTIME_ERROR'
  | 'TIME_LIMIT_EXCEEDED'
  | 'MEMORY_LIMIT_EXCEEDED'
  | 'PROCESS_LIMIT_EXCEEDED'
  | 'SECURITY_VIOLATION'
  | 'CANCELLED'
  | 'INTERNAL_ERROR';

export type TestStatus =
  | 'ACCEPTED'
  | 'WRONG_ANSWER'
  | 'RUNTIME_ERROR'
  | 'TIME_LIMIT_EXCEEDED'
  | 'MEMORY_LIMIT_EXCEEDED'
  | 'PROCESS_LIMIT_EXCEEDED';

export type ReviewStatus = 'PENDING' | 'UNDER_REVIEW' | 'CONFIRMED' | 'DISMISSED';

export type Difficulty = 'EASY' | 'MEDIUM' | 'HARD';

/** Per-limit capability vocabulary returned by `/api/v1/system/status`. */
export type CapabilityStatus = 'ACTIVE' | 'MEASURED_ONLY' | 'PARTIAL' | 'NOT_AVAILABLE' | 'RESERVED';

/** Generic page envelope used by every list endpoint. */
export interface Pagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface Page<T> {
  items: T[];
  pagination: Pagination;
}

// ---------------------------------------------------------------------------
// Health / system status
// ---------------------------------------------------------------------------
export interface DatabaseHealth {
  configured: boolean;
  config_state: string;
  connectivity: string;
  reason: string;
  display_url?: string | null;
}

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  database: DatabaseHealth;
  database_required_for_full_function?: boolean;
}

export interface CompilerStatus {
  language: string;
  compiler: string;
  available: boolean;
  path?: string | null;
  version?: string | null;
  detail?: string;
}

export interface CapabilityInfo {
  id: string;
  name: string;
  status: CapabilityStatus;
  detail: string;
}

export interface WorkerStatus {
  running: boolean;
  active_executions: number;
  queued_jobs: number;
  max_concurrent_executions: number;
  max_queued_jobs: number;
}

export interface SystemStatusResponse {
  app: string;
  version: string;
  environment: string;
  os: Record<string, unknown>;
  python: Record<string, unknown>;
  auth_mode: string;
  running_as_admin: boolean;
  database: DatabaseHealth;
  compilers: CompilerStatus[];
  supported_languages: string[];
  job_object: { available: boolean; detail: string };
  execution_worker: WorkerStatus;
  capabilities: CapabilityInfo[];
  warnings?: string[];
}

// ---------------------------------------------------------------------------
// Problems / test cases
// ---------------------------------------------------------------------------
export interface TestCaseCounts {
  public: number;
  hidden: number;
  generated: number;
}

export interface ProblemSummary {
  id: number;
  title: string;
  slug: string;
  difficulty: Difficulty;
  visibility: string;
  time_limit_ms: number | null;
  memory_limit_mb: number | null;
  process_limit: number | null;
  test_case_counts: TestCaseCounts;
  created_at: string;
}

export interface TestCasePublic {
  id: number;
  input_data: string;
  expected_output: string;
  label?: string | null;
}

export interface ProblemDetail extends ProblemSummary {
  description: string;
  input_format: string;
  output_format: string;
  constraints: string;
  supported_languages: string[];
  sample_tests?: TestCasePublic[];
  updated_at?: string | null;
}

// Problem creation / update
export interface TestCaseCreate {
  input_data: string;
  expected_output: string;
  is_public: boolean;
  is_generated: boolean;
  label?: string | null;
}

export interface TestCaseAdmin extends TestCaseCreate {
  id: number;
  problem_id: number;
  created_at: string;
}

export interface ProblemCreate {
  title: string;
  slug?: string | null;
  description: string;
  input_format: string;
  output_format: string;
  constraints: string;
  difficulty: Difficulty;
  supported_languages: Language[];
  time_limit_ms?: number | null;
  memory_limit_mb?: number | null;
  process_limit?: number | null;
  visibility: 'PUBLIC' | 'PRIVATE';
  test_cases: TestCaseCreate[];
}

export interface ProblemUpdate {
  title?: string | null;
  description?: string | null;
  input_format?: string | null;
  output_format?: string | null;
  constraints?: string | null;
  difficulty?: Difficulty | null;
  supported_languages?: Language[] | null;
  time_limit_ms?: number | null;
  memory_limit_mb?: number | null;
  process_limit?: number | null;
  visibility?: 'PUBLIC' | 'PRIVATE' | null;
}

export interface TestCaseCounts {
  public: number;
  hidden: number;
  generated: number;
}

export interface TestCasePublic {
  id: number;
  input_data: string;
  expected_output: string;
  label?: string | null;
}

export interface TestCasePublic {
  id: number;
  input_data: string;
  expected_output: string;
  label?: string | null;
}

export interface ProblemDetail extends ProblemSummary {
  description: string;
  input_format: string;
  output_format: string;
  constraints: string;
  supported_languages: string[];
  sample_tests?: TestCasePublic[];
  updated_at?: string | null;
}

// ---------------------------------------------------------------------------
// Submissions / execution
// ---------------------------------------------------------------------------
export interface SubmissionSummary {
  id: number;
  problem_id: number | null;
  language: string;
  status: SubmissionStatus;
  workspace_label: string | null;
  is_custom_run: boolean;
  execution_time_ms: number | null;
  cpu_time_ms: number | null;
  peak_memory_bytes: number | null;
  exit_code: number | null;
  error_summary: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface TestResultPublic {
  id: number;
  test_index: number;
  test_is_public: boolean;
  test_is_generated: boolean;
  status: TestStatus;
  execution_time_ms: number | null;
  memory_bytes: number | null;
  exit_code: number | null;
  error_summary: string | null;
  /** Truncated stdout for public tests; always `null` for hidden tests. */
  output_summary?: string | null;
}

export interface SubmissionDetail extends SubmissionSummary {
  source_code: string;
  compile_output?: string | null;
  test_results?: TestResultPublic[];
}

export interface SubmissionAccepted {
  id: number;
  status: string;
  detail_url: string;
  message?: string;
}

export interface RunRequestBody {
  language: Language;
  source_code: string;
  input?: string;
  workspace_label?: string;
}

export interface SubmitRequestBody {
  problem_id: number;
  language: Language;
  source_code: string;
  workspace_label?: string;
}

// ---------------------------------------------------------------------------
// Analysis / reporting
// ---------------------------------------------------------------------------
export interface FingerprintResponse {
  submission_id: number | string | null;
  analysis_type: string;
  measurement_source: string;
  fingerprint: Record<string, number | string | null | Record<string, number>>;
  measurements: Record<string, string>;
  notes?: string[];
}

export interface EventResponse {
  id: number;
  event_type: string;
  occurred_at: string;
  source: string;
  severity: string;
  details: Record<string, unknown>;
}

export interface SequenceMatch {
  rule_id: string;
  risk_level: string;
  explanation: string;
  creates_alert: boolean;
  matched_at_index: number;
  matched_event_types: string[];
  evidence: Record<string, unknown>[];
}

export interface SequenceAnalysisResponse {
  analysis_type: string;
  risk_level: string;
  rule?: string | null;
  observed_events: string[];
  explanation: string;
  source: string;
  rules_evaluated: number;
  matches?: SequenceMatch[];
}

export interface HardcodingAnalysisResponse {
  analysis_type: string;
  status: string;
  evidence: string[];
  /** Always null by design — the backend never claims a confidence score. */
  confidence: null;
  explanation: string;
  supporting_tests?: Record<string, unknown>[];
  notes?: string[];
}

export interface ReportCompilation {
  attempted: boolean;
  success?: boolean | null;
  compiler?: string | null;
  compiler_version?: string | null;
  compiler_path?: string | null;
  flags?: string | null;
  duration_ms?: number | null;
  output?: string | null;
  failure_reason?: string | null;
}

export interface ReportTestSummary {
  total: number;
  accepted: number;
  wrong_answer: number;
  runtime_error: number;
  time_limit_exceeded: number;
  memory_limit_exceeded: number;
  process_limit_exceeded: number;
  results: TestResultPublic[];
}

export interface ReportTrust {
  workspace_label?: string | null;
  current_score?: number | null;
  monitoring_level?: string | null;
  open_alerts?: number | null;
  note?: string;
}

export interface ReportAlertItem {
  id: number;
  alert_type: string;
  severity: string;
  review_status: string;
  explanation: string;
  created_at: string;
}

export interface ReportResponse {
  generated_at: string;
  submission: SubmissionSummary;
  problem?: Record<string, unknown> | null;
  compilation: ReportCompilation;
  test_summary: ReportTestSummary;
  /** `enforcement` maps each limit to ENFORCED / MEASURED_ONLY / … */
  resource_measurements: Record<string, unknown>;
  analysis: {
    fingerprint?: Record<string, unknown> | null;
    sequence_analysis?: Record<string, unknown> | null;
    hardcoding_analysis?: Record<string, unknown> | null;
  };
  alerts?: ReportAlertItem[];
  trust?: ReportTrust | null;
  limitations?: string[];
}

// ---------------------------------------------------------------------------
// Fingerprint comparison (GET /analysis/fingerprints/compare)
// ---------------------------------------------------------------------------
export interface FingerprintComparison {
  left_submission_id: number;
  right_submission_id: number;
  comparison: {
    comparable_fields: string[];
    unavailable_fields: string[];
    identical_fields: string[];
    similar_fields: string[];
    different_fields: string[];
    explanation: string;
  };
}

// ---------------------------------------------------------------------------
// Faculty surfaces
// ---------------------------------------------------------------------------
export interface DashboardResponse {
  generated_at: string;
  submissions: {
    total: number;
    by_status?: Record<string, number>;
    accepted?: number;
    accepted_rate_percent?: number;
    [key: string]: unknown;
  };
  alerts: {
    total?: number;
    pending_review?: number;
    [key: string]: number | undefined;
  };
  resource_metric_coverage: Record<string, number>;
  recent_submissions: SubmissionSummary[];
  recent_alerts: AlertSummary[];
  note?: string;
}

export interface AlertSummary {
  id: number;
  submission_id: number;
  alert_type: string;
  severity: string;
  review_status: ReviewStatus;
  explanation: string;
  created_at: string;
}

export interface AlertDetail extends AlertSummary {
  evidence_json: Record<string, unknown>;
  reviewed_by?: string | null;
  review_note?: string | null;
  reviewed_at?: string | null;
}

export interface TrustHistoryEntry {
  id: number;
  workspace_label: string;
  previous_score: number;
  new_score: number;
  reason: string;
  related_submission_id?: number | null;
  created_at: string;
}

export interface TrustHistoryResponse {
  workspace_label: string;
  current_score: number;
  monitoring_level: string;
  open_alerts: number;
  history_entries: number;
  formula: Record<string, unknown>;
  entries: TrustHistoryEntry[];
  note?: string;
}
