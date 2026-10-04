/**
 * REX Domain & API TypeScript Interfaces (REX-037 through REX-041).
 *
 * Types for research investigations, hypotheses, experiments, executions,
 * empirical metrics, claims, evidence lineage DAG, verification reports, and system stats.
 */

export type ResearchStatus =
  | "INITIALIZE"
  | "UNDERSTAND"
  | "LITERATURE"
  | "HYPOTHESES"
  | "DESIGN"
  | "IMPLEMENT"
  | "EXECUTE"
  | "VERIFY"
  | "ANALYZE"
  | "CRITIQUE"
  | "DECIDE"
  | "REFINE"
  | "REPLICATE"
  | "PIVOT"
  | "STOP"
  | "PAUSED"
  | "COMPLETE"
  | "FAILED";

export interface ResearchRunStats {
  hypotheses_count: number;
  experiments_count: number;
  verified_results_count: number;
  pending_count: number;
  claims_count: number;
  verified_claims_count: number;
  compute_cost_estimate: number;
}

export interface ResearchRun {
  id: string;
  title: string;
  research_question: string;
  status: ResearchStatus;
  created_at: string;
  updated_at: string;
  configuration: Record<string, any>;
  budget: Record<string, any>;
  stats: ResearchRunStats;
  current_action?: string | null;
  current_action_reason?: string | null;
  current_action_progress?: number | null;
  next_action?: string | null;
}

export interface Hypothesis {
  id: string;
  research_run_id: string;
  statement: string;
  rationale: string;
  expected_direction: "increase" | "decrease" | "no_change" | "non_zero" | "other";
  falsification_condition: string;
  status: "proposed" | "active" | "testing" | "validated" | "supported" | "falsified" | "rejected" | "inconclusive";
  created_at: string;
}

export interface Experiment {
  id: string;
  research_run_id: string;
  hypothesis_id?: string | null;
  objective: string;
  specification: {
    name?: string;
    description?: string;
    method?: string;
    variables?: Record<string, any>;
    controls?: Record<string, any>;
    baseline?: Record<string, any>;
    datasets?: Array<{ name: string; version?: string; split?: string }>;
    metrics?: Array<{ name: string; direction?: string; target_value?: number; description?: string }>;
    parameters?: Record<string, any>;
    seeds?: number[];
    repetitions?: number;
    analysis_methods?: string[];
    success_criteria?: string;
    falsification_criteria?: string;
  };
  status: "designed" | "pending" | "running" | "analyzing" | "completed" | "failed" | "cancelled";
  created_at: string;
  parent_experiment_id?: string | null;
  execution_count: number;
  latest_status?: string | null;
  latest_execution_id?: string | null;
  latest_metrics: Record<string, number | null>;
}

export interface Execution {
  id: string;
  experiment_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "timeout";
  started_at?: string | null;
  finished_at?: string | null;
  command: string;
  git_commit: string;
  code_hash: string;
  dataset_hash: string;
  configuration_hash: string;
  seed?: number | null;
  environment: Record<string, any>;
  resource_usage: Record<string, any>;
  exit_code?: number | null;
  stdout_artifact_id?: string | null;
  stderr_artifact_id?: string | null;
  duration_seconds?: number | null;
}

export interface Result {
  id: string;
  execution_id: string;
  metric_name: string;
  metric_value: number | null;
  metric_unit: string;
  result_data: Record<string, any>;
  created_at: string;
}

export interface MetricSeriesPoint {
  step: number;
  value: number;
  series_name: string;
  timestamp?: string | null;
}

export interface ExperimentComparison {
  experiment_ids: string[];
  experiments: Experiment[];
  metrics_summary: Array<{
    metric_name: string;
    values_by_experiment: Record<string, number | null>;
    unit?: string;
    direction?: string;
  }>;
  series_data: MetricSeriesPoint[];
}

export interface Claim {
  id: string;
  research_run_id: string;
  text: string;
  statement: string;
  claim_type: "observation" | "comparison" | "causal" | "methodological" | "conclusion" | "empirical";
  status: "draft" | "proposed" | "supported" | "unsupported" | "verified" | "rejected" | "tampered" | "disproven" | "inconclusive";
  confidence?: number | null;
  created_by: string;
  metadata: Record<string, any>;
  created_at: string;
  evidence_links_count: number;
}

export interface LineageNode {
  id: string;
  type: "claim" | "analysis" | "result" | "execution" | "experiment" | "code" | "dataset" | "configuration" | "artifact";
  label: string;
  sublabel: string;
  status: "verified" | "unverified" | "unsupported" | "missing" | "tampered";
  hash?: string | null;
  details: Record<string, any>;
}

export interface LineageEdge {
  source_id: string;
  target_id: string;
  relation: string;
  is_valid: boolean;
}

export interface ClaimLineage {
  claim_id: string;
  statement: string;
  status: string;
  is_complete: boolean;
  gaps: string[];
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface VerificationCheckItem {
  name: string;
  status: "pass" | "fail" | "warning";
  message: string;
  details: Record<string, any>;
}

export interface VerificationReport {
  research_run_id: string;
  status: "pass" | "warning" | "fail";
  is_passed: boolean;
  checks: VerificationCheckItem[];
  claims_verified: any[];
  artifacts_verified: any[];
  analyses_recomputed: any[];
  cross_run_violations: string[];
  errors: string[];
  warnings: string[];
  started_at: string;
  completed_at: string;
}

export interface ResearchReport {
  research_run_id: string;
  title: string;
  generated_at: string;
  is_fully_grounded: boolean;
  executive_summary: string;
  hypotheses: Array<Record<string, any>>;
  experiments: Array<Record<string, any>>;
  metrics: Array<Record<string, any>>;
  analyses: Array<Record<string, any>>;
  critiques: Array<Record<string, any>>;
  limitations: string[];
  conclusions: Array<Record<string, any>>;
  unsupported_claims: Array<Record<string, any>>;
  failed_experiments: Array<Record<string, any>>;
  markdown: string;
}

export interface Artifact {
  id: string;
  research_run_id: string;
  execution_id?: string | null;
  artifact_type: string;
  path: string;
  content_hash: string;
  size_bytes: number;
  metadata: Record<string, any>;
  created_at: string;
}

export interface SystemStatus {
  status: string;
  active_runs_count: number;
  total_runs_count: number;
  experiments_count: number;
  claims_count: number;
  reports_count: number;
  app_name: string;
  version: string;
  environment: string;
  docker_enabled: boolean;
  database_url_masked: string;
}

export interface SystemSettings {
  app: Record<string, any>;
  persistence: Record<string, any>;
  docker: Record<string, any>;
  budgets: Record<string, any>;
  literature: Record<string, any>;
  llm: Record<string, any>;
}

export interface AuditEvent {
  event_id: string;
  timestamp: string;
  event_type: string;
  actor: string;
  research_run_id: string;
  experiment_id?: string | null;
  execution_id?: string | null;
  payload: Record<string, any>;
}

// ----------------------------------------------------------------------------
// Quality & Evaluation Types (Batch 9 / Epic 11: REX-042 - REX-045)
// ----------------------------------------------------------------------------

export interface SuiteInfo {
  id: string;
  name: string;
  description: string;
  ticket: string;
  cases_count: number;
}

export interface EvaluationCase {
  id: string;
  suite: string;
  case_name: string;
  status: "passed" | "failed" | "skipped" | "error";
  duration_ms: number;
  assertions_passed: number;
  assertions_failed: number;
  failure_reason?: string | null;
  failure_classification?: string | null;
  details?: Record<string, any>;
}

export interface EvaluationComparison {
  id: string;
  evaluation_run_id?: string;
  comparison_name: string;
  baseline_metrics: Record<string, number>;
  rex_metrics: Record<string, number>;
  delta_metrics: Record<string, number>;
  statistical_summary?: {
    summary?: string;
    cost?: Record<string, any>;
  };
  created_at?: string | null;
}

export interface QualityScorecard {
  overall_score: number;
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  gate_compliance: Record<string, string>;
  domain_scores: Record<string, number>;
  timestamp: string;
}

export interface GateComplianceResponse {
  gates: Record<string, string>;
  overall_compliance_pct: number;
  total_gates: number;
  passed_gates: number;
  verified_at: string;
}

export interface EvaluationRun {
  id: string;
  suite_name: string;
  status: "pending" | "running" | "completed" | "passed" | "failed" | "error";
  started_at: string | null;
  completed_at: string | null;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  score: number;
  scorecard?: QualityScorecard | null;
  summary?: Record<string, any> | null;
  cases?: EvaluationCase[];
  comparisons?: EvaluationComparison[];
}

