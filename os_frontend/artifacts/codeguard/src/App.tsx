import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import Editor from '@monaco-editor/react';
import { Link, Route, Switch, useLocation, useParams, useSearch } from 'wouter';
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowRight, Braces,
  Check, ChevronDown, CircleHelp, Clock3, Code2, Cpu, FileCode2, FileSearch,
  Gauge, GitBranch, HardDrive, LayoutDashboard, List, LockKeyhole, Menu, Pause,
  Play, Plus, RefreshCw, RotateCcw, Search, Shield, ShieldAlert, ShieldCheck,
  Terminal, Trash2, Users, Wifi, WifiOff, X, Edit, Eye,
} from 'lucide-react';
import {
  API_BASE, addTestCase, compareFingerprints, createProblem, createSubmission, deleteProblem, deleteTestCase, describeApiError, getDashboard,
  getFingerprint, getProblem, getSubmission, getSubmissionReport, getTrustHistory, isAbortError, isTerminalStatus,
  listAlerts, listAllSubmissions, listProblemTestCases, listProblems, listSubmissionEvents, pollSubmission,
  probeBackend, runCustom, type BackendStatus,
} from '@/lib/codeguard-api';
import type {
  AlertSummary, DashboardResponse, Difficulty, EventResponse, FingerprintComparison, FingerprintResponse,
  Page as ApiPage, ProblemCreate, ProblemDetail, ProblemSummary, ProblemUpdate,
  ReportResponse, SubmissionAccepted, SubmissionDetail, SubmissionSummary,
  TestCaseAdmin, TestCaseCreate, TestCaseCounts,
  TrustHistoryResponse,
} from '@/lib/api-types';
import NotFound from '@/pages/not-found';

type Language = 'c' | 'cpp';
type SimulationKey = 'normal' | 'loop' | 'memory' | 'process' | 'file' | 'hardcoding' | 'sequence';
type DemoSignal = {
  id: string;
  label: string;
  tone: 'safe' | 'watch' | 'alert';
  value: string;
  detail: string;
};
type DemoAnalysis = {
  fingerprint: string;
  sequence: string[];
  hardcoding: string;
  trust: string;
  signals: DemoSignal[];
};
type SimulationRun = {
  id: string;
  scenario: SimulationKey;
  name: string;
  createdAt: string;
  analysis: DemoAnalysis;
};
type ProgramWorkspace = {
  id: string;
  name: string;
  sourceCode: string;
  language: Language;
  problemId: string;
  customInput: string;
  modified: string;
  lastSubmissionId?: string;
};
type StudentWorkspace = {
  id: string;
  name: string;
  programs: ProgramWorkspace[];
  activeProgramId: string;
};
type WorkspaceStore = { students: StudentWorkspace[]; activeStudentId: string; simulations: SimulationRun[] };

/** One backend execution (Run or Submit) plus everything the API returned for it. */
type ExecutionView = {
  kind: 'run' | 'submit';
  accepted?: SubmissionAccepted;
  detail?: SubmissionDetail;
  report?: ReportResponse;
  reportError?: string;
  polling: boolean;
};

/** Generic async state for a single backend read. */
type FeedState<T> = { status: 'idle' | 'loading' | 'ready' | 'error'; data?: T; error?: string };

const starter = `#include <stdio.h>

int main(void) {
    printf("CodeGuard workspace ready.\\n");
    return 0;
}
`;

const createProgram = (number: number): ProgramWorkspace => ({
  id: `program-${Date.now()}-${number}`,
  name: number === 1 ? 'main.c' : `solution-${number}.c`,
  sourceCode: starter,
  language: 'c',
  problemId: '',
  customInput: '',
  modified: 'Not saved yet',
});

const initialStore = (): WorkspaceStore => {
  const program = createProgram(1);
  const student: StudentWorkspace = { id: 'local-workspace', name: 'Demonstration workspace', programs: [program], activeProgramId: program.id };
  return { students: [student], activeStudentId: student.id, simulations: [] };
};

const STORAGE_KEY = 'codeguard-demo-workspace-v1';
function readStore(): WorkspaceStore {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (!value) return initialStore();
    const saved = JSON.parse(value) as WorkspaceStore;
    if (!Array.isArray(saved.students) || !saved.students.length) return initialStore();
    return { ...saved, simulations: Array.isArray(saved.simulations) ? saved.simulations : [] };
  } catch { return initialStore(); }
}

const scenarioDefinitions: Record<SimulationKey, { name: string; description: string; classification: string; metric: string }> = {
  normal: { name: 'Normal execution', description: 'A bounded run with an expected event order.', classification: 'Baseline', metric: 'No policy deviation' },
  loop: { name: 'Infinite loop', description: 'CPU use rises while the process remains active.', classification: 'Resource pressure', metric: 'CPU budget exceeded' },
  memory: { name: 'Memory-heavy allocation', description: 'Memory pressure crosses the demonstration guardrail.', classification: 'Resource pressure', metric: 'Memory guardrail crossed' },
  process: { name: 'Excessive process creation', description: 'A burst of child-process events is observed.', classification: 'Behavioral anomaly', metric: 'Process burst detected' },
  file: { name: 'Restricted file access', description: 'A protected-path request is denied by policy.', classification: 'Policy event', metric: 'Restricted path denied' },
  hardcoding: { name: 'Possible hardcoding', description: 'Output and input behavior diverge from the supplied exercise shape.', classification: 'Review signal', metric: 'Possible hardcoding review' },
  sequence: { name: 'Suspicious event sequence', description: 'An unusual ordering of otherwise observable events appears.', classification: 'Sequence anomaly', metric: 'Sequence requires review' },
};

function makeAnalysis(key: SimulationKey): DemoAnalysis {
  const findings: Record<SimulationKey, DemoSignal[]> = {
    normal: [
      { id: 'budget', label: 'Resource envelope', tone: 'safe', value: 'Within bounds', detail: 'No limit threshold crossed in this deterministic demonstration.' },
      { id: 'policy', label: 'Policy events', tone: 'safe', value: 'None observed', detail: 'No restricted operation appears in the simulated event stream.' },
    ],
    loop: [
      { id: 'cpu', label: 'CPU behavior', tone: 'alert', value: 'Sustained pressure', detail: 'CPU activity remains elevated as the run advances.' },
      { id: 'budget', label: 'Runtime budget', tone: 'alert', value: 'Exceeded', detail: 'Demonstration scenario crosses its local time guardrail.' },
    ],
    memory: [
      { id: 'memory', label: 'Memory behavior', tone: 'alert', value: 'High-water mark', detail: 'Simulated working set grows past the demonstration threshold.' },
      { id: 'allocation', label: 'Allocation pattern', tone: 'watch', value: 'Rapid growth', detail: 'Repeated allocation events are shown for teaching purposes.' },
    ],
    process: [
      { id: 'process', label: 'Process activity', tone: 'alert', value: 'Creation burst', detail: 'A process-creation rate anomaly is simulated.' },
      { id: 'policy', label: 'Execution policy', tone: 'watch', value: 'Review needed', detail: 'This local scenario illustrates containment signals, not a real sandbox.' },
    ],
    file: [
      { id: 'file', label: 'Filesystem policy', tone: 'alert', value: 'Access denied', detail: 'A protected-path request was denied in the simulated event stream.' },
      { id: 'scope', label: 'Workspace scope', tone: 'safe', value: 'Contained', detail: 'No host file is read or modified by this simulation.' },
    ],
    hardcoding: [
      { id: 'review', label: 'Hardcoding review', tone: 'watch', value: 'Possible signal', detail: 'This is a heuristic teaching cue; it is not a finding about submitted code.' },
      { id: 'coverage', label: 'Input variation', tone: 'watch', value: 'Low variation', detail: 'The demonstration stream shows limited response to varied inputs.' },
    ],
    sequence: [
      { id: 'sequence', label: 'Sequence analysis', tone: 'alert', value: 'Unusual order', detail: 'A policy event follows a high-risk action in the deterministic stream.' },
      { id: 'fingerprint', label: 'Behavior fingerprint', tone: 'watch', value: 'Baseline drift', detail: 'The sequence differs from this simulator’s normal demonstration path.' },
    ],
  };
  const signal: Record<SimulationKey, string> = {
    normal: 'bounded-run / stdout / exit',
    loop: 'cpu-burst / no-exit / timeout',
    memory: 'alloc-ramp / rss-growth / guardrail',
    process: 'spawn-burst / child-count / containment',
    file: 'path-request / policy-deny / continue',
    hardcoding: 'input-variation / output-repeat / review',
    sequence: 'spawn / restricted-path / output',
  };
  const sequences: Record<SimulationKey, string[]> = {
    normal: ['process.start', 'stdin.read', 'stdout.write', 'process.exit'],
    loop: ['process.start', 'cpu.sample ↑', 'cpu.sample ↑', 'runtime.limit'],
    memory: ['process.start', 'allocation × 4', 'working-set ↑', 'memory.guardrail'],
    process: ['process.start', 'child.spawn × 5', 'process-rate ↑', 'containment.signal'],
    file: ['process.start', 'path.request', 'policy.deny', 'process.continue'],
    hardcoding: ['input.variant A', 'stdout.capture', 'input.variant B', 'output.similarity review'],
    sequence: ['process.start', 'child.spawn', 'restricted-path.request', 'sequence.review'],
  };
  return {
    fingerprint: signal[key], sequence: sequences[key],
    hardcoding: key === 'hardcoding' ? 'Possible signal · review manually' : 'No signal in this scenario',
    trust: key === 'normal' ? 'Stable · demonstration baseline' : 'Review · demonstration adjustment',
    signals: findings[key],
  };
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatBytes(value: unknown): string {
  const size = typeof value === 'number' && Number.isFinite(value) ? value : null;
  if (size === null) return 'not measured';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function isNullish(value: unknown): boolean {
  return value === null || value === undefined;
}

/** Compact signature of the backend's stored fingerprint (nulls stay visible). */
function fingerprintSignature(report: ReportResponse): string {
  const stored = report.analysis.fingerprint as { fingerprint?: Record<string, unknown> } | null | undefined;
  const metrics = stored?.fingerprint;
  if (!metrics) return 'No fingerprint recorded for this submission';
  const parts = [
    `exit ${isNullish(metrics.exit_code) ? 'not measured' : String(metrics.exit_code)}`,
    `${isNullish(metrics.execution_time_ms) ? 'not measured' : String(metrics.execution_time_ms)} ms wall`,
    `${isNullish(metrics.cpu_time_ms) ? 'not measured' : String(metrics.cpu_time_ms)} ms cpu`,
    formatBytes(metrics.peak_memory_bytes),
  ];
  if (!isNullish(metrics.process_count)) parts.push(`${String(metrics.process_count)} peak proc`);
  return parts.join(' · ');
}

type SignalTone = DemoSignal['tone'];

const RISK_TONE: Record<string, SignalTone> = {
  NONE: 'safe', LOW: 'safe', MEDIUM: 'watch', HIGH: 'alert', CRITICAL: 'alert',
};

/**
 * Map a backend `ReportResponse` onto the four signal panels.
 * Every value comes straight from the API; missing data is stated, never invented.
 */
function backendSignals(report: ReportResponse): DemoAnalysis {
  const sequence = report.analysis.sequence_analysis as
    | { observed_events?: string[]; risk_level?: string; explanation?: string; rule?: string | null }
    | null
    | undefined;
  const hardcoding = report.analysis.hardcoding_analysis as
    | { status?: string; explanation?: string }
    | null
    | undefined;
  const trust = report.trust;
  const enforcement = (report.resource_measurements?.enforcement ?? null) as Record<string, string> | null;
  const spawnMs = report.resource_measurements?.spawn_ms;
  const alerts = report.alerts ?? [];

  const signals: DemoSignal[] = [];
  if (enforcement) {
    const cpu = enforcement.cpu_time_limit ?? 'unknown';
    const wall = enforcement.wall_clock_limit ?? 'unknown';
    const anyEnforced = Object.values(enforcement).some((value) => value === 'ENFORCED');
    signals.push({
      id: 'limits',
      label: 'Execution limits',
      tone: anyEnforced ? 'safe' : 'watch',
      value: `wall ${wall} · cpu ${cpu}`,
      detail:
        'ENFORCED means the engine stopped the run; MEASURED_ONLY means the value was observed without a working cap. Job Objects are not a full sandbox.',
    });
  }
  signals.push({
    id: 'events',
    label: 'Behavior events',
    tone: alerts.length ? 'watch' : 'safe',
    value: alerts.length ? `${alerts.length} alert${alerts.length === 1 ? '' : 's'} recorded` : 'No alerts recorded',
    detail: alerts.length
      ? alerts.map((alert) => `${alert.alert_type}: ${alert.explanation}`).join(' ')
      : 'Stored behavior events produced no security alert for this submission.',
  });
  signals.push({
    id: 'sequence',
    label: 'Sequence analysis',
    tone: RISK_TONE[sequence?.risk_level ?? ''] ?? 'safe',
    value: sequence?.risk_level
      ? `${sequence.risk_level}${sequence.rule ? ` · ${sequence.rule}` : ''}`
      : 'No rule matched',
    detail: sequence?.explanation ?? 'No sequence analysis was stored for this submission.',
  });
  signals.push({
    id: 'hardcoding',
    label: 'Possible hardcoding indicator',
    tone: hardcoding?.status === 'REVIEW_RECOMMENDED' ? 'watch' : 'safe',
    value: hardcoding?.status ?? 'INSUFFICIENT_DATA',
    detail: `${hardcoding?.explanation ?? 'No hardcoding analysis was stored for this submission.'} This is a heuristic review cue, not proof of misconduct.`,
  });
  if (trust) {
    signals.push({
      id: 'trust',
      label: 'Adaptive trust',
      tone: trust.monitoring_level === 'HIGH' ? 'alert' : trust.monitoring_level === 'ELEVATED' ? 'watch' : 'safe',
      value: `${isNullish(trust.current_score) ? 'not scored' : trust.current_score} · ${trust.monitoring_level ?? 'not scored'}`,
      detail: trust.note ?? 'Workspace-scoped score; it never changes resource limits.',
    });
  }
  if (typeof spawnMs === 'number') {
    signals.push({
      id: 'spawn',
      label: 'Process creation delay',
      tone: 'safe',
      value: `${spawnMs} ms`,
      detail: 'Time Windows spent creating the process (antivirus first-use analysis can add seconds). Reported separately from execution time.',
    });
  }

  return {
    fingerprint: fingerprintSignature(report),
    sequence: sequence?.observed_events?.length ? sequence.observed_events : ['No events recorded'],
    hardcoding: hardcoding?.status ? `${hardcoding.status} · review cue, not a verdict` : 'No hardcoding analysis stored',
    trust: trust ? `${isNullish(trust.current_score) ? 'not scored' : trust.current_score} · ${trust.monitoring_level ?? 'unknown'}` : 'Not recorded for this submission',
    signals,
  };
}

const TERMINAL_TONE: Record<string, SignalTone> = {
  ACCEPTED: 'safe',
  WRONG_ANSWER: 'watch',
  COMPILATION_ERROR: 'watch',
  RUNTIME_ERROR: 'watch',
  TIME_LIMIT_EXCEEDED: 'alert',
  MEMORY_LIMIT_EXCEEDED: 'alert',
  PROCESS_LIMIT_EXCEEDED: 'alert',
  SECURITY_VIOLATION: 'alert',
  CANCELLED: 'watch',
  INTERNAL_ERROR: 'alert',
};

function App() {
  const [store, setStore] = useState<WorkspaceStore>(readStore);
  const [api, setApi] = useState<BackendStatus>({ state: 'checking', baseUrl: API_BASE });
  const [apiError, setApiError] = useState('');
  const [execution, setExecution] = useState<ExecutionView | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [location] = useLocation();
  const pollController = useRef<AbortController | null>(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  }, [store]);

  const refreshApi = useCallback(async () => {
    setApi((current) => ({ ...current, state: 'checking', error: undefined }));
    const controller = new AbortController();
    try {
      setApi(await probeBackend(controller.signal));
    } catch (error) {
      if (isAbortError(error)) return;
      setApi({ state: 'disconnected', baseUrl: API_BASE, error: describeApiError(error) });
    }
  }, []);

  // One probe on mount, then only on explicit retry — no health-polling loop.
  useEffect(() => {
    const controller = new AbortController();
    probeBackend(controller.signal)
      .then(setApi)
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setApi({ state: 'disconnected', baseUrl: API_BASE, error: describeApiError(error) });
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => pollController.current?.abort(), []);

  useEffect(() => {
    setExecution(null);
    setApiError('');
  }, [location]);

  const student = store.students.find((item) => item.id === store.activeStudentId) ?? store.students[0];
  const program = student?.programs.find((item) => item.id === student.activeProgramId) ?? student?.programs[0];
  const activeSimulation = store.simulations[0];

  const updateProgram = (patch: Partial<ProgramWorkspace>) => {
    if (!student || !program) return;
    setExecution(null);
    setStore((current) => ({
      ...current,
      students: current.students.map((item) => item.id !== student.id ? item : {
        ...item,
        programs: item.programs.map((file) => file.id === program.id ? { ...file, ...patch, modified: 'Local changes' } : file),
      }),
    }));
  };

  const addStudent = () => {
    const number = store.students.length + 1;
    const label = window.prompt('Name this local demonstration workspace', `Demonstration workspace ${number}`);
    if (!label?.trim()) return;
    const file = createProgram(1);
    const item = { id: `workspace-${Date.now()}`, name: label.trim(), programs: [file], activeProgramId: file.id };
    setStore((current) => ({ ...current, students: [...current.students, item], activeStudentId: item.id }));
  };

  const addProgram = () => {
    if (!student) return;
    const file = createProgram(student.programs.length + 1);
    setStore((current) => ({
      ...current,
      students: current.students.map((item) => item.id === student.id ? { ...item, programs: [...item.programs, file], activeProgramId: file.id } : item),
    }));
  };

  const updateStudent = (id: string) => {
    setExecution(null);
    setStore((current) => ({ ...current, activeStudentId: id }));
  };
  const updateActiveProgram = (id: string) => {
    if (!student) return;
    setStore((current) => ({ ...current, students: current.students.map((item) => item.id === student.id ? { ...item, activeProgramId: id } : item) }));
    setExecution(null);
  };

  const rememberSubmission = (submissionId: string) => {
    if (!student || !program) return;
    setStore((current) => ({
      ...current,
      students: current.students.map((item) => item.id !== student.id ? item : {
        ...item,
        programs: item.programs.map((file) => file.id === program.id ? { ...file, lastSubmissionId: submissionId } : file),
      }),
    }));
  };

  /** Run = one execution with custom stdin. Submit = queued judging + gentle polling. */
  const execute = async (kind: 'run' | 'submit') => {
    if (!program || remoteBusy) return;
    if (kind === 'submit' && !Number(program.problemId)) {
      setApiError('Select a problem first: the backend requires problem_id to judge a submission.');
      return;
    }
    const workspaceLabel = (student?.name || 'local-workspace').slice(0, 120);

    pollController.current?.abort();
    const controller = new AbortController();
    pollController.current = controller;

    setRemoteBusy(true);
    setApiError('');
    setExecution({ kind, polling: kind === 'submit' });

    const loadReport = async (id: number) => {
      try {
        const report = await getSubmissionReport(id, controller.signal);
        setExecution((current) => (current ? { ...current, report } : current));
      } catch (error) {
        if (isAbortError(error)) return;
        setExecution((current) => (current ? { ...current, reportError: describeApiError(error) } : current));
      }
    };

    try {
      if (kind === 'run') {
        const detail = await runCustom({
          language: program.language,
          source_code: program.sourceCode,
          input: program.customInput,
          workspace_label: workspaceLabel,
        }, controller.signal);
        rememberSubmission(String(detail.id));
        if (isTerminalStatus(detail.status)) {
          setExecution({ kind, detail, polling: false });
          await loadReport(detail.id);
        } else {
          setExecution({ kind, detail, polling: true });
          const final = await pollSubmission(detail.id, {
            signal: controller.signal,
            onStatus: (next) => setExecution({ kind, detail: next, polling: !isTerminalStatus(next.status) }),
          });
          setExecution({ kind, detail: final, polling: false });
          await loadReport(final.id);
        }
      } else {
        const accepted = await createSubmission({
          problem_id: Number(program.problemId),
          language: program.language,
          source_code: program.sourceCode,
          workspace_label: workspaceLabel,
        }, controller.signal);
        rememberSubmission(String(accepted.id));
        setExecution({ kind, accepted, polling: true });
        const final = await pollSubmission(accepted.id, {
          signal: controller.signal,
          onStatus: (next) => setExecution({ kind, accepted, detail: next, polling: !isTerminalStatus(next.status) }),
        });
        setExecution({ kind, accepted, detail: final, polling: false });
        await loadReport(final.id);
      }
    } catch (error) {
      if (isAbortError(error)) return;
      setApiError(describeApiError(error));
      setExecution((current) => (current ? { ...current, polling: false } : current));
    } finally {
      if (pollController.current === controller) pollController.current = null;
      setRemoteBusy(false);
    }
  };

  const database = api.health?.database;
  const dbState = api.state === 'checking' || !api.health
    ? (api.state === 'disconnected' ? 'disconnected' : 'checking')
    : database?.configured && database.connectivity !== 'UNREACHABLE'
      ? 'connected'
      : 'disconnected';
  const dbLabel = dbState === 'checking'
    ? 'DB CHECKING'
    : dbState === 'connected'
      ? 'DB READY'
      : database?.configured
        ? 'DB UNREACHABLE'
        : 'DB UNAVAILABLE';

  const nav = <Navigation current={location} onNavigate={() => setMobileNav(false)} />;
  return (
    <div className="app-shell tech-grid" style={styles.app}>
      <aside className={`desktop-nav ${mobileNav ? 'mobile-open' : ''}`} style={styles.sidebar}>
        <div style={styles.brand}>
          <div style={styles.brandMark}><Shield size={18} strokeWidth={2.2} /></div>
          <div><div style={styles.brandName}>codeguard<span style={{ color: '#77dda6' }}>.</span></div><div style={styles.brandCaption}>EXECUTION OBSERVABILITY</div></div>
        </div>
        <div style={styles.navLabel}>WORKSPACE</div>
        {nav}
        <div style={{ marginTop: 'auto' }}>
      <div style={styles.sideNote}>
            <div style={styles.sideNoteTop}><span className="live-dot" /> LOCAL DEMO</div>
            <p className="side-note-copy">Editor and simulator run in this browser. Compilation and execution happen in the connected Windows backend.</p>
          </div>
          <div style={styles.sideFooter}><span>FACULTY DEMONSTRATION</span><span>v0.1</span></div>
        </div>
      </aside>
      {mobileNav && <button aria-label="Close navigation" onClick={() => setMobileNav(false)} className="mobile-scrim" data-testid="button-close-navigation" />}
      <main className="shell-main" style={styles.main}>
        <header style={styles.topbar}>
          <div style={styles.topLeft}>
            <button className="mobile-nav-toggle" aria-label="Open navigation" onClick={() => setMobileNav(true)} data-testid="button-open-navigation"><Menu size={17} /></button>
            <div style={styles.breadcrumb}><span>CODEGUARD</span><span style={{ color: '#424a45' }}>/</span><strong>{pageName(location)}</strong></div>
          </div>
          <div style={styles.topRight}>
            <div className={`api-status ${api.state}`} data-testid="status-api-connection" title={api.baseUrl}>
              <span className="status-dot" />
              {api.state === 'connected' ? 'CONNECTED' : api.state === 'checking' ? 'CHECKING' : 'DISCONNECTED'}
            </div>
            <div className={`api-status ${dbState}`} data-testid="status-database" title={database?.reason ?? 'Reported by GET /api/v1/health'}>
              <span className="status-dot" />
              {dbLabel}
            </div>
            <button className="refreshButton" onClick={refreshApi} title="Retry backend probe" aria-label="Retry API connection" data-testid="button-refresh-api"><RefreshCw size={13} /></button>
            <div style={styles.separator} />
            <div className="demo-pill" style={styles.demoPill}><span>DEMO MODE</span></div>
          </div>
        </header>

        {/*
          Routes render through a children function instead of `component={() => …}`:
          an inline component function gets a new identity on every App render, which
          would remount the whole page (and Monaco) on every keystroke and reset
          per-tab UI state. The JSX below keeps a stable element type, so the page
          instances persist and only receive new props.
        */}
        <Switch>
          <Route path="/">{() => <AdminPage api={api} workspaceLabel={student?.name ?? ''} />}</Route>
          <Route path="/compiler">{() => <CompilerPage
            api={api} student={student} program={program} store={store} activeSimulation={activeSimulation}
            execution={execution} remoteBusy={remoteBusy} apiError={apiError}
            updateProgram={updateProgram} updateStudent={updateStudent} updateActiveProgram={updateActiveProgram}
            addStudent={addStudent} addProgram={addProgram} execute={execute}
          />}</Route>
          <Route path="/admin">{() => <AdminPage api={api} workspaceLabel={student?.name ?? ''} />}</Route>
          <Route path="/submissions">{() => <SubmissionsPage api={api} />}</Route>
          <Route path="/compare">{() => <SubmissionComparisonPage api={api} />}</Route>
          <Route path="/alerts">{() => <SecurityAlertsPage api={api} />}</Route>
          <Route path="/analytics">{() => <BehaviorAnalyticsPage api={api} />}</Route>
          <Route path="/trust">{() => <TrustRiskPage api={api} workspaceLabel={student?.name ?? ''} />}</Route>
          <Route path="/simulation">{() => <SimulationPage store={store} setStore={setStore} />}</Route>
          <Route path="/health">{() => <SystemHealthPage api={api} onRetry={refreshApi} />}</Route>
          <Route path="/reports">{() => <ReportsPage store={store} api={api} />}</Route>
          <Route path="/submissions/:id">{() => <SubmissionRoute store={store} api={api} />}</Route>
          <Route path="/problems">{() => <ProblemsPage api={api} />}</Route>
          <Route component={NotFound} />
        </Switch>
      </main>
    </div>
  );
}

function Navigation({ current, onNavigate }: { current: string; onNavigate: () => void }) {
  const groups = [
    {
      label: 'FACULTY CONSOLE',
      items: [
        { href: '/admin', label: 'Admin Dashboard', icon: LayoutDashboard },
        { href: '/submissions', label: 'Submissions', icon: FileCode2 },
        { href: '/compare', label: 'Submission Comparison', icon: GitBranch },
        { href: '/alerts', label: 'Security Alerts', icon: ShieldAlert },
        { href: '/analytics', label: 'Behavior Analytics', icon: Activity },
        { href: '/trust', label: 'Trust & Risk History', icon: Gauge },
        { href: '/problems', label: 'Manage Problems', icon: FileCode2 },
      ],
    },
    {
      label: 'DEVELOPMENT WORKSPACE',
      items: [
        { href: '/compiler', label: 'Compiler', icon: Code2 },
        { href: '/simulation', label: 'Behavior Simulator', icon: Activity },
        { href: '/health', label: 'System Health', icon: Cpu },
      ],
    },
  ];
  return <nav aria-label="Primary navigation" style={styles.nav}>
    {groups.map((group) => <div key={group.label} className="nav-group">
      <div className="nav-group-label">{group.label}</div>
      {group.items.map(({ href, label, icon: Icon }) => {
        const active = current === href || current.startsWith(`${href}/`);
        return <Link key={href} href={href} onClick={onNavigate} className={`nav-item ${active ? 'active' : ''}`} data-testid={`link-nav-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}>
          <Icon size={16} strokeWidth={1.8} /><span>{label}</span>{active && <span className="nav-active-mark" />}
        </Link>;
      })}
    </div>)}
  </nav>;
}

function pageName(path: string) {
  if (path.startsWith('/submissions/')) return 'SUBMISSION DETAIL';
  return ({
    '/': 'ADMIN DASHBOARD', '/admin': 'ADMIN DASHBOARD', '/compiler': 'COMPILER',
    '/submissions': 'SUBMISSIONS', '/compare': 'SUBMISSION COMPARISON',
    '/alerts': 'SECURITY ALERTS', '/analytics': 'BEHAVIOR ANALYTICS', '/trust': 'TRUST & RISK HISTORY',
    '/simulation': 'BEHAVIOR SIMULATOR', '/health': 'SYSTEM HEALTH', '/reports': 'REPORTS',
    '/problems': 'MANAGE PROBLEMS',
  } as Record<string, string>)[path] ?? 'CODEGUARD';
}

function PageHeading({ eyebrow, title, detail, trailing }: { eyebrow: string; title: string; detail: string; trailing?: ReactNode }) {
  return <div style={styles.pageHeading} className="page-heading fade-in">
    <div><div style={styles.eyebrow}>{eyebrow}</div><h1 style={styles.pageTitle}>{title}</h1><p style={styles.pageDetail}>{detail}</p></div>
    {trailing && <div className="heading-trailing" style={styles.headingTrailing}>{trailing}</div>}
  </div>;
}

const CAPABILITY_TONE: Record<string, string> = {
  ACTIVE: '#87d6a1',
  MEASURED_ONLY: '#d0b476',
  PARTIAL: '#9eb4c1',
  NOT_AVAILABLE: '#7f8a81',
  RESERVED: '#7f8a81',
};

function ApiNotice({ api, compact = false }: { api: BackendStatus; compact?: boolean }) {
  const disconnected = api.state === 'disconnected';
  const database = api.health?.database;
  return <div style={{ ...styles.apiNotice, ...(compact ? styles.apiNoticeCompact : {}) }} data-testid="text-backend-contract-state">
    <div style={styles.noticeIcon}>{disconnected ? <WifiOff size={15} /> : <Wifi size={15} />}</div>
    <div style={{ minWidth: 0, flex: 1 }}>
      <div style={styles.noticeTitle}>{disconnected ? 'Backend not reachable' : api.state === 'checking' ? 'Checking the backend' : 'Backend connected'}</div>
      <div style={styles.noticeDescription}>
        {disconnected
          ? `Editing and local simulations still work. Execution and backend reports stay disabled until ${api.baseUrl} answers. ${api.error ?? ''}`
          : api.state === 'checking'
            ? `Probing ${api.baseUrl} …`
            : `API base ${api.baseUrl}${api.specUrl ? ` · OpenAPI contract ${api.operations ?? ''} paths` : ''}. Only documented operations are called.`}
      </div>
      {!compact && database && <div style={styles.endpointText}>Database · {database.config_state} · {database.connectivity} — {database.reason}</div>}
      {!compact && api.system && <div style={styles.capabilityList}>
        <div style={styles.capabilityHead}>CAPABILITIES AS REPORTED BY THE BACKEND</div>
        {api.system.capabilities.map((capability) => <div key={capability.id} style={styles.capabilityRow} title={capability.detail}>
          <span style={styles.capabilityName}>{capability.name}</span>
          <span className="mono" style={{ color: CAPABILITY_TONE[capability.status] ?? '#87948a', fontSize: 8 }}>{capability.status}</span>
        </div>)}
        {(api.system.warnings ?? []).map((warning) => <div key={warning} style={styles.capabilityWarning}>• {warning}</div>)}
      </div>}
    </div>
    {api.state === 'connected' && <Check size={14} color="#75d9a3" />}
  </div>;
}

function CompilerPage(props: {
  api: BackendStatus; student?: StudentWorkspace; program?: ProgramWorkspace; store: WorkspaceStore;
  activeSimulation?: SimulationRun; execution: ExecutionView | null;
  remoteBusy: boolean; apiError: string;
  updateProgram: (patch: Partial<ProgramWorkspace>) => void; updateStudent: (id: string) => void;
  updateActiveProgram: (id: string) => void; addStudent: () => void; addProgram: () => void;
  execute: (kind: 'run' | 'submit') => void;
}) {
  const { api, student, program, store, activeSimulation, execution } = props;
  const [rightTab, setRightTab] = useState<'problem' | 'signals'>('problem');
  const [consoleTab, setConsoleTab] = useState<'output' | 'input'>('output');
  const [expanded, setExpanded] = useState(true);
  const submitNeedsProblem = !Number(program?.problemId);
  return <div style={styles.content}>
    <PageHeading eyebrow="WORKSPACE / C & C++" title="Compiler workspace" detail="Write C or C++, run it with your own input, or submit it for judging." trailing={<span style={styles.localBadge}><span className="live-dot" /> LOCAL WORKSPACE</span>} />
    <div style={styles.workspaceShell}>
      <div style={styles.tabRows} className="tabRows mobile-scroll">
        <div className="tabRow" style={styles.tabRow}>
          <div style={styles.tabRowLabel}><Users size={12} /> WORKSPACES</div>
          {store.students.map((item, index) => <button key={item.id} onClick={() => props.updateStudent(item.id)} className={`workspace-tab ${item.id === student?.id ? 'selected' : ''}`} data-testid={`tab-workspace-${item.id}`}>
            <span className="workspace-index">{String(index + 1).padStart(2, '0')}</span>{item.name}<span className="demo-label">DEMO LABEL</span>
          </button>)}
          <button className="small-add" onClick={props.addStudent} title="Add local demonstration workspace" data-testid="button-add-workspace"><Plus size={14} /></button>
        </div>
        {student && <div className="tabRow" style={styles.tabRow}>
          <div style={styles.tabRowLabel}><FileCode2 size={12} /> PROGRAMS</div>
          {student.programs.map((file) => <button key={file.id} onClick={() => props.updateActiveProgram(file.id)} className={`program-tab ${file.id === program?.id ? 'selected' : ''}`} data-testid={`tab-program-${file.id}`}>
            <span className="file-dot" />{file.name}{file.language === 'cpp' && <span className="file-ext">C++</span>}
          </button>)}
          <button className="small-add" onClick={props.addProgram} title="New local program" data-testid="button-add-program"><Plus size={14} /></button>
          <div style={{ marginLeft: 'auto', color: '#788079', fontSize: 10 }} className="mono">SAVED LOCALLY</div>
        </div>}
      </div>

      <div className="workspace-columns" style={styles.workspaceGrid}>
        <section style={styles.editorColumn}>
          <div style={styles.panelHead}>
            <div style={styles.panelHeadTitle}><Braces size={14} color="#7fdaa7" /> <span>{program?.name ?? 'No program selected'}</span><span className="language-chip">{program?.language === 'cpp' ? 'C++' : 'C'}</span></div>
            <div style={styles.editorToolbar}>
              <select value={program?.language ?? 'c'} onChange={(event) => {
                const language = event.target.value as Language;
                const extension = language === 'cpp' ? 'cpp' : 'c';
                props.updateProgram({ language, name: program?.name.replace(/\.(c|cc|cpp|cxx)$/i, `.${extension}`) ?? `main.${extension}` });
              }} style={styles.languageSelect} aria-label="Select language" data-testid="select-language">
                <option value="c">C</option><option value="cpp">C++</option>
              </select>
              <span style={styles.fileSaved}>{program?.modified ?? 'LOCAL'}</span>
            </div>
          </div>
          <div className="monaco-wrap" style={styles.monacoFrame}>
            <Editor
              height="100%"
              language={program?.language === 'cpp' ? 'cpp' : 'c'}
              theme="vs-dark"
              value={program?.sourceCode ?? ''}
              onChange={(value: string | undefined) => props.updateProgram({ sourceCode: value ?? '' })}
              options={{
                automaticLayout: true, minimap: { enabled: false }, fontFamily: "'DM Mono', 'JetBrains Mono', monospace",
                fontSize: 13, lineHeight: 22, padding: { top: 17, bottom: 16 }, scrollBeyondLastLine: false,
                renderLineHighlight: 'gutter', roundedSelection: false, wordWrap: 'on', glyphMargin: false,
                folding: true, lineNumbersMinChars: 3, overviewRulerBorder: false, cursorBlinking: 'smooth',
                suggestOnTriggerCharacters: true, tabSize: 4,
              }}
              onMount={(editor: { focus: () => void }) => editor.focus()}
              data-testid="editor-source-code"
            />
          </div>
          <div style={styles.editorBottom}><span><GitBranch size={12} /> local/demo</span><span>UTF-8</span><span>{program?.sourceCode.split('\n').length ?? 0} lines</span><span style={{ marginLeft: 'auto' }}>C / C++ · MONACO</span></div>
          <div style={styles.console}>
            <div style={styles.consoleHeader}>
              <div style={styles.consoleTabs}>
                <button onClick={() => setConsoleTab('output')} className={`console-tab ${consoleTab === 'output' ? 'selected' : ''}`} data-testid="tab-console-output"><Terminal size={13} /> EXECUTION</button>
                <button onClick={() => setConsoleTab('input')} className={`console-tab ${consoleTab === 'input' ? 'selected' : ''}`} data-testid="tab-console-input">STDIN</button>
              </div>
              {execution && <span className="result-tag"><span className="live-dot" /> BACKEND {execution.kind.toUpperCase()} · {execution.detail?.status ?? execution.accepted?.status ?? (execution.polling ? 'QUEUED' : 'NO RESULT')}</span>}
              {props.apiError && <span className="result-tag danger"><AlertTriangle size={11} /> REQUEST FAILED</span>}
            </div>
            <div style={styles.consoleBody}>
              {consoleTab === 'input' ? <textarea value={program?.customInput ?? ''} onChange={(event) => props.updateProgram({ customInput: event.target.value })} placeholder="stdin forwarded to the backend on every run." style={styles.stdinArea} data-testid="input-custom-stdin" /> :
                props.apiError ? <div style={styles.consoleMessageError}>{props.apiError}</div> :
                  execution ? <ExecutionResult execution={execution} busy={props.remoteBusy} /> :
                    <div style={styles.consoleEmpty}><span className="console-prompt">$</span> {props.remoteBusy ? 'Waiting for the backend…' : 'No backend execution in this workspace yet.'}<span style={styles.consoleFootnote}> · Local editor changes are never presented as execution output.</span></div>}
            </div>
            <div style={styles.consoleActions}>
              <div className="mono" style={{ color: '#747d76', fontSize: 10 }}>{program?.lastSubmissionId ? <Link href={`/submissions/${program.lastSubmissionId}`} className="inlineLink">LAST SUBMISSION · {program.lastSubmissionId} <ArrowRight size={11} /></Link> : 'NO SUBMISSION ID RETURNED'}</div>
              <div style={{ display: 'flex', gap: 7 }}>
                <button className="action-button secondary" onClick={() => props.execute('run')} disabled={props.remoteBusy || api.state !== 'connected'} data-testid="button-run-code"><Play size={12} fill="currentColor" /> Run</button>
                <button className="action-button primary" onClick={() => props.execute('submit')} disabled={props.remoteBusy || submitNeedsProblem || api.state !== 'connected'} data-testid="button-submit-code">{props.remoteBusy ? <RefreshCw size={13} className="spin" /> : <ShieldCheck size={13} />} Submit</button>
                {api.state !== 'connected' && <span style={styles.actionUnavailable}><LockKeyhole size={12} /> {api.state === 'checking' ? 'Connecting…' : 'Backend offline'}</span>}
              </div>
            </div>
            {submitNeedsProblem && <div className="required-problem" data-testid="text-required-problem-id">Select a problem: the submission request requires problem_id.</div>}
          </div>
        </section>

        <aside className="right-column" style={styles.rightColumn}>
          <div style={styles.rightTabs}>
            <button className={`right-tab ${rightTab === 'problem' ? 'selected' : ''}`} onClick={() => setRightTab('problem')} data-testid="tab-problem-explorer"><FileSearch size={13} /> Problem explorer</button>
            <button className={`right-tab ${rightTab === 'signals' ? 'selected' : ''}`} onClick={() => setRightTab('signals')} data-testid="tab-behavior-signals"><Activity size={13} /> Signals</button>
          </div>
          {rightTab === 'problem' ? <ProblemExplorer api={api} program={program} updateProgram={props.updateProgram} /> :
            <div className="fade-in">
              {execution?.report
                ? <AnalysisPanels analysis={backendSignals(execution.report)} prefix="compiler" source="backend" />
                : <AnalysisPanels analysis={activeSimulation?.analysis} prefix="compiler" />}
              {execution?.reportError && <small className="backend-feed-error">{execution.reportError}</small>}
              {!execution?.report && !execution?.reportError && execution?.detail && <div style={styles.inlineEmpty}>Loading the stored analysis for submission {execution.detail.id}…</div>}
              {!execution && !activeSimulation && <div style={styles.inlineEmpty}>Run or submit code to load backend analysis for that submission. Signals appear only when the API returns them.</div>}
            </div>}
          <ApiNotice api={api} compact />
        </aside>
      </div>
    </div>
    <button onClick={() => setExpanded((value) => !value)} className="sectionToggle" style={styles.sectionToggle} aria-expanded={expanded} data-testid="button-toggle-monitoring-overview">
      <span><LayoutDashboard size={14} /> MONITORING OVERVIEW</span><span>{expanded ? 'COLLAPSE' : 'EXPAND'} <ChevronDown size={13} style={{ transform: expanded ? 'rotate(180deg)' : undefined }} /></span>
    </button>
    {expanded && <DashboardOverview api={api} activeSimulation={activeSimulation} report={execution?.report} />}
  </div>;
}

/** Structured rendering of a backend execution inside the console panel. */
function ExecutionResult({ execution, busy }: { execution: ExecutionView; busy: boolean }) {
  const detail = execution.detail;
  const report = execution.report;
  const status = detail?.status ?? execution.accepted?.status ?? 'QUEUED';
  const measurements = (report?.resource_measurements ?? {}) as Record<string, unknown>;
  const enforcement = (measurements.enforcement ?? null) as Record<string, string> | null;
  const spawnMs = measurements.spawn_ms;
  const stdout = detail?.test_results?.[0]?.output_summary ?? null;
  const results = detail?.test_results ?? [];
  const passed = results.filter((result) => result.status === 'ACCEPTED').length;
  const compile = report?.compilation;
  const tone = TERMINAL_TONE[status] ?? 'watch';

  const metrics: Array<{ label: string; value: string }> = [];
  if (detail) {
    if (!isNullish(detail.exit_code)) metrics.push({ label: 'EXIT', value: String(detail.exit_code) });
    if (!isNullish(detail.execution_time_ms)) metrics.push({ label: 'WALL', value: `${detail.execution_time_ms} ms` });
    if (!isNullish(detail.cpu_time_ms)) metrics.push({ label: 'CPU', value: `${detail.cpu_time_ms} ms` });
    if (!isNullish(detail.peak_memory_bytes)) metrics.push({ label: 'PEAK MEM', value: formatBytes(detail.peak_memory_bytes) });
  }
  if (typeof spawnMs === 'number') metrics.push({ label: 'SPAWN', value: `${spawnMs} ms` });

  return <div data-testid="text-backend-result" style={styles.resultWrap}>
    <div style={styles.resultHead}>
      <span className={`tone-label ${tone}`}>{status}</span>
      <span style={styles.resultMeta}>
        {detail ? `submission #${detail.id} · ${detail.language} · ${detail.is_custom_run ? 'custom run' : 'judged'}` : execution.accepted ? `submission #${execution.accepted.id} · accepted for judging` : 'queued'}
      </span>
      {busy && <span style={styles.resultMeta}>{execution.polling ? 'judging…' : 'working…'}</span>}
    </div>

    {metrics.length > 0 && <div style={styles.resultMetrics}>
      {metrics.map((metric) => <div key={metric.label} style={styles.resultMetric}>
        <span style={styles.resultMetricLabel}>{metric.label}</span>
        <span style={styles.resultMetricValue}>{metric.value}</span>
      </div>)}
    </div>}

    {compile?.attempted && <div style={styles.resultLine}>
      <span style={styles.resultMetricLabel}>COMPILE</span>
      <span style={styles.resultMetricValue}>
        {compile.success === false ? 'failed' : 'ok'}
        {compile.compiler ? ` · ${compile.compiler}${compile.compiler_version ? ` ${compile.compiler_version}` : ''}` : ''}
        {isNullish(compile.duration_ms) ? '' : ` · ${compile.duration_ms} ms`}
      </span>
    </div>}
    {(detail?.compile_output || compile?.output) && <pre style={styles.outputPre}>{detail?.compile_output ?? compile?.output}</pre>}

    {execution.kind === 'run' && <div style={styles.resultSection}>
      <span style={styles.resultMetricLabel}>STDOUT</span>
      {stdout ? <pre style={styles.outputPre} data-testid="text-program-output">{stdout}</pre> : <span style={styles.resultMeta}>No program output was captured for this run.</span>}
    </div>}

    {execution.kind === 'submit' && detail && <div style={styles.resultSection}>
      <span style={styles.resultMetricLabel}>TESTS</span>
      <span style={styles.resultMetricValue}>{passed}/{results.length} passed</span>
      <div style={styles.resultTests}>
        {results.map((result) => <div key={result.id} className="mono" style={{ fontSize: 9, color: result.status === 'ACCEPTED' ? '#8cd5a2' : '#cfb875' }}>
          #{result.test_index} {result.test_is_public ? 'public' : 'hidden'} · {result.status}
          {isNullish(result.execution_time_ms) ? '' : ` · ${result.execution_time_ms} ms`}
          {result.error_summary ? ` · ${result.error_summary}` : ''}
        </div>)}
        {!results.length && <div className="mono" style={{ fontSize: 9, color: '#7e8981' }}>No per-test rows were stored for this submission.</div>}
      </div>
    </div>}

    {detail?.error_summary && <div style={styles.resultError}><AlertTriangle size={11} /> {detail.error_summary}</div>}

    {enforcement && <div className="mono" style={styles.resultLimits} title="Per-limit state reported by the execution engine for this run">
      LIMITS {Object.entries(enforcement).map(([key, value]) => `${key.replace(/_limit$/, '').replace(/_/g, ' ')}=${value}`).join(' · ')}
    </div>}
  </div>;
}

function ProblemExplorer({ api, program, updateProgram }: { api: BackendStatus; program?: ProgramWorkspace; updateProgram: (patch: Partial<ProgramWorkspace>) => void }) {
  const [catalog, setCatalog] = useState<FeedState<ProblemSummary[]>>({ status: 'idle' });

  useEffect(() => {
    if (api.state !== 'connected') {
      setCatalog({ status: 'idle' });
      return;
    }
    const controller = new AbortController();
    setCatalog({ status: 'loading' });
    listProblems({ page: 1, page_size: 20 }, controller.signal)
      .then((page) => setCatalog({ status: 'ready', data: page.items }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) setCatalog({ status: 'error', error: describeApiError(error) });
      });
    return () => controller.abort();
  }, [api.state]);

  const items = catalog.data ?? [];
  const selected = items.find((problem) => String(problem.id) === program?.problemId);

  return <div style={styles.problemPanel} className="fade-in">
    <div style={styles.explorerHeading}><div><div style={styles.eyebrow}>CATALOG</div><h2 style={styles.sectionTitle}>Problem explorer</h2></div><span style={styles.smallIcon}><Search size={14} /></span></div>
    {catalog.status === 'loading' && <div className="contractHint" style={styles.contractHint}><div style={styles.emptyIcon}><FileSearch size={17} /></div><strong>Loading problems</strong><p>GET /api/v1/problems</p></div>}
    {catalog.status === 'error' && <div className="backend-feed-error" data-testid="text-problem-error">{catalog.error}</div>}
    {catalog.status === 'ready' && !items.length && <div className="emptyState" style={styles.emptyState}>
      <div style={styles.emptyIcon}><FileSearch size={17} /></div>
      <strong>No problems stored</strong>
      <p>The backend returned an empty catalog. This is a real zero — no problems were invented.</p>
      <p className="mono" style={{ fontSize: 9, color: '#707b73' }}>Seed a demonstration problem (idempotent): .\.venv\Scripts\python.exe scripts\seed_demo.py</p>
    </div>}
    {items.length > 0 ? <div style={styles.problemList}>
      {items.map((problem) => <button
        key={problem.id}
        className={`scenario-choice ${String(problem.id) === program?.problemId ? 'selected' : ''}`}
        onClick={() => updateProgram({ problemId: String(problem.id) })}
        data-testid={`button-problem-${problem.id}`}
      >
        <span className="scenario-index">{String(problem.id).padStart(2, '0')}</span>
        <span className="scenario-copy">
          <strong>{problem.title}</strong>
          <small>{problem.difficulty} · {problem.test_case_counts.public} public / {problem.test_case_counts.hidden} hidden tests{problem.time_limit_ms ? ` · ${problem.time_limit_ms} ms` : ''}</small>
        </span>
        <ArrowRight size={13} className="scenario-arrow" />
      </button>)}
      <div className="mono" style={styles.selectedProblem} data-testid="text-selected-problem">
        {selected ? `SELECTED · #${selected.id} ${selected.title}` : 'NO PROBLEM SELECTED — PICK ONE ABOVE TO SUBMIT'}
      </div>
    </div> : catalog.status !== 'loading' && <div className="emptyState" style={styles.emptyState}>
      <div style={styles.emptyIcon}><FileSearch size={17} /></div>
      <strong>Backend problem ID</strong>
      <p>Required by POST /api/v1/submissions when the catalog is unavailable.</p>
      <label style={styles.fieldLabel}>PROBLEM ID <span>OPTIONAL</span></label>
      <input value={program?.problemId ?? ''} onChange={(event) => updateProgram({ problemId: event.target.value })} placeholder="No problem selected" style={styles.textInput} data-testid="input-problem-id" />
      <div className="mono" style={{ fontSize: 9, color: '#707b73', marginTop: 8 }}>{api.state === 'connected' ? 'Catalog unavailable — enter an ID manually.' : 'Waiting for a backend connection.'}</div>
    </div>}
    <div className="problemFooter" style={styles.problemFooter}><div style={styles.footerIcon}><CircleHelp size={14} /></div><div><strong>How submissions work</strong><p>Run executes your code once with your stdin. Submit judges it against this problem's stored tests.</p></div></div>
  </div>;
}

function DashboardOverview({ api, activeSimulation, report }: { api: BackendStatus; activeSimulation?: SimulationRun; report?: ReportResponse }) {
  const signals = report ? backendSignals(report) : activeSimulation?.analysis;
  return <div style={styles.overview} className="fade-in">
    <div style={styles.overviewHead}>
      <div><div style={styles.eyebrow}>SIGNAL SURFACE</div><h2 style={styles.sectionTitle}>Execution behavior</h2></div>
      <div style={styles.overviewMeta}><span className="live-dot" /> {report ? `LATEST · SUBMISSION #${report.submission.id}` : activeSimulation ? 'LATEST · LOCAL SIMULATION' : 'AWAITING BACKEND DATA'}</div>
    </div>
    <AnalysisPanels analysis={signals} prefix="overview" source={report ? 'backend' : 'simulation'} />
    <div style={styles.backendDataFoot}><span><Shield size={13} /> Backend reports appear here only when returned by the connected service.</span><span>{report ? 'REAL API DATA' : api.state === 'connected' ? 'CONNECTED · NO REPORT YET' : 'NO BACKEND DATA'}</span></div>
  </div>;
}

function AnalysisPanels({ analysis, prefix, source = 'simulation' }: { analysis?: DemoAnalysis; prefix: string; source?: 'backend' | 'simulation' }) {
  const panels = [
    { icon: <Activity size={14} />, name: 'Behavior fingerprint', value: analysis?.fingerprint, note: 'Observable execution signature', key: 'fingerprint' },
    { icon: <GitBranch size={14} />, name: 'Sequence analysis', value: analysis?.sequence?.join('  →  '), note: 'Ordered event trace', key: 'sequence' },
    { icon: <FileSearch size={14} />, name: 'Possible-hardcoding review', value: analysis?.hardcoding, note: 'Review cue · not a verdict', key: 'hardcoding' },
    { icon: <Gauge size={14} />, name: 'Adaptive trust', value: analysis?.trust, note: 'Backend-owned when returned', key: 'trust' },
  ];
  return <div className="signal-grid" style={styles.signalGrid}>
    {panels.map((panel) => <div key={panel.key} style={styles.signalCard} data-testid={`${prefix}-panel-${panel.key}`}>
      <div style={styles.signalCardHead}><span style={styles.signalIcon}>{panel.icon}</span><span>{panel.name}</span><ArrowDownRight size={12} color="#66716a" /></div>
      <div className={`signal-value ${analysis ? 'has-value' : ''}`} style={panel.key === 'sequence' ? { fontSize: 10, lineHeight: '17px' } : undefined}>{panel.value ?? 'No analysis returned'}</div>
      <div style={styles.signalNote}>{analysis ? (source === 'backend' ? 'BACKEND · REAL EXECUTION' : 'SIMULATION · DEMO DATA') : panel.note}</div>
    </div>)}
  </div>;
}

function SimulationPage({ store, setStore }: { store: WorkspaceStore; setStore: Dispatch<SetStateAction<WorkspaceStore>> }) {
  const [scenario, setScenario] = useState<SimulationKey>('normal');
  const [phase, setPhase] = useState<'idle' | 'running' | 'paused' | 'complete'>('idle');
  const [tick, setTick] = useState(0);
  const [speed, setSpeed] = useState(1);
  const savedRunRef = useRef('');
  const scenarioData = scenarioDefinitions[scenario];
  const analysis = useMemo(() => makeAnalysis(scenario), [scenario]);
  const progress = Math.min(tick / 10, 1);

  useEffect(() => {
    if (phase !== 'running') return;
    const timer = window.setInterval(() => setTick((current) => Math.min(current + 1, 10)), Math.max(100, 650 / speed));
    return () => window.clearInterval(timer);
  }, [phase, speed]);

  useEffect(() => {
    if (tick >= 10 && phase === 'running') setPhase('complete');
  }, [tick, phase]);

  useEffect(() => {
    if (phase !== 'complete' || savedRunRef.current === `${scenario}-${tick}`) return;
    const id = `demo-${Date.now()}`;
    savedRunRef.current = `${scenario}-${tick}`;
    const run: SimulationRun = { id, scenario, name: scenarioDefinitions[scenario].name, createdAt: new Date().toISOString(), analysis };
    setStore((current) => ({ ...current, simulations: [run, ...current.simulations].slice(0, 60) }));
  }, [phase, scenario, tick, analysis, setStore]);

  const start = () => {
    if (phase === 'paused') { setPhase('running'); return; }
    setTick(0);
    savedRunRef.current = '';
    setPhase('running');
  };
  const reset = () => { setPhase('idle'); setTick(0); savedRunRef.current = ''; };
  const metric = scenario === 'normal' ? `${Math.round(progress * 20)} ms` :
    scenario === 'loop' ? `${Math.round(18 + progress * 82)}%` :
      scenario === 'memory' ? `${Math.round(24 + progress * 71)} MB` :
        scenario === 'process' ? `${Math.round(progress * 12)} events` :
          scenario === 'file' ? `${Math.round(progress * 3)} event${progress > .3 ? 's' : ''}` :
            `${Math.round(progress * 8)} events`;
  return <div style={styles.content}>
    <PageHeading eyebrow="BEHAVIOR LAB / DETERMINISTIC" title="OS behavior simulator" detail="Safe, repeatable event streams for faculty demonstrations. Nothing is executed." trailing={<span className="simulationBadge"><Activity size={12} /> SIMULATION / DEMO DATA</span>} />
    <div className="sim-grid" style={styles.simGrid}>
      <section style={styles.simMain}>
        <div style={styles.simPanelHead}>
          <div><div style={styles.eyebrow}>SCENARIO CONFIGURATION</div><h2 style={styles.sectionTitle}>Choose an event profile</h2></div>
          <span className="mono" style={styles.simInstance}>DETERMINISTIC SET · LOCAL ONLY</span>
        </div>
        <div className="scenario-grid" style={styles.scenarioGrid}>
          {(Object.keys(scenarioDefinitions) as SimulationKey[]).map((key, index) => {
            const definition = scenarioDefinitions[key];
            return <button key={key} className={`scenario-choice ${scenario === key ? 'selected' : ''}`} onClick={() => { if (phase !== 'running') { setScenario(key); reset(); } }} disabled={phase === 'running'} data-testid={`button-scenario-${key}`}>
              <span className="scenario-index">{String(index + 1).padStart(2, '0')}</span><span className="scenario-copy"><strong>{definition.name}</strong><small>{definition.description}</small></span><ArrowRight size={13} className="scenario-arrow" />
            </button>;
          })}
        </div>
        <div style={styles.simRun}>
          <div style={styles.runInfo}>
            <div style={styles.runState}><span className={`phase-dot ${phase}`} />{phase === 'running' ? 'SIMULATING' : phase === 'paused' ? 'PAUSED' : phase === 'complete' ? 'COMPLETE' : 'READY'}</div>
            <div style={styles.runName}>{scenarioData.name}</div>
            <div style={styles.runDescription}>{scenarioData.classification} <span>·</span> {scenarioData.metric}</div>
          </div>
          <div className="run-controls" style={styles.runControls}>
            <div style={styles.speedControl}><span>SPEED</span>{[1, 2, 4].map((value) => <button className={`speed-option ${speed === value ? 'selected' : ''}`} key={value} onClick={() => setSpeed(value)} data-testid={`button-speed-${value}`}>{value}×</button>)}</div>
            <div style={{ display: 'flex', gap: 7 }}>
              {phase === 'running' ? <button className="action-button secondary" onClick={() => setPhase('paused')} data-testid="button-pause-simulation"><Pause size={13} /> Pause</button> :
                <button className="action-button primary" onClick={start} disabled={phase === 'complete'} data-testid={phase === 'paused' ? 'button-resume-simulation' : 'button-start-simulation'}><Play size={12} fill="currentColor" /> {phase === 'paused' ? 'Resume' : phase === 'complete' ? 'Complete' : 'Start'}</button>}
              <button className="action-button secondary" onClick={reset} data-testid="button-reset-simulation"><RotateCcw size={13} /> Reset</button>
            </div>
          </div>
          <div style={styles.progressTrack}><div style={{ ...styles.progressBar, transform: `scaleX(${progress})` }} /></div>
          <div style={styles.progressCaption}><span>EVENT TIMELINE</span><span className="mono">{String(tick).padStart(2, '0')} / 10</span></div>
        </div>
        <div className="telemetry-grid" style={styles.telemetryGrid}>
          <Telemetry icon={<Cpu size={14} />} label={scenario === 'memory' ? 'WORKING SET' : scenario === 'loop' ? 'CPU ACTIVITY' : 'EVENT PRESSURE'} value={phase === 'idle' ? '—' : metric} tone={scenario === 'normal' ? 'safe' : 'watch'} />
          <Telemetry icon={<HardDrive size={14} />} label="POLICY SIGNAL" value={phase === 'idle' ? 'Standby' : phase === 'complete' ? scenarioData.metric : 'Observing'} tone={scenario === 'normal' ? 'safe' : 'watch'} />
          <Telemetry icon={<Clock3 size={14} />} label="SIMULATED TIME" value={phase === 'idle' ? '00:00' : `00:${String(Math.min(tick * 2, 20)).padStart(2, '0')}`} tone="neutral" />
        </div>
      </section>
      <section style={styles.eventSection}>
        <div style={styles.simPanelHead}><div><div style={styles.eyebrow}>OBSERVED EVENTS</div><h2 style={styles.sectionTitle}>Event stream</h2></div><span className="demoMicroBadge">SIMULATED</span></div>
        <div style={styles.eventList}>
          {analysis.sequence.slice(0, Math.max(phase === 'idle' ? 1 : 2, Math.ceil(tick / 2))).map((event, index) => <div key={`${event}-${index}`} className="event-row fade-in">
            <span className={`event-marker ${scenario === 'normal' ? 'safe' : index >= 2 ? 'alert' : 'watch'}`} />
            <span className="mono event-time">{`00:${String(index * 2).padStart(2, '0')}`}</span><span className="mono event-name">{event}</span><span className="event-class">{index >= 2 && scenario !== 'normal' ? 'REVIEW' : 'OBSERVED'}</span>
          </div>)}
          {phase === 'idle' && <div style={styles.eventPlaceholder}>Start a scenario to advance the local event stream.</div>}
        </div>
        <div style={styles.eventFooter}><ShieldCheck size={13} /><span>Isolated from host OS · No syscalls or child processes run</span></div>
      </section>
    </div>
    <div style={styles.analysisHeading}><div><div style={styles.eyebrow}>NOVELTY SIGNALS</div><h2 style={styles.sectionTitle}>Behavioral interpretation</h2></div><span className="simulationBadge">SIMULATION / DEMO DATA</span></div>
    <div style={styles.analysisWrap}><AnalysisPanels analysis={phase !== 'idle' ? analysis : undefined} prefix="simulation" /></div>
    <div className="signal-details" style={styles.signalDetails}>
      {analysis.signals.map((signal) => <div key={signal.id} className={`signal-detail ${signal.tone}`} data-testid={`signal-detail-${signal.id}`}>
        <span className={`signal-detail-dot ${signal.tone}`} /><div><div className="signal-detail-title">{signal.label}<span className={`tone-label ${signal.tone}`}>{signal.value}</span></div><p>{signal.detail}</p></div>
      </div>)}
    </div>
    <div style={styles.localHistoryHead}><div><div style={styles.eyebrow}>PERSISTED LOCALLY</div><h2 style={styles.sectionTitle}>Recent simulation runs</h2></div><Link href="/reports" className="inlineLink">View reports <ArrowRight size={13} /></Link></div>
    {store.simulations.length ? <div style={styles.recentRuns}>{store.simulations.slice(0, 3).map((run) => <Link key={run.id} href={`/submissions/${run.id}`} className="recent-run" data-testid={`link-simulation-${run.id}`}><span className="run-id">{run.id}</span><strong>{run.name}</strong><span>{formatTime(run.createdAt)}</span><ArrowRight size={13} /></Link>)}</div> : <div style={styles.inlineEmpty}>Completed local simulations will appear here. No backend or live measurements are implied.</div>}
  </div>;
}

function Telemetry({ icon, label, value, tone, caption = 'LOCAL SIMULATION' }: { icon: ReactNode; label: string; value: string; tone: string; caption?: string }) {
  return <div style={styles.telemetry}><div style={styles.telemetryHead}>{icon}<span>{label}</span></div><div className={`telemetry-value ${tone}`}>{value}</div><div className="mono telemetry-caption">{caption}</div></div>;
}

/** Real faculty-dashboard numbers; each value comes straight from the API. */
function DashboardMetrics({ dashboard, trust }: { dashboard?: DashboardResponse; trust?: TrustHistoryResponse }) {
  const submissions = dashboard?.submissions ?? { total: 0, accepted_rate_percent: 0 };
  const alerts = dashboard?.alerts ?? {};
  const coverage = dashboard?.resource_metric_coverage ?? {};
  const pending = Number(alerts.pending_review ?? 0);
  const acceptedRate = Number(submissions.accepted_rate_percent ?? 0);
  const tiles = [
    { icon: <Activity size={14} />, label: 'SUBMISSIONS', value: String(submissions.total ?? 0), tone: 'neutral', href: '/submissions' },
    { icon: <Check size={14} />, label: 'ACCEPTED RATE', value: `${acceptedRate}%`, tone: acceptedRate >= 50 ? 'safe' : 'watch', href: '/submissions?status=ACCEPTED' },
    { icon: <ShieldAlert size={14} />, label: 'ALERTS PENDING', value: String(pending), tone: pending ? 'watch' : 'safe', href: '/alerts?review_status=PENDING' },
    { icon: <Gauge size={14} />, label: 'TRUST SCORE', value: trust ? String(trust.current_score) : '—', tone: trust?.monitoring_level === 'HIGH' ? 'watch' : 'neutral', href: '/trust' },
    { icon: <Cpu size={14} />, label: 'CPU METRIC COVERAGE', value: `${Number(coverage.coverage_cpu_percent ?? 0)}%`, tone: 'neutral', href: '/analytics' },
    { icon: <FileSearch size={14} />, label: 'FINGERPRINT COVERAGE', value: `${Number(coverage.coverage_fingerprint_percent ?? 0)}%`, tone: 'neutral', href: '/analytics' },
  ];
  return <div className="telemetry-grid" style={styles.telemetryGrid}>
    {tiles.map((tile) => {
      const inner = <Telemetry icon={tile.icon} label={tile.label} value={tile.value} tone={tile.tone} caption="REAL API DATA" />;
      return <Link key={tile.label} href={tile.href} style={{ textDecoration: 'none', display: 'block', cursor: 'pointer' }} data-testid={`kpi-${tile.label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}>{inner}</Link>;
    })}
  </div>;
}

function statusCount(dashboard: DashboardResponse | undefined, status: string): number | null {
  const counts = dashboard?.submissions.by_status;
  if (!counts || !(status in counts)) return null;
  const value = counts[status];
  return typeof value === 'number' ? value : null;
}

function FacultyDashboardTables({ dashboard }: { dashboard: DashboardResponse }) {
  const timeoutCount = ['TIME_LIMIT_EXCEEDED', 'MEMORY_LIMIT_EXCEEDED', 'PROCESS_LIMIT_EXCEEDED']
    .reduce((sum, status) => sum + (statusCount(dashboard, status) ?? 0), 0);
  const kpis = [
    { label: 'Compile errors', value: statusCount(dashboard, 'COMPILATION_ERROR') },
    { label: 'Wrong answers', value: statusCount(dashboard, 'WRONG_ANSWER') },
    { label: 'Execution timeouts / limits', value: timeoutCount },
    { label: 'Pending alerts', value: Number(dashboard.alerts.pending_review ?? 0) },
  ];
  const timeline = dashboard.recent_submissions.reduce<Record<string, number>>((acc, submission) => {
    const key = new Date(submission.created_at).toISOString().slice(0, 10);
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const timelineRows = Object.entries(timeline).sort(([a], [b]) => a.localeCompare(b));
  return <section style={styles.dashboardPanel} className="fade-in">
    <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><LayoutDashboard size={14} /></span><h3>Faculty review summary</h3></div><span className="backendBadge">LIVE BACKEND DATA</span></div>
    <div style={styles.kpiStrip}>{kpis.map((kpi) => <div key={kpi.label} style={styles.kpiMini}><span>{kpi.label}</span><strong>{kpi.value === null ? 'Unavailable' : kpi.value}</strong></div>)}</div>
    <div className="detail-columns" style={styles.detailColumns}>
      <div>
        <div className="backend-feed-caption">RECENT SUBMISSION ACTIVITY</div>
        {timelineRows.length >= 2 ? timelineRows.map(([day, count]) => <div className="sequence-row" key={day}><span className="sequence-number">{day.slice(5)}</span><code>{count} submission{count === 1 ? '' : 's'}</code><span>API</span></div>) :
          <div style={styles.inlineEmpty}>Not enough timestamped backend submissions for an activity chart. No decorative chart is fabricated.</div>}
      </div>
      <div>
        <div className="backend-feed-caption">SUBMISSIONS REQUIRING REVIEW</div>
        {dashboard.recent_submissions.filter((s) => s.status !== 'ACCEPTED').slice(0, 5).map((submission) => <Link key={submission.id} href={`/submissions/${submission.id}`} className="monitor-run-link">
          <span className="run-id">#{submission.id}</span><strong>{submission.status}</strong><span>{submission.workspace_label ?? 'workspace unavailable'}</span><ArrowRight size={13} />
        </Link>)}
        {!dashboard.recent_submissions.some((s) => s.status !== 'ACCEPTED') && <div style={styles.inlineEmpty}>No recent non-accepted submissions returned by the dashboard endpoint.</div>}
      </div>
    </div>
  </section>;
}

function AdminPage({ api, workspaceLabel }: { api: BackendStatus; workspaceLabel: string }) {
  const [dashboard, setDashboard] = useState<FeedState<DashboardResponse>>({ status: 'idle' });
  const [alerts, setAlerts] = useState<FeedState<AlertSummary[]>>({ status: 'idle' });
  const [trust, setTrust] = useState<FeedState<TrustHistoryResponse>>({ status: 'idle' });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (api.state !== 'connected') {
      setDashboard({ status: 'idle' });
      setAlerts({ status: 'idle' });
      setTrust({ status: 'idle' });
      return;
    }
    const controller = new AbortController();
    setDashboard({ status: 'loading' });
    setAlerts({ status: 'loading' });
    getDashboard(controller.signal)
      .then((data) => setDashboard({ status: 'ready', data }))
      .catch((error: unknown) => { if (!isAbortError(error)) setDashboard({ status: 'error', error: describeApiError(error) }); });
    listAlerts({ page: 1, page_size: 5 }, controller.signal)
      .then((page) => setAlerts({ status: 'ready', data: page.items }))
      .catch((error: unknown) => { if (!isAbortError(error)) setAlerts({ status: 'error', error: describeApiError(error) }); });
    if (workspaceLabel) {
      setTrust({ status: 'loading' });
      getTrustHistory(workspaceLabel, 10, controller.signal)
        .then((data) => setTrust({ status: 'ready', data }))
        .catch((error: unknown) => { if (!isAbortError(error)) setTrust({ status: 'error', error: describeApiError(error) }); });
    }
    return () => controller.abort();
  }, [api.state, workspaceLabel, reload]);

  const alertItems = alerts.data ?? [];
  const feedLabel = dashboard.status === 'ready'
    ? 'REAL API DATA'
    : dashboard.status === 'error'
      ? 'DATABASE UNAVAILABLE'
      : 'NO SYNTHETIC COUNTS';

  return <div style={styles.content}>
    <PageHeading eyebrow="FACULTY CONSOLE / LIVE OVERVIEW" title="Admin Dashboard" detail="A consolidated view of backend submissions, alerts, resource coverage, and review queues. Local-dev mode is not authentication." trailing={<span className="backendBadge"><Activity size={12} /> LIVE BACKEND DATA</span>} />
    <ApiNotice api={api} />
    <div style={styles.adminSectionHead}><div><div style={styles.eyebrow}>OBSERVABILITY</div><h2 style={styles.sectionTitle}>Signals at a glance</h2></div><span className="mono" style={styles.simInstance}>{feedLabel}</span></div>
    {dashboard.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading GET /api/v1/faculty/dashboard …</span></div>}
    {dashboard.status === 'error' && <div className="monitor-empty" style={styles.monitorEmpty} data-testid="text-dashboard-unavailable"><div className="empty-icon"><ShieldAlert size={16} /></div><strong>Dashboard unavailable</strong><p>{dashboard.error}</p><button className="action-button secondary" onClick={() => setReload((value) => value + 1)}>Retry</button></div>}
    {dashboard.status === 'ready' && <DashboardMetrics dashboard={dashboard.data} trust={trust.status === 'ready' ? trust.data : undefined} />}
    {dashboard.status === 'ready' && dashboard.data && <FacultyDashboardTables dashboard={dashboard.data} />}
    <div style={styles.quickActions}>
      <Link href="/submissions" className="action-button secondary">Open submissions <ArrowRight size={12} /></Link>
      <Link href="/compare" className="action-button secondary">Compare submissions <GitBranch size={12} /></Link>
      <Link href="/alerts" className="action-button secondary">Review alerts <ShieldAlert size={12} /></Link>
    </div>
      <div className="admin-lower" style={styles.adminLower}>
      <section style={styles.monitorCard}>
        <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><ShieldAlert size={14} /></span><h3>Security alerts</h3></div><Link href="/reports" className="inlineLink">Open reports <ArrowRight size={13} /></Link></div>
        {alertItems.length ? alertItems.map((alert) => <Link key={alert.id} href={`/submissions/${alert.submission_id}`} className="monitor-run-link" data-testid={`row-alert-${alert.id}`}>
          <span className="run-id">#{alert.id} · {alert.severity}</span><strong>{alert.alert_type}</strong><span>{formatTime(alert.created_at)}</span><ArrowRight size={13} />
        </Link>) : <div className="monitor-empty" style={styles.monitorEmpty}>
          <div className="empty-icon"><ShieldCheck size={16} /></div>
          <strong>{alerts.status === 'ready' ? 'No alerts recorded' : alerts.status === 'error' ? 'Alerts unavailable' : alerts.status === 'loading' ? 'Loading alerts…' : 'Backend offline'}</strong>
          <p>{alerts.status === 'error' ? alerts.error : alerts.status === 'ready' ? 'GET /api/v1/faculty/alerts returned an empty page — a real zero, not a placeholder.' : 'GET /api/v1/faculty/alerts runs when the database is configured.'}</p>
        </div>}
      </section>
      <section style={styles.monitorCard}>
        <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Clock3 size={14} /></span><h3>Recent backend submissions</h3></div><Link href="/submissions" className="inlineLink">All submissions <ArrowRight size={13} /></Link></div>
        {(dashboard.data?.recent_submissions ?? []).length ? dashboard.data!.recent_submissions.slice(0, 5).map((submission) => <Link key={submission.id} href={`/submissions/${submission.id}`} className="monitor-run-link" data-testid={`link-dashboard-submission-${submission.id}`}><span className="run-id">#{submission.id}</span><strong>{submission.status}</strong><span>{formatTime(submission.created_at)}</span><ArrowRight size={13} /></Link>) :
          <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><FileSearch size={16} /></div><strong>{dashboard.status === 'ready' ? 'No submissions stored' : 'Dashboard unavailable'}</strong><p>Recent rows come only from GET /api/v1/faculty/dashboard.</p></div>}
      </section>
    </div>
  </div>;
}

function SubmissionsPage({ api }: { api: BackendStatus }) {
  const [, setLocation] = useLocation();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<number[]>([]);
  const [feed, setFeed] = useState<FeedState<ApiPage<SubmissionSummary>>>({ status: 'idle' });
  const load = useCallback((signal?: AbortSignal) => {
    setFeed({ status: 'loading' });
    return listAllSubmissions({ page, page_size: 20, status: status || undefined }, signal)
      .then((data) => setFeed({ status: 'ready', data }))
      .catch((error: unknown) => { if (!isAbortError(error)) setFeed({ status: 'error', error: describeApiError(error) }); });
  }, [page, status]);
  useEffect(() => {
    if (api.state !== 'connected') { setFeed({ status: 'idle' }); return; }
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [api.state, load]);
  const rows = (feed.data?.items ?? []).filter((submission) => `${submission.id} ${submission.problem_id ?? ''} ${submission.workspace_label ?? ''} ${submission.status} ${submission.language}`.toLowerCase().includes(query.toLowerCase()));
  const compare = () => selected.length >= 2 && setLocation(`/compare?ids=${selected.join(',')}`);
  return <div style={styles.content}>
    <PageHeading eyebrow="FACULTY CONSOLE / SUBMISSIONS" title="Submissions" detail="Search, filter, inspect, and select backend submissions for comparison. Values are loaded from the faculty submissions endpoint." trailing={<span className="backendBadge">LIVE BACKEND DATA</span>} />
    <ApiNotice api={api} compact />
    <div className="report-toolbar" style={styles.reportToolbar}>
      <div className="searchBox search-box" style={styles.searchBox}><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search id, status, workspace, problem" /></div>
      <select className="filter-select" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }} style={styles.filterSelect} aria-label="Filter submission status">
        <option value="">All statuses</option>{Object.keys(TERMINAL_TONE).map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
      <button className="action-button secondary" onClick={() => load()} disabled={feed.status === 'loading'}><RefreshCw size={12} /> Retry</button>
      <button className="action-button primary" onClick={compare} disabled={selected.length < 2}><GitBranch size={12} /> Compare selected</button>
    </div>
    {feed.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading GET /api/v1/faculty/submissions …</span></div>}
    {feed.status === 'error' && <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><ShieldAlert size={16} /></div><strong>Submissions unavailable</strong><p>{feed.error}</p></div>}
    {feed.status === 'ready' && <div style={styles.reportTable}>
      <div className="submission-table-head"><span /><span>ID / PROBLEM</span><span>WORKSPACE</span><span>STATUS</span><span>RUNTIME</span><span>TIME</span><span /></div>
      {rows.map((submission) => <div className="submission-row" key={submission.id}>
        <input type="checkbox" aria-label={`Select submission ${submission.id}`} checked={selected.includes(submission.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, submission.id] : current.filter((id) => id !== submission.id))} />
        <Link href={`/submissions/${submission.id}`}><strong>#{submission.id} · {submission.language}</strong><small>{submission.problem_id ? `problem ${submission.problem_id}` : 'custom run / no problem'}</small></Link>
        <span>{submission.workspace_label ?? 'Unavailable'}</span>
        <span className={`tone-label ${TERMINAL_TONE[submission.status] ?? 'watch'}`}>{submission.status}</span>
        <span className="mono">{isNullish(submission.execution_time_ms) ? 'time unavailable' : `${submission.execution_time_ms} ms`} · {formatBytes(submission.peak_memory_bytes)}</span>
        <span className="report-time">{formatTime(submission.created_at)}</span>
        <Link href={`/submissions/${submission.id}`} className="small-link"><ArrowRight size={13} /></Link>
      </div>)}
      {!rows.length && <EmptyFeedMessage title="No submissions found" body={(feed.data?.pagination.total ?? 0) ? 'No rows match the current client-side search.' : 'The backend returned an empty page.'} />}
    </div>}
    {feed.status === 'ready' && <div style={styles.paginationBar}><button className="action-button secondary" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><span className="mono">PAGE {feed.data?.pagination.page ?? page} / {feed.data?.pagination.total_pages ?? 1}</span><button className="action-button secondary" disabled={(feed.data?.pagination.page ?? page) >= (feed.data?.pagination.total_pages ?? 1)} onClick={() => setPage((value) => value + 1)}>Next</button></div>}
  </div>;
}

type ComparisonRecord = {
  detail: SubmissionDetail;
  report?: ReportResponse;
  fingerprint?: FingerprintResponse;
  events: EventResponse[];
  error?: string;
};

type PairwiseResult = {
  comparison?: FingerprintComparison;
};

function SubmissionComparisonPage({ api }: { api: BackendStatus }) {
  const [, setLocation] = useLocation();
  // wouter's useLocation returns the pathname only; useSearch() carries the
  // query string, so comparison selections survive a full page reload.
  const search = useSearch();
  const initialIds = useMemo(() => new URLSearchParams(search).get('ids')?.split(',').map(Number).filter(Boolean) ?? [], [search]);
  const [available, setAvailable] = useState<FeedState<SubmissionSummary[]>>({ status: 'idle' });
  const [selected, setSelected] = useState<number[]>(initialIds);
  const [records, setRecords] = useState<FeedState<ComparisonRecord[]>>({ status: 'idle' });
  const [pairwise, setPairwise] = useState<FeedState<PairwiseResult>>({ status: 'idle' });
  useEffect(() => { setSelected(initialIds); }, [initialIds.join(',')]);
  useEffect(() => {
    if (api.state !== 'connected') { setAvailable({ status: 'idle' }); return; }
    const controller = new AbortController();
    setAvailable({ status: 'loading' });
    listAllSubmissions({ page: 1, page_size: 50 }, controller.signal)
      .then((page) => setAvailable({ status: 'ready', data: page.items }))
      .catch((error: unknown) => { if (!isAbortError(error)) setAvailable({ status: 'error', error: describeApiError(error) }); });
    return () => controller.abort();
  }, [api.state]);
  const compare = useCallback((ids = selected) => {
    if (ids.length < 2) return;
    setLocation(`/compare?ids=${ids.join(',')}`);
    const controller = new AbortController();
    setRecords({ status: 'loading' });
    setPairwise({ status: 'idle' });
    Promise.all(ids.map(async (id): Promise<ComparisonRecord> => {
      const detail = await getSubmission(id, controller.signal);
      const [reportResult, eventsResult, fingerprintResult] = await Promise.allSettled([
        getSubmissionReport(id, controller.signal),
        listSubmissionEvents(id, { page: 1, page_size: 100 }, controller.signal),
        getFingerprint(id, controller.signal),
      ]);
      return {
        detail,
        report: reportResult.status === 'fulfilled' ? reportResult.value : undefined,
        events: eventsResult.status === 'fulfilled' ? eventsResult.value.items : [],
        fingerprint: fingerprintResult.status === 'fulfilled' ? fingerprintResult.value : undefined,
        error: reportResult.status === 'rejected' ? describeApiError(reportResult.reason) : undefined,
      };
    }))
      .then((data) => {
        setRecords({ status: 'ready', data });
        // The backend exposes a real pairwise fingerprint comparison for exactly
        // two submissions. Use it when two records are selected.
        if (data.length === 2) {
          setPairwise({ status: 'loading' });
          compareFingerprints(data[0].detail.id, data[1].detail.id, controller.signal)
            .then((comparison) => setPairwise({ status: 'ready', data: { comparison } }))
            .catch((error: unknown) => {
              if (!isAbortError(error)) setPairwise({ status: 'error', error: describeApiError(error) });
            });
        }
      })
      .catch((error: unknown) => setRecords({ status: 'error', error: error instanceof Error ? error.message : describeApiError(error) }));
  }, [selected, setLocation]);
  useEffect(() => { if (initialIds.length >= 2 && api.state === 'connected') compare(initialIds); }, [api.state, initialIds.join(',')]);
  return <div style={styles.content}>
    <PageHeading eyebrow="FACULTY CONSOLE / INVESTIGATION" title="Submission Comparison" detail="Compare real submissions side by side. Differences are investigative signals, never proof of misconduct." trailing={<span className="backendBadge"><GitBranch size={12} /> API COMPOSED VIEW</span>} />
    <ApiNotice api={api} compact />
    <section style={styles.dashboardPanel}>
      <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><FileCode2 size={14} /></span><h3>Select submissions</h3></div><button className="action-button primary" disabled={selected.length < 2} onClick={() => compare()}>Compare selected</button></div>
      {available.status === 'error' && <div className="backend-feed-error">{available.error}</div>}
      <div style={styles.comparePicker}>{(available.data ?? []).map((submission) => <label key={submission.id} className="compare-choice"><input type="checkbox" checked={selected.includes(submission.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, submission.id] : current.filter((id) => id !== submission.id))} /><span>#{submission.id}</span><small>{submission.status} · {submission.workspace_label ?? 'workspace unavailable'}</small></label>)}</div>
      {available.status === 'ready' && !(available.data ?? []).length && <div style={styles.inlineEmpty}>No backend submissions are available to compare.</div>}
    </section>
    {records.status === 'idle' && initialIds.length === 0 && <div style={styles.inlineEmpty} data-testid="text-compare-hint">Select two or more submissions above, or open this page with ?ids=1,2 to auto-load a comparison.</div>}
    {records.status === 'idle' && initialIds.length === 1 && (() => {
      const availableIds = new Set((available.data ?? []).map((s) => s.id));
      const missing = initialIds.filter((id) => !availableIds.has(id));
      if (missing.length > 0) return <div style={styles.inlineEmpty} data-testid="text-compare-missing">URL submission ID {missing.join(', ')} was not found in backend records. Select from the list below.</div>;
      return <div style={styles.inlineEmpty}>Select at least two submissions to compare.</div>;
    })()}
    {records.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading submission details, reports, fingerprints, and event timelines …</span></div>}
    {records.status === 'error' && <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><AlertTriangle size={16} /></div><strong>Comparison unavailable</strong><p>{records.error}</p></div>}
    {records.status === 'ready' && records.data && <ComparisonResults records={records.data} pairwise={pairwise} />}
  </div>;
}

function metricFromFingerprint(fingerprint: FingerprintResponse | undefined, key: string): unknown {
  return fingerprint?.fingerprint?.[key];
}

/** Format a fingerprint metric honestly: a real zero stays `0`; null is Unavailable. */
function formatMetric(value: unknown, suffix = ''): string {
  if (value === null || value === undefined) return 'Unavailable';
  if (typeof value === 'number') return `${value}${suffix}`;
  return String(value);
}

function ComparisonResults({ records, pairwise }: { records: ComparisonRecord[]; pairwise: FeedState<PairwiseResult> }) {
  const columns = `minmax(130px,1fr) repeat(${records.length}, minmax(0,1fr))`;
  const comparisonRowStyle = { ...styles.comparisonRowFallback, gridTemplateColumns: columns } as CSSProperties;
  const metricRows = [
    { label: 'Status', value: (r: ComparisonRecord) => r.detail.status },
    { label: 'Language', value: (r: ComparisonRecord) => r.detail.language.toUpperCase() },
    { label: 'Execution duration', value: (r: ComparisonRecord) => formatMetric(r.detail.execution_time_ms, ' ms') },
    { label: 'CPU measurement', value: (r: ComparisonRecord) => formatMetric(r.detail.cpu_time_ms, ' ms') },
    { label: 'Peak memory', value: (r: ComparisonRecord) => formatBytes(r.detail.peak_memory_bytes) },
    { label: 'Process count', value: (r: ComparisonRecord) => formatMetric(metricFromFingerprint(r.fingerprint, 'process_count')) },
    { label: 'Child processes', value: (r: ComparisonRecord) => formatMetric(metricFromFingerprint(r.fingerprint, 'child_process_count')) },
    { label: 'Resource violations', value: (r: ComparisonRecord) => formatMetric(metricFromFingerprint(r.fingerprint, 'resource_violations')) },
    { label: 'File events', value: (r: ComparisonRecord) => formatMetric(metricFromFingerprint(r.fingerprint, 'file_events')) },
    { label: 'Behavior event count', value: (r: ComparisonRecord) => String(r.events.length) },
    { label: 'Fingerprint source', value: (r: ComparisonRecord) => r.fingerprint?.measurement_source ?? 'Unavailable' },
    { label: 'Alerts', value: (r: ComparisonRecord) => String(r.report?.alerts?.length ?? 0) },
    { label: 'Hardcoding review', value: (r: ComparisonRecord) => String((r.report?.analysis.hardcoding_analysis as { status?: string } | undefined)?.status ?? 'Unavailable') },
  ];
  return <div className="fade-in">
    <div className="comparison-grid" style={styles.comparisonGrid}>{records.map((record) => <section style={styles.detailCard} key={record.detail.id}>
      <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><FileCode2 size={14} /></span><h3>Submission #{record.detail.id}</h3></div><Link href={`/submissions/${record.detail.id}`} className="small-link">Open <ArrowRight size={12} /></Link></div>
      <div style={styles.resultMetrics}>
        <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>STATUS</span><span style={styles.resultMetricValue}>{record.detail.status}</span></div>
        <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>LANGUAGE</span><span style={styles.resultMetricValue}>{record.detail.language.toUpperCase()}</span></div>
        <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>WORKSPACE</span><span style={styles.resultMetricValue}>{record.detail.workspace_label ?? 'Unavailable'}</span></div>
        <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>TIMESTAMP</span><span style={styles.resultMetricValue}>{formatTime(record.detail.created_at)}</span></div>
      </div>
      {record.error && <div className="backend-feed-error">Report unavailable: {record.error}</div>}
    </section>)}</div>

    {/* Real pairwise fingerprint comparison from GET /analysis/fingerprints/compare (exactly two records). */}
    {pairwise.status === 'loading' && <div style={styles.inlineEmpty}>Loading pairwise fingerprint comparison …</div>}
    {pairwise.status === 'error' && <div className="backend-feed-error">Pairwise comparison unavailable: {pairwise.error}</div>}
    {pairwise.status === 'ready' && pairwise.data?.comparison && <section style={styles.detailCard}>
      <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><GitBranch size={14} /></span><h3>Fingerprint comparison</h3></div><span className="backendBadge">GET /analysis/fingerprints/compare</span></div>
      <p style={{ margin: '0 0 10px', color: '#8b968d', fontSize: 10, lineHeight: 1.5 }}>{pairwise.data.comparison.comparison.explanation}</p>
      <div style={{ display: 'grid', gap: 0 }}>
        <div className="comparison-row" style={comparisonRowStyle}><strong>Comparable fields</strong><span>{pairwise.data.comparison.comparison.comparable_fields.join(', ') || 'None'}</span></div>
        <div className="comparison-row" style={comparisonRowStyle}><strong>Identical</strong><span className="diff-mark">{pairwise.data.comparison.comparison.identical_fields.join(', ') || 'None'}</span></div>
        <div className="comparison-row" style={comparisonRowStyle}><strong>Different</strong><span className="diff-mark">{pairwise.data.comparison.comparison.different_fields.join(', ') || 'None'}</span></div>
        <div className="comparison-row" style={comparisonRowStyle}><strong>Similar</strong><span>{pairwise.data.comparison.comparison.similar_fields.join(', ') || 'None'}</span></div>
        <div className="comparison-row" style={comparisonRowStyle}><strong>Unavailable</strong><span>{pairwise.data.comparison.comparison.unavailable_fields.join(', ') || 'None'}</span></div>
      </div>
    </section>}

    <section style={styles.detailCard}><div className="backend-feed-caption">SIDE-BY-SIDE COMPARISON · REAL API VALUES ONLY</div>
      <div style={{ display: 'grid', gap: 0 }}>
        {metricRows.map((row) => <div className="comparison-row" key={row.label} style={comparisonRowStyle}>
          <strong>{row.label}</strong>
          {records.map((record) => <span key={record.detail.id}>{row.value(record)}</span>)}
        </div>)}
      </div>
    </section>

    <div className="comparison-grid" style={styles.comparisonGrid}>{records.map((record) => <section style={styles.detailCard} key={`events-${record.detail.id}`}>
      <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><GitBranch size={14} /></span><h3>Timeline #{record.detail.id}</h3></div><span className="backendBadge">{record.events.length} EVENTS</span></div>
      {record.events.length ? record.events.slice(0, 20).map((event, index) => <div className="sequence-row" key={event.id} title={event.severity}>
        <span className="sequence-number">{String(index + 1).padStart(2, '0')}</span>
        <code>{event.event_type}</code>
        <span>{event.source}</span>
      </div>) : <div style={styles.inlineEmpty}>No events returned by the backend for this submission.</div>}
    </section>)}</div>

    <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> Comparison highlights measurable differences only. Similarity, hardcoding signals, and event patterns require human review and are not accusations. The backend provides no validated cheating verdict.</div>
  </div>;
}

function SecurityAlertsPage({ api }: { api: BackendStatus }) {
  const search = useSearch();
  const [feed, setFeed] = useState<FeedState<ApiPage<AlertSummary>>>({ status: 'idle' });
  const initialReviewStatus = useMemo(() => new URLSearchParams(search).get('review_status') ?? '', [search]);
  const [reviewStatus, setReviewStatus] = useState(initialReviewStatus);
  const load = useCallback((signal?: AbortSignal) => {
    setFeed({ status: 'loading' });
    return listAlerts({ page: 1, page_size: 30, review_status: reviewStatus || undefined }, signal)
      .then((data) => setFeed({ status: 'ready', data }))
      .catch((error: unknown) => { if (!isAbortError(error)) setFeed({ status: 'error', error: describeApiError(error) }); });
  }, [reviewStatus]);
  useEffect(() => { if (api.state !== 'connected') return; const c = new AbortController(); load(c.signal); return () => c.abort(); }, [api.state, load]);
  return <div style={styles.content}><PageHeading eyebrow="FACULTY CONSOLE / ALERTS" title="Security Alerts" detail="Review backend-generated alerts without treating them as final accusations." trailing={<span className="backendBadge">LIVE BACKEND DATA</span>} /><div className="report-toolbar" style={styles.reportToolbar}><select className="filter-select" style={styles.filterSelect} value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}><option value="">All review states</option><option value="PENDING">Pending</option><option value="UNDER_REVIEW">Under review</option><option value="CONFIRMED">Confirmed</option><option value="DISMISSED">Dismissed</option></select><button className="action-button secondary" onClick={() => load()}><RefreshCw size={12} /> Retry</button></div>{feed.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /></div>}{feed.status === 'error' && <div className="backend-feed-error">{feed.error}</div>}{feed.status === 'ready' && <div style={styles.reportTable}>{(feed.data?.items ?? []).map((alert) => <Link key={alert.id} href={`/submissions/${alert.submission_id}`} className="report-row"><span><strong>#{alert.id} · {alert.alert_type}</strong><small>submission {alert.submission_id}</small><br/><span style={{ fontSize: 9, color: '#7e8981', maxWidth: 320, display: 'block', marginTop: 2 }}>{alert.explanation}</span></span><span className="report-signal"><span className="report-dot" />{alert.severity}</span><span><span className="report-class">{alert.review_status}</span></span><span className="report-time">{formatTime(alert.created_at)}</span><ArrowRight size={14} /></Link>)}{!(feed.data?.items ?? []).length && <div className="report-empty" style={styles.reportEmpty}><div className="empty-icon"><ShieldCheck size={16} /></div><strong>No alerts returned</strong><p>The backend returned an empty alert page for the selected review state. This is a real zero — no alerts were fabricated to populate the list. No review endpoint exists yet, so this page is read-only.</p><div style={styles.quickActions}><Link href="/submissions" className="action-button secondary">View submissions <ArrowRight size={12} /></Link><Link href="/analytics" className="action-button secondary">Behavior analytics <ArrowRight size={12} /></Link></div></div>}</div>}</div>;
}


function ProblemForm({
  initialData,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initialData: ProblemDetail | null;
  onSubmit: (data: ProblemCreate) => Promise<ProblemDetail>;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [title, setTitle] = useState(initialData?.title ?? '');
  const [slug, setSlug] = useState(initialData?.slug ?? '');
  const [description, setDescription] = useState(initialData?.description ?? '');
  const [inputFormat, setInputFormat] = useState(initialData?.input_format ?? '');
  const [outputFormat, setOutputFormat] = useState(initialData?.output_format ?? '');
  const [constraints, setConstraints] = useState(initialData?.constraints ?? '');
  const [difficulty, setDifficulty] = useState<Difficulty>(initialData?.difficulty ?? 'EASY');
  const [supportedLanguages, setSupportedLanguages] = useState<Language[]>((initialData?.supported_languages as Language[]) ?? ['c', 'cpp']);
  const [timeLimitMs, setTimeLimitMs] = useState<number | null>(initialData?.time_limit_ms ?? null);
  const [memoryLimitMb, setMemoryLimitMb] = useState<number | null>(initialData?.memory_limit_mb ?? null);
  const [processLimit, setProcessLimit] = useState<number | null>(initialData?.process_limit ?? null);
  const [visibility, setVisibility] = useState<'PUBLIC' | 'PRIVATE'>((initialData?.visibility as 'PUBLIC' | 'PRIVATE') ?? 'PUBLIC');
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await onSubmit({
        title,
        slug: slug || undefined,
        description,
        input_format: inputFormat,
        output_format: outputFormat,
        constraints,
        difficulty,
        supported_languages: supportedLanguages,
        time_limit_ms: timeLimitMs,
        memory_limit_mb: memoryLimitMb,
        process_limit: processLimit,
        visibility,
        test_cases: [],
      });
    } catch (err) {
      setError(describeApiError(err));
    }
  };

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <div className="form-group">
        <label>Title *</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Problem title" required />
      </div>
      <div className="form-group">
        <label>Slug</label>
        <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="URL-friendly identifier (auto-generated if empty)" />
      </div>
      <div className="form-group">
        <label>Description</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Problem statement" rows={4} />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Input Format</label>
          <textarea value={inputFormat} onChange={(e) => setInputFormat(e.target.value)} placeholder="Input format description" rows={3} />
        </div>
        <div className="form-group">
          <label>Output Format</label>
          <textarea value={outputFormat} onChange={(e) => setOutputFormat(e.target.value)} placeholder="Output format description" rows={3} />
        </div>
      </div>
      <div className="form-group">
        <label>Constraints</label>
        <textarea value={constraints} onChange={(e) => setConstraints(e.target.value)} placeholder="Constraints" rows={2} />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Difficulty *</label>
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value as Difficulty)}>
            <option value="EASY">Easy</option>
            <option value="MEDIUM">Medium</option>
            <option value="HARD">Hard</option>
          </select>
        </div>
        <div className="form-group">
          <label>Visibility *</label>
          <select value={visibility} onChange={(e) => setVisibility(e.target.value as 'PUBLIC' | 'PRIVATE')}>
            <option value="PUBLIC">Public</option>
            <option value="PRIVATE">Private</option>
          </select>
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Supported Languages</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={supportedLanguages.includes('c')} onChange={(e) => setSupportedLanguages(e.target.checked ? [...supportedLanguages, 'c'] : supportedLanguages.filter(l => l !== 'c'))} /> C
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={supportedLanguages.includes('cpp')} onChange={(e) => setSupportedLanguages(e.target.checked ? [...supportedLanguages, 'cpp'] : supportedLanguages.filter(l => l !== 'cpp'))} /> C++
            </label>
          </div>
        </div>
        <div className="form-group">
          <label>Time Limit (ms)</label>
          <input type="number" value={timeLimitMs ?? ''} onChange={(e) => setTimeLimitMs(e.target.value ? Number(e.target.value) : null)} placeholder="e.g. 1000" min={50} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Memory Limit (MB)</label>
          <input type="number" value={memoryLimitMb ?? ''} onChange={(e) => setMemoryLimitMb(e.target.value ? Number(e.target.value) : null)} placeholder="e.g. 128" min={8} />
        </div>
        <div className="form-group">
          <label>Process Limit</label>
          <input type="number" value={processLimit ?? ''} onChange={(e) => setProcessLimit(e.target.value ? Number(e.target.value) : null)} placeholder="e.g. 10" min={1} />
        </div>
      </div>
      {error && <div className="backend-feed-error">{error}</div>}
      <div className="form-actions">
        <button type="button" className="action-button secondary" onClick={onCancel} disabled={isLoading}>Cancel</button>
        <button type="submit" className="action-button primary" disabled={isLoading || !title}>
          {isLoading ? 'Saving...' : 'Save Problem'}
        </button>
      </div>
    </form>
  );
}

function ProblemPreview({ problem }: { problem: ProblemDetail }) {
  return (
    <div style={styles.preview}>
      <div className="preview-header">
        <h4>{problem.title}</h4>
        <div className="preview-meta">
          <span className={`difficulty-badge ${problem.difficulty.toLowerCase()}`}>{problem.difficulty}</span>
          <span className="mono">#{problem.id}</span>
          <span className="mono">{problem.slug}</span>
        </div>
      </div>
      <div className="preview-section">
        <h5>Description</h5>
        <p>{problem.description || 'No description provided.'}</p>
      </div>
      {problem.input_format && (
        <div className="preview-section">
          <h5>Input Format</h5>
          <p>{problem.input_format}</p>
        </div>
      )}
      {problem.output_format && (
        <div className="preview-section">
          <h5>Output Format</h5>
          <p>{problem.output_format}</p>
        </div>
      )}
      {problem.constraints && (
        <div className="preview-section">
          <h5>Constraints</h5>
          <p>{problem.constraints}</p>
        </div>
      )}
      <div className="preview-section">
        <h5>Sample Tests</h5>
        {problem.sample_tests?.length ? (
          <div style={styles.sampleTests}>
            {problem.sample_tests.map((test) => (
              <div key={test.id} className="sample-test">
                <div className="sample-test-input">
                  <strong>Input:</strong>
                  <pre>{test.input_data}</pre>
                </div>
                <div className="sample-test-output">
                  <strong>Output:</strong>
                  <pre>{test.expected_output}</pre>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p>No public sample tests available.</p>
        )}
      </div>
      <div className="preview-section">
        <h5>Test Case Summary</h5>
        <div style={{ display: 'flex', gap: 16 }}>
          <span>{problem.test_case_counts.public} public</span>
          <span>{problem.test_case_counts.hidden} hidden</span>
          <span>{problem.test_case_counts.generated} generated</span>
        </div>
      </div>
      <div className="preview-section">
        <h5>Limits</h5>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span>Time: {problem.time_limit_ms ? `${problem.time_limit_ms} ms` : 'Default'}</span>
          <span>Memory: {problem.memory_limit_mb ? `${problem.memory_limit_mb} MB` : 'Default'}</span>
          <span>Processes: {problem.process_limit ? problem.process_limit : 'Default'}</span>
        </div>
      </div>
    </div>
  );
}

function TestCaseManager({
  problemId,
  testCases,
  onCreate,
  onDelete,
  onClose,
}: {
  problemId: number;
  testCases: FeedState<TestCaseAdmin[]>;
  onCreate: (problemId: number, testCase: Omit<TestCaseCreate, 'problem_id'>) => Promise<void>;
  onDelete: (problemId: number, caseId: number) => Promise<void>;
  onClose: () => void;
}) {
  const [showCreate, setShowCreate] = useState(false);
  const [newInput, setNewInput] = useState('');
  const [newExpected, setNewExpected] = useState('');
  const [newIsPublic, setNewIsPublic] = useState(false);
  const [newLabel, setNewLabel] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setCreating(true);
    try {
      await onCreate(problemId, {
        input_data: newInput,
        expected_output: newExpected,
        is_public: newIsPublic,
        is_generated: false,
        label: newLabel || undefined,
      });
      setShowCreate(false);
      setNewInput('');
      setNewExpected('');
      setNewIsPublic(false);
      setNewLabel('');
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="test-case-manager" style={styles.testCaseManager}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h4>Test Cases for Problem #{problemId}</h4>
        <button className="action-button primary" onClick={() => setShowCreate(true)}><Plus size={12} /> Add Test Case</button>
      </div>

      {testCases.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading test cases...</span></div>}
      {testCases.status === 'error' && <div className="backend-feed-error">{testCases.error}</div>}

      {testCases.status === 'ready' && (
        <>
          {testCases.data?.length === 0 && <div className="report-empty" style={styles.reportEmpty}><div className="empty-icon"><FileSearch size={16} /></div><strong>No test cases</strong><p>Add test cases to make this problem submittable.</p></div>}
          {testCases.data?.map((tc) => (
            <div key={tc.id} className="report-row" style={{ borderBottom: '1px solid #252e28', padding: '10px 0' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mono" style={{ fontSize: 9 }}>#{tc.id} · {tc.is_public ? 'Public' : 'Hidden'}{tc.is_generated ? ' · Generated' : ''}{tc.label ? ` · ${tc.label}` : ''}</div>
                <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <strong>Input:</strong>
                    <pre className="mono" style={{ fontSize: 9, maxHeight: 60, overflow: 'auto' }}>{tc.input_data}</pre>
                  </div>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <strong>Expected:</strong>
                    <pre className="mono" style={{ fontSize: 9, maxHeight: 60, overflow: 'auto' }}>{tc.expected_output}</pre>
                  </div>
                  <button className="action-button secondary" style={{ padding: '4px 8px', color: '#d79385', alignSelf: 'flex-start' }} onClick={() => { if (confirm('Delete this test case?')) onDelete(problemId, tc.id); }}>
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </>
      )}

      {showCreate && (
        <form onSubmit={handleCreate} style={{ marginTop: 16, padding: 16, border: '1px solid #2a332d', borderRadius: 4, background: '#111613' }}>
          <h5>Add Test Case</h5>
          <div className="form-row">
            <div className="form-group">
              <label>Input</label>
              <textarea value={newInput} onChange={(e) => setNewInput(e.target.value)} placeholder="Test input" rows={4} required />
            </div>
            <div className="form-group">
              <label>Expected Output</label>
              <textarea value={newExpected} onChange={(e) => setNewExpected(e.target.value)} placeholder="Expected output" rows={4} required />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Public Test Case</label>
              <select value={newIsPublic ? 'true' : 'false'} onChange={(e) => setNewIsPublic(e.target.value === 'true')}>
                <option value="false">Hidden (judging only)</option>
                <option value="true">Public (shown to students)</option>
              </select>
            </div>
            <div className="form-group">
              <label>Label</label>
              <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder="e.g. boundary-1" />
            </div>
          </div>
          {error && <div className="backend-feed-error">{error}</div>}
          <div className="form-actions">
            <button type="button" className="action-button secondary" onClick={() => { setShowCreate(false); setNewInput(''); setNewExpected(''); setNewIsPublic(false); setNewLabel(''); }}>Cancel</button>
            <button type="submit" className="action-button primary" disabled={creating || !newInput || !newExpected}>
              {creating ? 'Adding...' : 'Add Test Case'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function ProblemsPage({ api }: { api: BackendStatus }) {
  const [problems, setProblems] = useState<FeedState<ApiPage<ProblemSummary>>>({ status: 'idle' });
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editingProblem, setEditingProblem] = useState<ProblemDetail | null>(null);
  const [viewingProblem, setViewingProblem] = useState<ProblemDetail | null>(null);
  const [showTestCases, setShowTestCases] = useState<number | null>(null);
  const [testCases, setTestCases] = useState<FeedState<TestCaseAdmin[]>>({ status: 'idle' });
  const [loading, setLoading] = useState(false);

  const loadProblems = useCallback((signal?: AbortSignal) => {
    setProblems({ status: 'loading' });
    listProblems({ page: 1, page_size: 50 }, signal)
      .then((data) => setProblems({ status: 'ready', data }))
      .catch((error: unknown) => { if (!isAbortError(error)) setProblems({ status: 'error', error: describeApiError(error) }); });
  }, []);

  useEffect(() => {
    if (api.state !== 'connected') { setProblems({ status: 'idle' }); return; }
    const controller = new AbortController();
    loadProblems(controller.signal);
    return () => controller.abort();
  }, [api.state, loadProblems]);

  const loadTestCases = useCallback((problemId: number) => {
    setShowTestCases(problemId);
    setTestCases({ status: 'loading' });
    listProblemTestCases(problemId)
      .then((data) => setTestCases({ status: 'ready', data }))
      .catch((error: unknown) => { if (!isAbortError(error)) setTestCases({ status: 'error', error: describeApiError(error) }); });
  }, []);

  const handleCreate = async (formData: ProblemCreate) => {
    setLoading(true);
    try {
      const created = await createProblem(formData);
      setShowCreate(false);
      loadProblems();
      return created;
    } catch (error) {
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const handleTestCaseCreate = async (problemId: number, testCase: Omit<TestCaseCreate, 'problem_id'>) => {
    try {
      await addTestCase(problemId, testCase);
      loadTestCases(problemId);
    } catch (error) {
      throw error;
    }
  };

  const handleDeleteTestCase = async (problemId: number, caseId: number) => {
    try {
      await deleteTestCase(problemId, caseId);
      loadTestCases(problemId);
    } catch (error) {
      throw error;
    }
  };

  const handleViewProblem = async (problemId: number) => {
    setViewingProblem(null);
    try {
      const detail = await getProblem(problemId);
      setViewingProblem(detail);
    } catch (error) {
      setViewingProblem(null);
      throw error;
    }
  };

  const handleEditProblem = async (problemId: number) => {
    setEditingProblem(null);
    try {
      const detail = await getProblem(problemId);
      setEditingProblem(detail);
    } catch (error) {
      setEditingProblem(null);
      throw error;
    }
  };

  const filteredProblems = problems.data?.items?.filter((p) =>
    p.title.toLowerCase().includes(search.toLowerCase()) ||
    p.slug.toLowerCase().includes(search.toLowerCase()) ||
    String(p.id).includes(search)
  ) ?? [];

  return (
    <div style={styles.content}>
      <PageHeading eyebrow="FACULTY CONSOLE / PROBLEMS" title="Manage Problems" detail="Create and manage programming problems and their test cases. Local-dev mode is not authentication." trailing={<span className="backendBadge">LIVE BACKEND DATA</span>} />
      <ApiNotice api={api} compact />
      <div className="report-toolbar" style={styles.reportToolbar}>
        <div className="searchBox search-box" style={styles.searchBox}>
          <Search size={14} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title, slug, or ID"
          />
        </div>
        <button className="action-button secondary" onClick={() => loadProblems()} disabled={problems.status === 'loading'}>
          <RefreshCw size={12} /> Refresh
        </button>
        <button className="action-button primary" onClick={() => { setEditingProblem(null); setShowCreate(true); }}>
          <Plus size={12} /> Create Problem
        </button>
      </div>

      {problems.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading problems...</span></div>}
      {problems.status === 'error' && <div className="backend-feed-error">{problems.error}</div>}

      {problems.status === 'ready' && (
        <>
          <div className="backend-feed-caption">{filteredProblems.length} OF {problems.data?.pagination?.total ?? 0} PROBLEMS</div>
          <div className="report-table" style={styles.reportTable}>
            <div className="report-table-head problem-table-head">
              <span>ID</span>
              <span>TITLE</span>
              <span>SLUG</span>
              <span>DIFFICULTY</span>
              <span>TESTS</span>
              <span>ACTIONS</span>
            </div>
            {filteredProblems.length === 0 ? (
              <div className="report-empty" style={styles.reportEmpty}>
                <div className="empty-icon"><FileSearch size={16} /></div>
                <strong>No problems match</strong>
                <p>Adjust your search or create a new problem.</p>
              </div>
            ) : (
              filteredProblems.map((problem) => (
                <div key={problem.id} className="report-row problem-row">
                  <span className="mono">#{problem.id}</span>
                  <span><strong>{problem.title}</strong></span>
                  <span className="mono">{problem.slug}</span>
                  <span><span className={`report-class ${problem.difficulty.toLowerCase()}`}>{problem.difficulty}</span></span>
                  <span className="mono">{problem.test_case_counts.public} public / {problem.test_case_counts.hidden} hidden</span>
                  <div className="problem-actions" style={{ display: 'flex', gap: 4 }}>
                    <button className="action-button secondary" style={{ padding: '4px 8px' }} onClick={() => { handleViewProblem(problem.id); setViewingProblem(null); }}>
                      <Eye size={12} /> View
                    </button>
                    <button className="action-button secondary" style={{ padding: '4px 8px' }} onClick={() => handleEditProblem(problem.id)}>
                      <Edit size={12} /> Edit
                    </button>
                    <button className="action-button secondary" style={{ padding: '4px 8px' }} onClick={() => loadTestCases(problem.id)}>
                      <List size={12} /> Tests
                    </button>
                    <button className="action-button secondary" style={{ padding: '4px 8px', color: '#d79385' }} onClick={() => { if (confirm(`Delete problem "${problem.title}"?`)) deleteProblem(problem.id).then(() => loadProblems()).catch(e => alert(describeApiError(e))); }}>
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}

      {/* Create/Edit Problem Modal */}
      {(showCreate || editingProblem) && (
        <div className="modal-overlay" onClick={() => { setShowCreate(false); setEditingProblem(null); }}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={styles.modalContent}>
            <div className="modal-header">
              <h3>{editingProblem ? 'Edit Problem' : 'Create Problem'}</h3>
              <button className="modal-close" onClick={() => { setShowCreate(false); setEditingProblem(null); }}><X size={16} /></button>
            </div>
            <ProblemForm
              initialData={editingProblem}
              onSubmit={handleCreate}
              onCancel={() => { setShowCreate(false); setEditingProblem(null); }}
              isLoading={loading}
            />
          </div>
        </div>
      )}

      {/* Problem Preview Modal */}
      {viewingProblem && (
        <div className="modal-overlay" onClick={() => setViewingProblem(null)}>
          <div className="modal-content large" onClick={(e) => e.stopPropagation()} style={styles.modalContent}>
            <div className="modal-header">
              <h3>Problem Preview</h3>
              <button className="modal-close" onClick={() => setViewingProblem(null)}><X size={16} /></button>
            </div>
            <ProblemPreview problem={viewingProblem} />
          </div>
        </div>
      )}

      {/* Test Cases Modal */}
      {showTestCases !== null && (
        <div className="modal-overlay" onClick={() => setShowTestCases(null)}>
          <div className="modal-content large" onClick={(e) => e.stopPropagation()} style={styles.modalContent}>
            <div className="modal-header">
              <h3>Test Cases</h3>
              <button className="modal-close" onClick={() => setShowTestCases(null)}><X size={16} /></button>
            </div>
            <TestCaseManager
              problemId={showTestCases}
              testCases={testCases}
              onCreate={handleTestCaseCreate}
              onDelete={handleDeleteTestCase}
              onClose={() => setShowTestCases(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function BehaviorAnalyticsPage({ api }: { api: BackendStatus }) {
  const [dashboard, setDashboard] = useState<FeedState<DashboardResponse>>({ status: 'idle' });
  useEffect(() => { if (api.state !== 'connected') return; const c = new AbortController(); setDashboard({ status: 'loading' }); getDashboard(c.signal).then((data) => setDashboard({ status: 'ready', data })).catch((error: unknown) => { if (!isAbortError(error)) setDashboard({ status: 'error', error: describeApiError(error) }); }); return () => c.abort(); }, [api.state]);
  const byStatus = dashboard.data?.submissions.by_status ?? {};
  const total = dashboard.data?.submissions.total ?? 0;
  const coverage = dashboard.data?.resource_metric_coverage ?? {};
  const statusEntries = Object.entries(byStatus);
  return <div style={styles.content}><PageHeading eyebrow="FACULTY CONSOLE / BEHAVIOR" title="Behavior Analytics" detail="Backend-derived execution behavior, resource metric coverage, and recent security signals. Simulations are intentionally excluded." trailing={<span className="backendBadge">OBSERVED / STORED DATA</span>} /><ApiNotice api={api} compact />{dashboard.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /></div>}{dashboard.status === 'error' && <div className="backend-feed-error">{dashboard.error}</div>}{dashboard.status === 'ready' && <><DashboardMetrics dashboard={dashboard.data} /><div className="detail-columns" style={styles.detailColumns}><section style={styles.dashboardPanel}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Cpu size={14} /></span><h3>Outcome distribution</h3></div><span className="backendBadge">DASHBOARD API</span></div><div className="backend-feed-caption">SAMPLE SIZE: {total} SUBMISSION{total === 1 ? '' : 'S'}{statusEntries.length < 2 ? ' · TOO SMALL FOR A MEANINGFUL TREND' : ''}</div>{statusEntries.map(([status, count]) => <div className="comparison-row" key={status}><strong>{status}</strong><span>{count}</span></div>)}{!statusEntries.length && <div style={styles.inlineEmpty}>No outcome data was returned by the dashboard.</div>}{total < 5 && <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> Only {total} backend submission{total === 1 ? '' : 's'} exist. No trend or statistical claim is made from a sample this small.</div>}</section><section style={styles.dashboardPanel}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><FileSearch size={14} /></span><h3>Resource metric coverage</h3></div><span className="backendBadge">DASHBOARD API</span></div>{Object.entries(coverage).map(([key, value]) => <div className="comparison-row" key={key}><strong>{key.replace(/_/g, ' ')}</strong><span>{String(value)}</span></div>)}{!Object.keys(coverage).length && <div style={styles.inlineEmpty}>No resource coverage metrics were returned.</div>}</section></div></>}</div>;
}

function TrustRiskPage({ api, workspaceLabel }: { api: BackendStatus; workspaceLabel: string }) {
  const [label, setLabel] = useState(workspaceLabel || 'local-workspace');
  const [trust, setTrust] = useState<FeedState<TrustHistoryResponse>>({ status: 'idle' });
  const load = useCallback((signal?: AbortSignal) => { setTrust({ status: 'loading' }); return getTrustHistory(label, 50, signal).then((data) => setTrust({ status: 'ready', data })).catch((error: unknown) => { if (!isAbortError(error)) setTrust({ status: 'error', error: describeApiError(error) }); }); }, [label]);
  useEffect(() => { if (api.state !== 'connected') return; const c = new AbortController(); load(c.signal); return () => c.abort(); }, [api.state, load]);
  return <div style={styles.content}><PageHeading eyebrow="FACULTY CONSOLE / TRUST" title="Trust & Risk History" detail="Workspace-scoped adaptive trust history. This is not user authentication and never changes execution limits." trailing={<span className="backendBadge">LIVE BACKEND DATA</span>} /><div className="report-toolbar" style={styles.reportToolbar}><div className="searchBox search-box" style={styles.searchBox}><Gauge size={14} /><input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Workspace label" /></div><button className="action-button secondary" onClick={() => load()}><RefreshCw size={12} /> Load history</button></div>{trust.status === 'loading' && <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /></div>}{trust.status === 'error' && <div className="backend-feed-error">{trust.error}</div>}{trust.status === 'ready' && <section style={styles.detailCard}><div style={styles.resultMetrics}><div style={styles.resultMetric}><span style={styles.resultMetricLabel}>SCORE</span><span style={styles.resultMetricValue}>{trust.data?.current_score ?? 'Unavailable'}</span></div><div style={styles.resultMetric}><span style={styles.resultMetricLabel}>LEVEL</span><span style={styles.resultMetricValue}>{trust.data?.monitoring_level ?? 'Unavailable'}</span></div><div style={styles.resultMetric}><span style={styles.resultMetricLabel}>OPEN ALERTS</span><span style={styles.resultMetricValue}>{trust.data?.open_alerts ?? 'Unavailable'}</span></div></div>{(trust.data?.entries ?? []).map((entry) => <div className="sequence-row" key={entry.id}><span className="sequence-number">{entry.previous_score}→{entry.new_score}</span><code>{entry.reason}</code><span>{formatTime(entry.created_at)}</span></div>)}{!(trust.data?.entries ?? []).length && <div style={styles.inlineEmpty}>No trust-history rows returned for this workspace label.</div>}</section>}</div>;
}

const CAPABILITY_TONE_CLASS: Record<string, string> = {
  ACTIVE: '',
  MEASURED_ONLY: 'watch',
  PARTIAL: 'watch',
  NOT_AVAILABLE: 'alert',
  RESERVED: 'watch',
};

function capabilityToneClass(status: string): string {
  return CAPABILITY_TONE_CLASS[status] ?? 'watch';
}

function SystemHealthPage({ api, onRetry }: { api: BackendStatus; onRetry: () => void }) {
  const system = api.system;
  return <div style={styles.content}>
    <PageHeading eyebrow="DEVELOPMENT WORKSPACE / HEALTH" title="System Health" detail="Backend connectivity, database state, compilers, workers, and the real capability and limitation matrix reported by the execution engine." trailing={<button className="action-button secondary" onClick={onRetry}><RefreshCw size={12} /> Retry probe</button>} />
    <ApiNotice api={api} />
    {!system ? <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><ShieldAlert size={16} /></div><strong>System status unavailable</strong><p>{api.state === 'connected' ? 'GET /api/v1/system/status did not return a usable response.' : 'The backend is not reachable. Start the backend to inspect system health.'}</p></div> :
    <div className="fade-in">
      <div className="detail-columns" style={styles.detailColumns}>
        <section style={styles.detailCard}>
          <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Cpu size={14} /></span><h3>Runtime</h3></div><span className="backendBadge">SYSTEM STATUS</span></div>
          <div className="sequence-row"><span className="sequence-number">OS</span><code>{String(system.os.system)} {String(system.os.release)} ({String(system.os.machine)})</code><span>REPORTED</span></div>
          <div className="sequence-row"><span className="sequence-number">Python</span><code>{String(system.python.implementation)} {String(system.python.version)}</code><span>REPORTED</span></div>
          <div className="sequence-row"><span className="sequence-number">Environment</span><code>{system.environment}</code><span>REPORTED</span></div>
          <div className="sequence-row"><span className="sequence-number">Auth mode</span><code>{system.auth_mode}</code><span className={`tone-label ${system.auth_mode === 'local_dev' ? 'watch' : ''}`}>{system.auth_mode === 'local_dev' ? 'NO REAL AUTH' : 'AUTH'}</span></div>
          <div className="sequence-row"><span className="sequence-number">Database</span><code>{system.database.config_state} · {system.database.connectivity}</code><span className={`tone-label ${system.database.connectivity === 'REACHABLE' ? '' : 'alert'}`}>{system.database.connectivity}</span></div>
        </section>
        <section style={styles.detailCard}>
          <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Activity size={14} /></span><h3>Execution worker</h3></div><span className="backendBadge">SYSTEM STATUS</span></div>
          <div style={styles.resultMetrics}>
            <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>RUNNING</span><span style={styles.resultMetricValue}>{system.execution_worker.running ? 'YES' : 'NO'}</span></div>
            <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>ACTIVE</span><span style={styles.resultMetricValue}>{system.execution_worker.active_executions}</span></div>
            <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>QUEUED</span><span style={styles.resultMetricValue}>{system.execution_worker.queued_jobs}</span></div>
            <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>MAX CONCURRENT</span><span style={styles.resultMetricValue}>{system.execution_worker.max_concurrent_executions}</span></div>
            <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>MAX QUEUED</span><span style={styles.resultMetricValue}>{system.execution_worker.max_queued_jobs}</span></div>
          </div>
          <div className="backend-feed-caption" style={{ marginTop: 10 }}>COMPILERS</div>
          {system.compilers.map((compiler) => <div className="sequence-row" key={compiler.language}><span className="sequence-number">{compiler.language}</span><code>{compiler.available ? compiler.path ?? compiler.compiler : 'Unavailable'}{compiler.version ? ` · ${compiler.version.split(' ')[0]}` : ''}</code><span>{compiler.available ? 'READY' : 'NO'}</span></div>)}
          <div className="sequence-row"><span className="sequence-number">Job Objects</span><code>{system.job_object.detail}</code><span className={`tone-label ${system.job_object.available ? '' : 'alert'}`}>{system.job_object.available ? 'AVAILABLE' : 'NO'}</span></div>
        </section>
      </div>
      <section style={styles.detailCard}>
        <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><ShieldCheck size={14} /></span><h3>Capability &amp; limitation matrix</h3></div><span className="backendBadge">REPORTED BY BACKEND</span></div>
        {system.capabilities.map((capability) => <div className="capability-row-grid" key={capability.id}>
          <span>{capability.name}</span>
          <span className={`capability-status tone-label ${capabilityToneClass(capability.status)}`}>{capability.status}</span>
          <span>{capability.detail}</span>
        </div>)}
      </section>
      {(system.warnings ?? []).length > 0 && <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> {system.warnings!.join(' ')}</div>}
      <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> CPU usage is measured (MEASURED_ONLY); the reliable stopping mechanism is the wall-clock deadline. Job Objects are not a complete sandbox. Local-dev auth mode verifies no credentials and is not real authentication or RBAC.</div>
    </div>}
  </div>;
}

function ReportsPage({ store, api }: { store: WorkspaceStore; api: BackendStatus }) {
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [feed, setFeed] = useState<BackendFeedId>('submissions');
  const [feedState, setFeedState] = useState<FeedState<unknown>>({ status: 'idle' });
  const loadBackendRecords = async (selected: BackendFeedId) => {
    setFeed(selected);
    setFeedState({ status: 'loading' });
    try {
      if (selected === 'alerts') setFeedState({ status: 'ready', data: await listAlerts({ page: 1, page_size: 10 }) });
      else if (selected === 'dashboard') setFeedState({ status: 'ready', data: await getDashboard() });
      else setFeedState({ status: 'ready', data: await listAllSubmissions({ page: 1, page_size: 10 }) });
    } catch (error) {
      setFeedState({ status: 'error', error: describeApiError(error) });
    }
  };
  const runs = useMemo(() => store.simulations.filter((run) => {
    const matchesType = filter === 'all' || run.scenario === filter;
    const matchesQuery = `${run.name} ${run.id} ${run.analysis.fingerprint}`.toLowerCase().includes(search.toLowerCase());
    return matchesType && matchesQuery;
  }), [store.simulations, filter, search]);
  return <div style={styles.content}>
    <PageHeading eyebrow="OBSERVABILITY / REPORTS" title="Security reports" detail="Filter local teaching simulations. Backend records load on demand from the API." trailing={<span className="simulationBadge"><Activity size={12} /> LOCAL HISTORY</span>} />
    <div className="report-toolbar" style={styles.reportToolbar}>
      <div className="searchBox search-box" style={styles.searchBox}><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter by scenario or run ID" data-testid="input-report-search" /></div>
      <select className="filter-select" value={filter} onChange={(event) => setFilter(event.target.value)} style={styles.filterSelect} aria-label="Filter simulation scenario" data-testid="select-report-filter">
        <option value="all">All local scenarios</option>{Object.entries(scenarioDefinitions).map(([key, item]) => <option value={key} key={key}>{item.name}</option>)}
      </select>
      <div className="demoMicroBadge">SIMULATION / DEMO DATA</div>
    </div>
    {api.state === 'connected' && <section className="backend-feed" data-testid="panel-backend-report-feed">
      <div className="backend-feed-head">
        <div><span className="backendBadge">BACKEND RECORDS</span><span className="backend-feed-description">Requested only on demand.</span></div>
        <div className="backend-feed-actions">
          <button className={`action-button secondary ${feed === 'submissions' && feedState.status !== 'idle' ? 'primary' : ''}`} disabled={feedState.status === 'loading'} onClick={() => loadBackendRecords('submissions')} data-testid="button-load-submissions">Submissions <ArrowRight size={12} /></button>
          <button className={`action-button secondary ${feed === 'alerts' && feedState.status !== 'idle' ? 'primary' : ''}`} disabled={feedState.status === 'loading'} onClick={() => loadBackendRecords('alerts')} data-testid="button-load-alerts">Security alerts <ArrowRight size={12} /></button>
          <button className={`action-button secondary ${feed === 'dashboard' && feedState.status !== 'idle' ? 'primary' : ''}`} disabled={feedState.status === 'loading'} onClick={() => loadBackendRecords('dashboard')} data-testid="button-load-dashboard">Dashboard summary <ArrowRight size={12} /></button>
        </div>
      </div>
      {feedState.status === 'error' && <div className="backend-feed-error">{feedState.error}</div>}
      {feedState.status === 'ready' && <div className="backend-feed-body" data-testid="text-backend-report-data">{renderBackendFeed(feed, feedState.data)}</div>}
      {feedState.status === 'idle' && <div className="backend-feed-body"><div className="backend-feed-caption">NO REQUEST SENT YET</div></div>}
    </section>}
    <div style={styles.reportTable}>
      <div className="report-table-head"><span>SCENARIO / ID</span><span>SIGNAL</span><span>CLASSIFICATION</span><span>TIME</span><span /></div>
      {runs.map((run) => <Link key={run.id} href={`/submissions/${run.id}`} className="report-row" data-testid={`row-report-${run.id}`}>
        <span><strong>{run.name}</strong><small className="mono">{run.id}</small></span><span className="report-signal"><span className="report-dot" />{run.analysis.fingerprint}</span><span><span className="report-class">{scenarioDefinitions[run.scenario].classification}</span></span><span className="report-time">{formatTime(run.createdAt)}</span><ArrowRight size={14} />
      </Link>)}
      {!runs.length && <div className="report-empty"><div className="empty-icon"><FileSearch size={16} /></div><strong>{store.simulations.length ? 'No reports match these filters' : 'No local reports yet'}</strong><p>{store.simulations.length ? 'Clear the search or select a different scenario.' : 'Complete a deterministic simulation to create a local report. No records are fabricated.'}</p>{store.simulations.length > 0 && <button className="action-button secondary" onClick={() => { setFilter('all'); setSearch(''); }} data-testid="button-clear-report-filters">Clear filters <X size={12} /></button>}</div>}
    </div>
    <div style={styles.reportNote}><Shield size={13} /><span>Local simulation history is stored in this browser. Backend records, when supported, remain separate.</span></div>
  </div>;
}

function SubmissionRoute({ store, api }: { store: WorkspaceStore; api: BackendStatus }) {
  const params = useParams<{ id: string }>();
  return <SubmissionPage id={params.id ?? ''} store={store} api={api} />;
}

/**
 * `/submissions/:id` renders the local simulation record when the id is a local
 * simulation id, otherwise it loads the stored backend submission, its report
 * and its observed events. Missing data is stated, never invented.
 */
function SubmissionPage({ id, store, api }: { id: string; store: WorkspaceStore; api: BackendStatus }) {
  const local = store.simulations.find((run) => run.id === id);
  const isBackendRecord = !local && /^\d+$/.test(id);
  const [detail, setDetail] = useState<SubmissionDetail | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [reportError, setReportError] = useState('');
  const [events, setEvents] = useState<EventResponse[]>([]);
  const [eventsError, setEventsError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setDetail(null);
    setReport(null);
    setReportError('');
    setEvents([]);
    setEventsError('');
    setError('');
    if (local || !isBackendRecord || api.state !== 'connected') return;
    const controller = new AbortController();
    setLoading(true);
    (async () => {
      try {
        const submission = await getSubmission(id, controller.signal);
        setDetail(submission);
      } catch (reason) {
        if (!isAbortError(reason)) setError(describeApiError(reason));
        return;
      }
      try {
        setReport(await getSubmissionReport(id, controller.signal));
      } catch (reason) {
        if (!isAbortError(reason)) setReportError(describeApiError(reason));
      }
      try {
        const page = await listSubmissionEvents(id, { page: 1, page_size: 50 }, controller.signal);
        setEvents(page.items);
      } catch (reason) {
        if (!isAbortError(reason)) setEventsError(describeApiError(reason));
      }
    })().finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [id, local, isBackendRecord, api.state]);

  const signalSet = report ? backendSignals(report) : null;
  const measurements = (report?.resource_measurements ?? {}) as Record<string, unknown>;
  const enforcement = (measurements.enforcement ?? null) as Record<string, string> | null;

  return <div style={styles.content}>
    <PageHeading eyebrow="REPORT / DETAIL" title={local ? local.name : 'Submission detail'} detail={local ? 'Deterministic local simulation · not a real submission.' : 'Stored backend record: execution result, behavior analysis and observed events.'} trailing={<span className={local ? 'simulationBadge' : 'backendBadge'}>{local ? 'SIMULATION / DEMO DATA' : 'BACKEND RECORD'}</span>} />
    <div style={styles.detailId}><span className="mono">RECORD ID</span><code data-testid="text-submission-id">{id}</code>{local && <span className="demoMicroBadge">LOCAL ONLY</span>}</div>
    {local ? <div className="fade-in">
    <div className="detail-summary" style={styles.detailSummary}>
        <div><div style={styles.eyebrow}>DETERMINISTIC SCENARIO</div><h2 style={styles.sectionTitle}>{scenarioDefinitions[local.scenario].name}</h2><p>{scenarioDefinitions[local.scenario].description}</p></div>
        <div className="detail-classification" style={styles.detailClassification}><span>CLASSIFICATION</span><strong>{scenarioDefinitions[local.scenario].classification}</strong><small>{formatTime(local.createdAt)} · browser-local history</small></div>
      </div>
      <AnalysisPanels analysis={local.analysis} prefix="submission" />
      <div className="detail-columns" style={styles.detailColumns}>
        <section style={styles.detailCard}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Activity size={14} /></span><h3>Behavior findings</h3></div><span className="demoMicroBadge">SIMULATED</span></div>
          {local.analysis.signals.map((signal) => <div className={`signal-detail ${signal.tone}`} key={signal.id}><span className={`signal-detail-dot ${signal.tone}`} /><div><div className="signal-detail-title">{signal.label}<span className={`tone-label ${signal.tone}`}>{signal.value}</span></div><p>{signal.detail}</p></div></div>)}
        </section>
        <section style={styles.detailCard}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><GitBranch size={14} /></span><h3>Observed event sequence</h3></div></div>
          {local.analysis.sequence.map((event, index) => <div className="sequence-row" key={`${event}-${index}`}><span className="sequence-number">{String(index + 1).padStart(2, '0')}</span><code>{event}</code><span>DEMO</span></div>)}
        </section>
      </div>
      <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> All values and event labels above are deterministic demonstration data. No process, file operation, measurement, or security finding came from a real execution.</div>
    </div> : loading ? <div style={styles.loadingState}><div className="skeleton-line" /><div className="skeleton-block" /><span>Loading GET /api/v1/submissions/{id} …</span></div> :
      detail ? <div className="fade-in">
        <div className="detail-summary" style={styles.detailSummary}>
          <div><div style={styles.eyebrow}>BACKEND EXECUTION RECORD</div><h2 style={styles.sectionTitle}>Submission #{detail.id} · {detail.language.toUpperCase()}</h2>
            <p>{detail.is_custom_run ? 'Custom run — executed with the supplied stdin, no test evaluation.' : `Judged against problem ${detail.problem_id}`} · workspace {detail.workspace_label ?? 'local-workspace'}</p>
            <div style={styles.resultMetrics}>
              <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>STATUS</span><span style={styles.resultMetricValue}>{detail.status}</span></div>
              {!isNullish(detail.exit_code) && <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>EXIT</span><span style={styles.resultMetricValue}>{detail.exit_code}</span></div>}
              {!isNullish(detail.execution_time_ms) && <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>WALL</span><span style={styles.resultMetricValue}>{detail.execution_time_ms} ms</span></div>}
              {!isNullish(detail.cpu_time_ms) && <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>CPU</span><span style={styles.resultMetricValue}>{detail.cpu_time_ms} ms</span></div>}
              {!isNullish(detail.peak_memory_bytes) && <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>PEAK MEM</span><span style={styles.resultMetricValue}>{formatBytes(detail.peak_memory_bytes)}</span></div>}
              {typeof measurements.spawn_ms === 'number' && <div style={styles.resultMetric}><span style={styles.resultMetricLabel}>SPAWN</span><span style={styles.resultMetricValue}>{measurements.spawn_ms} ms</span></div>}
            </div>
          </div>
          <div className="detail-classification" style={styles.detailClassification}><span>STATUS</span><strong className={`tone-label ${TERMINAL_TONE[detail.status] ?? 'watch'}`} style={{ fontSize: 12 }}>{detail.status}</strong><small>{formatTime(detail.created_at)}{detail.completed_at ? ` · completed ${formatTime(detail.completed_at)}` : ''}</small></div>
        </div>
        <div style={styles.quickActions}>
          <Link href="/submissions" className="action-button secondary">Back to submissions <ArrowRight size={12} /></Link>
          <Link href="/compare" className="action-button secondary">Compare with another submission <GitBranch size={12} /></Link>
          <Link href="/alerts" className="action-button secondary">Security alerts <ShieldAlert size={12} /></Link>
          <Link href="/reports" className="action-button secondary">Reports <FileSearch size={12} /></Link>
        </div>
        <AnalysisPanels analysis={signalSet ?? undefined} prefix="submission" source="backend" />
        {reportError && <div className="backend-feed-error">Report unavailable: {reportError}</div>}
        <div className="detail-columns" style={styles.detailColumns}>
          <section style={styles.detailCard}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><Activity size={14} /></span><h3>Behavior findings</h3></div><span className="backendBadge">RETURNED BY API</span></div>
            {signalSet ? signalSet.signals.map((signal) => <div className={`signal-detail ${signal.tone}`} key={signal.id}><span className={`signal-detail-dot ${signal.tone}`} /><div><div className="signal-detail-title">{signal.label}<span className={`tone-label ${signal.tone}`}>{signal.value}</span></div><p>{signal.detail}</p></div></div>) :
              <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><FileSearch size={16} /></div><strong>No analysis stored</strong><p>The backend returned no report for this submission.</p></div>}
            {detail.error_summary && <div style={styles.resultError}><AlertTriangle size={11} /> {detail.error_summary}</div>}
          </section>
          <section style={styles.detailCard}><div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><GitBranch size={14} /></span><h3>Observed event sequence</h3></div><span className="backendBadge">{events.length} EVENTS</span></div>
            {events.length ? events.map((event, index) => <div className="sequence-row" key={event.id}><span className="sequence-number">{String(index + 1).padStart(2, '0')}</span><code>{event.event_type}</code><span title={`${event.severity} · ${event.details ? JSON.stringify(event.details).slice(0, 160) : ''}`}>{event.source}</span></div>) :
              <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><ShieldCheck size={16} /></div><strong>{eventsError ? 'Event stream unavailable' : 'No stored events'}</strong><p>{eventsError || `GET /api/v1/analysis/submissions/${id}/events returned no rows for this submission.`}</p></div>}
          </section>
        </div>
        <section style={styles.detailCard} data-testid="text-backend-test-results">
          <div className="cardHeader" style={styles.cardHeader}><div><span className="cardIcon"><ShieldCheck size={14} /></span><h3>Test results</h3></div><span className="backendBadge">RETURNED BY API</span></div>
          {(detail.test_results ?? []).length ? (detail.test_results ?? []).map((result) => <div className="test-row" key={result.id} style={styles.testRow}>
            <span className="mono">#{result.test_index}</span>
            <span className={`tone-label ${TERMINAL_TONE[result.status] ?? 'watch'}`}>{result.status}</span>
            <span className="mono">{result.test_is_public ? 'public' : 'hidden'}</span>
            <span className="mono">{isNullish(result.execution_time_ms) ? 'time not measured' : `${result.execution_time_ms} ms`}</span>
            <span className="mono">{formatBytes(result.memory_bytes)}</span>
          </div>) : <div className="monitor-empty" style={styles.monitorEmpty}><div className="empty-icon"><FileSearch size={16} /></div><strong>No per-test rows</strong><p>Hidden test inputs and expected outputs are never returned to the frontend.</p></div>}
        </section>
        {enforcement && <div className="mono" style={styles.resultLimits} title="Per-limit state reported by the execution engine">
          LIMITS {Object.entries(enforcement).map(([key, value]) => `${key.replace(/_limit$/, '').replace(/_/g, ' ')}=${value}`).join(' · ')}
        </div>}
        <div style={styles.detailDisclaimer}><AlertTriangle size={14} /> {report?.limitations?.length ? report.limitations.slice(0, 2).join(' ') : 'Job Objects are not a complete sandbox. Sequence and hardcoding results are assistive signals for human review, not proof of misconduct.'}</div>
      </div> :
        <div className="no-record" style={styles.noRecord}><div className="empty-icon"><FileSearch size={18} /></div><h2>No report returned</h2><p>{error || (api.state !== 'connected' ? `The backend at ${api.baseUrl} is not reachable.` : isBackendRecord ? 'The backend has no stored submission with this id.' : 'This record id is neither a local simulation nor a numeric backend submission.')}</p><p style={styles.endpointText}>GET /api/v1/submissions/{id}</p><Link href="/reports" className="small-link">Back to reports <ArrowRight size={12} /></Link></div>}
  </div>;
}

// ---------------------------------------------------------------------------
// Backend record feed (Reports page)
// ---------------------------------------------------------------------------

type BackendFeedId = 'submissions' | 'alerts' | 'dashboard';

const BACKEND_FEEDS: Array<{ id: BackendFeedId; label: string }> = [
  { id: 'submissions', label: 'GET /api/v1/faculty/submissions' },
  { id: 'alerts', label: 'GET /api/v1/faculty/alerts' },
  { id: 'dashboard', label: 'GET /api/v1/faculty/dashboard' },
];

function EmptyFeedMessage({ title, body }: { title: string; body: string }) {
  return <div className="report-empty" style={styles.reportEmpty}>
    <div className="empty-icon"><ShieldCheck size={16} /></div>
    <strong>{title}</strong>
    <p>{body}</p>
  </div>;
}

/** Render whichever documented feed the user requested — real rows only. */
function renderBackendFeed(feed: BackendFeedId, data: unknown): ReactNode {
  if (feed === 'dashboard') {
    return <><div className="backend-feed-caption">RETURNED BY API · GET /api/v1/faculty/dashboard</div>
      <pre className="mono">{JSON.stringify(data, null, 2)}</pre></>;
  }
  const page = data as ApiPage<SubmissionSummary> | ApiPage<AlertSummary> | undefined;
  const items = page?.items ?? [];
  const total = page?.pagination?.total ?? items.length;
  const caption = feed === 'submissions' ? 'SUBMISSIONS' : 'SECURITY ALERTS';
  if (!items.length) {
    return <EmptyFeedMessage title="No records stored" body={`GET ${BACKEND_FEEDS.find((item) => item.id === feed)?.label.replace('GET ', '')} returned an empty page — a real zero from the database, not a placeholder.`} />;
  }
  return <><div className="backend-feed-caption">{caption} · RETURNED BY API · {total} TOTAL</div>
    <div className="report-table">
      {feed === 'submissions'
        ? items.map((row) => {
          const submission = row as SubmissionSummary;
          return <Link key={submission.id} href={`/submissions/${submission.id}`} className="report-row" data-testid={`row-backend-submission-${submission.id}`}>
            <span><strong>#{submission.id} · {submission.language}</strong><small className="mono">{submission.problem_id ? `problem ${submission.problem_id}` : 'custom run'} · {submission.workspace_label ?? 'local-workspace'}</small></span>
            <span className="report-signal"><span className="report-dot" />{submission.status}</span>
            <span><span className="report-class">{submission.is_custom_run ? 'CUSTOM RUN' : 'JUDGED'}</span></span>
            <span className="report-time">{formatTime(submission.created_at)}</span>
            <ArrowRight size={14} />
          </Link>;
        })
        : items.map((row) => {
          const alert = row as AlertSummary;
          return <Link key={alert.id} href={`/submissions/${alert.submission_id}`} className="report-row" data-testid={`row-backend-alert-${alert.id}`}>
            <span><strong>#{alert.id} · {alert.alert_type}</strong><small className="mono">submission {alert.submission_id} · {alert.review_status}</small></span>
            <span className="report-signal"><span className="report-dot" />{alert.severity}</span>
            <span><span className="report-class">{alert.explanation.slice(0, 42)}{alert.explanation.length > 42 ? '…' : ''}</span></span>
            <span className="report-time">{formatTime(alert.created_at)}</span>
            <ArrowRight size={14} />
          </Link>;
        })}
    </div></>;
}

const styles: Record<string, CSSProperties> = {
  app: { backgroundColor: '#0d100f', color: '#e0e7e1', display: 'flex', position: 'relative' },
  sidebar: { width: 224, flexShrink: 0, height: '100dvh', position: 'fixed', inset: '0 auto 0 0', zIndex: 20, display: 'flex', flexDirection: 'column', background: 'rgba(12,15,14,.97)', borderRight: '1px solid #202723', padding: '22px 15px 12px' },
  brand: { display: 'flex', alignItems: 'center', gap: 11, padding: '1px 6px 28px' },
  brandMark: { height: 32, width: 32, border: '1px solid #386148', borderRadius: 8, display: 'grid', placeItems: 'center', color: '#77dda6', background: '#14221a' },
  brandName: { fontSize: 16, fontWeight: 700, letterSpacing: '-.6px', lineHeight: '17px' },
  brandCaption: { color: '#778079', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '1.05px', marginTop: 4 },
  navLabel: { fontFamily: 'var(--app-font-mono)', color: '#68726b', fontSize: 9, letterSpacing: '1.1px', padding: '0 10px 9px' },
  nav: { display: 'grid', gap: 3 },
  sideNote: { border: '1px solid #202923', background: 'linear-gradient(135deg, #111914, #101311)', borderRadius: 7, padding: '12px 11px', margin: '0 2px 15px' },
  sideNoteTop: { color: '#83d9a5', fontFamily: 'var(--app-font-mono)', fontSize: 9, letterSpacing: '.8px', display: 'flex', alignItems: 'center', gap: 8 },
  sideNoteP: { color: '#87918a', fontSize: 10, lineHeight: 1.55 },
  sideFooter: { display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #202723', padding: '12px 3px 2px', color: '#626b65', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.55px' },
  main: { marginLeft: 224, width: 'calc(100% - 224px)', minWidth: 0 },
  topbar: { height: 52, display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #1d2420', background: 'rgba(14,18,16,.92)', padding: '0 clamp(17px, 3vw, 42px)', position: 'sticky', top: 0, zIndex: 10, backdropFilter: 'blur(12px)' },
  topLeft: { display: 'flex', alignItems: 'center', gap: 12 },
  breadcrumb: { display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--app-font-mono)', fontSize: 9, letterSpacing: '.8px', color: '#737e76' },
  topRight: { display: 'flex', alignItems: 'center', gap: 12 },
  apiStatus: {}, separator: { height: 16, width: 1, background: '#29312b' },
  demoPill: { border: '1px solid #3b6249', background: 'rgba(98,196,132,.075)', color: '#85dca5', padding: '5px 8px', borderRadius: 3, fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.7px' },
  content: { padding: '27px clamp(17px, 3vw, 42px) 54px', maxWidth: 1550, margin: '0 auto' },
  pageHeading: { display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-end', marginBottom: 21 },
  eyebrow: { color: '#8b978e', fontFamily: 'var(--app-font-mono)', fontSize: 9, letterSpacing: '1.1px', textTransform: 'uppercase' },
  pageTitle: { margin: '7px 0 4px', color: '#e5ece6', fontSize: 'clamp(22px, 2.4vw, 29px)', fontWeight: 600, letterSpacing: '-1.05px', lineHeight: 1.13 },
  pageDetail: { margin: 0, color: '#89938b', fontSize: 12, lineHeight: 1.55 },
  headingTrailing: { display: 'flex', alignItems: 'center', paddingBottom: 3, flexShrink: 0 },
  localBadge: { fontFamily: 'var(--app-font-mono)', color: '#87d9a5', fontSize: 9, letterSpacing: '.6px', border: '1px solid #31523d', background: 'rgba(86,172,117,.075)', padding: '7px 9px', borderRadius: 3, display: 'flex', alignItems: 'center', gap: 7 },
  workspaceShell: { border: '1px solid #26302a', borderRadius: 6, overflow: 'hidden', background: 'rgba(14,18,16,.92)' },
  tabRows: { borderBottom: '1px solid #252e28', background: '#111613' },
  tabRow: { display: 'flex', alignItems: 'center', gap: 4, minHeight: 41, padding: '0 13px', borderBottom: '1px solid #202722', whiteSpace: 'nowrap' },
  tabRowLabel: { color: '#778179', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.8px', display: 'flex', gap: 7, alignItems: 'center', paddingRight: 11, borderRight: '1px solid #2c342e', marginRight: 5 },
  tabRowsUnimportant: {},
  workspaceGrid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 280px', gap: 0 },
  editorColumn: { minWidth: 0, borderRight: '1px solid #242c26' },
  panelHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 40, padding: '0 14px', borderBottom: '1px solid #222a25', background: '#121714' },
  panelHeadTitle: { display: 'flex', alignItems: 'center', gap: 8, color: '#d1d9d2', fontSize: 11 },
  editorToolbar: { display: 'flex', alignItems: 'center', gap: 10 },
  languageSelect: { color: '#a6b0a8', background: '#171e19', border: '1px solid #2c3730', borderRadius: 3, fontFamily: 'var(--app-font-mono)', fontSize: 9, padding: '4px 7px', outline: 'none' },
  fileSaved: { color: '#727d75', fontFamily: 'var(--app-font-mono)', fontSize: 9 },
  monacoFrame: { height: 373, background: '#101412' },
  editorBottom: { minHeight: 25, display: 'flex', gap: 14, alignItems: 'center', padding: '0 12px', borderTop: '1px solid #232b26', borderBottom: '1px solid #232b26', color: '#768078', fontFamily: 'var(--app-font-mono)', fontSize: 8 },
  console: { background: '#101412' },
  consoleHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: 37, padding: '0 13px', borderBottom: '1px solid #222a25' },
  consoleTabs: { display: 'flex', alignItems: 'stretch', alignSelf: 'stretch', gap: 16 },
  consoleBody: { minHeight: 82, maxHeight: 178, overflow: 'auto', padding: '12px 14px', color: '#9ea8a0', fontFamily: 'var(--app-font-mono)', fontSize: 10, lineHeight: 1.65 },
  consoleEmpty: { color: '#87918a' },
  consolePrompt: { color: '#77dda6' },
  consoleFootnote: { color: '#59635c' },
  outputPre: { margin: 0, whiteSpace: 'pre-wrap', color: '#bed0c0' },
  consoleMessageError: { color: '#e9998f' },
  stdinArea: { width: '100%', minHeight: 62, background: 'transparent', outline: 'none', resize: 'vertical', border: 0, color: '#c6d2c7', fontSize: 11 },
  consoleActions: { borderTop: '1px solid #202722', minHeight: 45, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 12px', gap: 8 },
  actionUnavailable: { display: 'flex', alignItems: 'center', gap: 6, color: '#78827b', fontFamily: 'var(--app-font-mono)', fontSize: 9 },
  rightColumn: { minWidth: 0, padding: '0 13px 12px', background: 'rgba(15,19,17,.76)' },
  rightTabs: { display: 'flex', height: 40, gap: 14, borderBottom: '1px solid #232b25', marginBottom: 15 },
  problemPanel: { minHeight: 351 },
  explorerHeading: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '3px 1px 16px' },
  sectionTitle: { color: '#e0e8e1', margin: '5px 0 0', fontSize: 16, fontWeight: 600, letterSpacing: '-.5px' },
  smallIcon: { width: 26, height: 26, display: 'grid', placeItems: 'center', color: '#87948a', border: '1px solid #2b352e', borderRadius: 4 },
  emptyState: { textAlign: 'center', padding: '24px 12px 19px', border: '1px dashed #303b33', borderRadius: 5, background: 'rgba(21,27,23,.46)' },
  emptyIcon: { width: 34, height: 34, margin: '0 auto 10px', display: 'grid', placeItems: 'center', border: '1px solid #304034', color: '#81c99b', background: '#162019', borderRadius: 5 },
  emptyStrong: {},
  textInput: { width: '100%', height: 32, background: '#111714', color: '#d1d9d2', border: '1px solid #2a352e', borderRadius: 3, padding: '0 9px', outline: 'none', fontFamily: 'var(--app-font-mono)', fontSize: 10, textAlign: 'left' },
  fieldLabel: { display: 'flex', justifyContent: 'space-between', color: '#89958c', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.65px', textAlign: 'left', margin: '17px 0 6px' },
  problemFooter: { display: 'flex', gap: 10, padding: '13px 0 0', borderTop: '1px solid #252e28', marginTop: 15, color: '#a4afa6' },
  footerIcon: { color: '#7d9a85', marginTop: 1 },
  overview: { marginTop: 12, border: '1px solid #262f29', borderRadius: 5, background: 'rgba(16,21,18,.75)', padding: '16px 16px 0' },
  overviewHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  overviewMeta: { color: '#78837b', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.6px', display: 'flex', gap: 7, alignItems: 'center' },
  signalGrid: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8 },
  signalCard: { minWidth: 0, minHeight: 104, border: '1px solid #27312a', background: '#121714', borderRadius: 4, padding: '10px 10px 9px' },
  signalCardHead: { display: 'flex', alignItems: 'center', gap: 7, color: '#99a39b', fontSize: 10 },
  signalIcon: { color: '#82c99a', display: 'flex' },
  signalValue: { color: '#717c74', fontFamily: 'var(--app-font-mono)', fontSize: 11, overflowWrap: 'anywhere', margin: '16px 0 8px' },
  signalNote: { color: '#626c64', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.35px' },
  backendDataFoot: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, borderTop: '1px solid #252e28', marginTop: 13, minHeight: 35, color: '#7d8880', fontFamily: 'var(--app-font-mono)', fontSize: 8 },
  apiNotice: { display: 'flex', alignItems: 'flex-start', gap: 11, background: 'rgba(31,34,28,.42)', border: '1px solid #35382b', borderRadius: 5, padding: '13px 14px', marginBottom: 17, color: '#c4c7b7' },
  apiNoticeCompact: { padding: '10px 9px', marginTop: 9, marginBottom: 0, background: '#121713' },
  noticeIcon: { color: '#c2ad72', marginTop: 2, flexShrink: 0 },
  noticeTitle: { color: '#d4d3c5', fontSize: 11, fontWeight: 600, marginBottom: 3 },
  noticeDescription: { color: '#92988c', fontSize: 10, lineHeight: 1.5 },
  endpointText: { color: '#727d73', fontSize: 9, marginTop: 6 },
  noticeCheck: {},
  sectionToggle: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', background: 'transparent', border: 0, padding: '21px 2px 1px', color: '#7f8a81', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.75px' },
  sectionToggleSpan: {},
  overviewTop: {},
  simGrid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(260px, .8fr)', gap: 12 },
  simMain: { border: '1px solid #28322b', borderRadius: 5, background: 'rgba(16,21,18,.93)', padding: 16, minWidth: 0 },
  simPanelHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 15 },
  simInstance: { color: '#707b73', fontSize: 8, letterSpacing: '.55px' },
  scenarioGrid: { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 6 },
  simRun: { position: 'relative', overflow: 'hidden', border: '1px solid #2c3730', borderRadius: 4, background: 'linear-gradient(110deg,#141b16,#111613)', padding: '13px 13px 11px', marginTop: 14 },
  runInfo: { paddingRight: 7 },
  runState: { display: 'flex', alignItems: 'center', gap: 6, color: '#8c9a8e', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.7px' },
  runName: { color: '#dce6dd', fontSize: 14, fontWeight: 600, marginTop: 7 },
  runDescription: { color: '#88938a', fontSize: 10, marginTop: 3 },
  runControls: { display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-end', marginTop: 12 },
  speedControl: { display: 'flex', alignItems: 'center', gap: 4, color: '#707b73', fontFamily: 'var(--app-font-mono)', fontSize: 8 },
  progressTrack: { height: 3, background: '#2a342d', borderRadius: 2, marginTop: 14, overflow: 'hidden' },
  progressBar: { width: '100%', transformOrigin: 'left', height: '100%', background: '#70d29c', transition: 'transform .25s ease' },
  progressCaption: { display: 'flex', justifyContent: 'space-between', marginTop: 6, color: '#707b73', fontFamily: 'var(--app-font-mono)', fontSize: 8 },
  telemetryGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 7, marginTop: 8 },
  telemetry: { border: '1px solid #252f29', borderRadius: 4, background: '#111613', padding: '9px 10px' },
  telemetryHead: { display: 'flex', gap: 6, alignItems: 'center', color: '#818d84', fontFamily: 'var(--app-font-mono)', fontSize: 8 },
  eventSection: { border: '1px solid #28322b', borderRadius: 5, background: 'rgba(16,21,18,.93)', padding: 15, minWidth: 0 },
  eventList: { minHeight: 250, borderTop: '1px solid #252e28', borderBottom: '1px solid #252e28', padding: '8px 0', position: 'relative' },
  eventFooter: { display: 'flex', gap: 7, alignItems: 'center', color: '#79867d', fontSize: 9, paddingTop: 11 },
  eventPlaceholder: { color: '#717c74', fontSize: 10, textAlign: 'center', padding: '28px 4px' },
  analysisHeading: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', margin: '25px 0 12px' },
  analysisWrap: {},
  signalDetails: { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 7, marginTop: 9 },
  localHistoryHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 27, marginBottom: 10 },
  recentRuns: { border: '1px solid #28322b', borderRadius: 4, overflow: 'hidden' },
  inlineEmpty: { border: '1px dashed #2b352e', borderRadius: 4, padding: 14, fontSize: 10, color: '#7e8981' },
  adminSectionHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', margin: '23px 0 10px' },
  adminLower: { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 10, marginTop: 11 },
  monitorCard: { border: '1px solid #28322b', background: '#111613', borderRadius: 5, padding: 13, minWidth: 0 },
  cardHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 9, marginBottom: 12 },
  monitorEmpty: { minHeight: 153, borderTop: '1px solid #252e28', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '14px 16px' },
  reportToolbar: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 },
  searchBox: { display: 'flex', alignItems: 'center', gap: 8, height: 34, border: '1px solid #2c362f', borderRadius: 4, padding: '0 10px', color: '#78837b', background: '#111613', flex: 1, maxWidth: 390 },
  filterSelect: { color: '#b6c0b8', background: '#111613', border: '1px solid #2b352e', borderRadius: 4, height: 34, padding: '0 10px', fontSize: 10, outline: 'none' },
  reportTable: { border: '1px solid #28322b', borderRadius: 5, background: 'rgba(16,21,18,.9)', overflow: 'hidden' },
  reportNote: { display: 'flex', alignItems: 'center', gap: 8, color: '#7e8981', fontSize: 10, marginTop: 12 },
  detailId: { display: 'flex', gap: 10, alignItems: 'center', padding: '9px 12px', border: '1px solid #29332d', borderRadius: 4, marginBottom: 12, color: '#7f8a82', fontSize: 9 },
  detailSummary: { display: 'flex', justifyContent: 'space-between', gap: 20, border: '1px solid #2b352e', background: '#111714', borderRadius: 5, padding: 17, marginBottom: 10 },
  detailClassification: { display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 5, minWidth: 170 },
  detailColumns: { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 10, marginTop: 10 },
  detailCard: { background: '#111613', border: '1px solid #29332d', borderRadius: 5, padding: 13 },
  detailDisclaimer: { display: 'flex', alignItems: 'center', gap: 8, color: '#c2ad72', background: 'rgba(45,39,22,.28)', border: '1px solid #47402a', borderRadius: 4, padding: '10px 12px', fontSize: 10, marginTop: 12 },
  noRecord: { minHeight: 300, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', border: '1px dashed #303a33', borderRadius: 5, background: '#101512' },
  loadingState: { minHeight: 220, display: 'flex', alignItems: 'center', flexDirection: 'column', justifyContent: 'center', gap: 13, color: '#96a198', fontSize: 11 },
  jsonResponse: { border: '1px solid #29332d', background: '#111613', borderRadius: 5, padding: 14 },
  // Execution result (console body) — same tokens as the rest of the workspace.
  resultWrap: { display: 'grid', gap: 9, fontFamily: 'var(--app-font-mono)', fontSize: 10, color: '#b7c4b9', minWidth: 0 },
  resultHead: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' },
  resultMeta: { color: '#7e8981', fontFamily: 'var(--app-font-mono)', fontSize: 9 },
  resultMetrics: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(78px,1fr))', gap: 6, marginTop: 8 },
  resultMetric: { display: 'grid', gap: 3, border: '1px solid #26302a', borderRadius: 4, padding: '6px 8px', background: '#111613' },
  resultMetricLabel: { color: '#6f7b73', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.6px' },
  resultMetricValue: { color: '#c8d4c9', fontFamily: 'var(--app-font-mono)', fontSize: 10 },
  resultLine: { display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' },
  resultSection: { display: 'grid', gap: 5 },
  resultTests: { display: 'grid', gap: 3, marginTop: 5 },
  resultError: { display: 'flex', gap: 6, alignItems: 'center', color: '#d79385', background: 'rgba(60,30,26,.3)', border: '1px solid #4a332e', borderRadius: 4, padding: '6px 8px', fontSize: 10, fontFamily: 'var(--app-font-mono)' },
  resultLimits: { color: '#7f8b83', fontSize: 8, lineHeight: '15px', overflowWrap: 'anywhere', borderTop: '1px solid #232c26', paddingTop: 7 },
  // Problem explorer (backend catalog list).
  problemList: { display: 'grid', gap: 6, marginTop: 10, maxHeight: 240, overflowY: 'auto' },
  selectedProblem: { color: '#8fa795', fontSize: 8, marginTop: 8, letterSpacing: '.5px', overflowWrap: 'anywhere' },
  contractHint: { display: 'grid', gap: 6, justifyItems: 'start', color: '#7f8a82', fontSize: 9 },
  // Capability table (admin / non-compact ApiNotice).
  capabilityList: { display: 'grid', gap: 4, marginTop: 4 },
  capabilityHead: { color: '#66726a', fontFamily: 'var(--app-font-mono)', fontSize: 8, letterSpacing: '.6px', marginTop: 8 },
  capabilityRow: { display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline', padding: '4px 0', borderBottom: '1px solid #232c26' },
  capabilityName: { color: '#a8b3aa', fontSize: 9 },
  capabilityWarning: { color: '#b7a97a', fontSize: 9, marginTop: 6, lineHeight: 1.5 },
  testRow: { display: 'grid', gridTemplateColumns: '46px minmax(90px,1fr) 70px 96px 90px', gap: 10, alignItems: 'center', minHeight: 30, borderTop: '1px solid #252e28', fontSize: 9, color: '#8b968d' },
  // Problem Statement Manager containers (ProblemForm / ProblemPreview / TestCaseManager).
  form: { display: 'grid', gap: 13 },
  preview: { display: 'grid', gap: 13 },
  sampleTests: { display: 'grid', gap: 9 },
  testCaseManager: { display: 'grid', gap: 8 },
  reportEmpty: { minHeight: 130, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 18, gap: 5 },
  // Faculty dashboard / submissions / comparison — same design tokens.
  dashboardPanel: { border: '1px solid #28322b', background: '#111613', borderRadius: 5, padding: 13, minWidth: 0 },
  kpiStrip: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 8 },
  kpiMini: { display: 'grid', gap: 4, border: '1px solid #26302a', borderRadius: 4, padding: '9px 10px', background: '#111613' },
  quickActions: { display: 'flex', flexWrap: 'wrap', gap: 8, margin: '4px 0 14px' },
  paginationBar: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, padding: '10px 0 4px', color: '#7e8981' },
  comparePicker: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: 6, maxHeight: 220, overflow: 'auto' },
  comparisonGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 10 },
  comparisonRowFallback: { padding: '7px 0', borderBottom: '1px solid #232c26', fontSize: 9, color: '#8b968d' },
  capabilityTable: { border: '1px solid #29332d', borderRadius: 4, overflow: 'hidden' },
};

export default App;
