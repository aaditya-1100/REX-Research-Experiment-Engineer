/**
 * REX Backend HTTP API Client (REX-037).
 *
 * Provides type-safe access to all backend resources with error normalization,
 * query formatting, and Server-Sent Event subscription utilities.
 */

import {
  Artifact,
  AuditEvent,
  Claim,
  ClaimLineage,
  EvaluationCase,
  EvaluationComparison,
  EvaluationRun,
  Execution,
  Experiment,
  ExperimentComparison,
  GateComplianceResponse,
  Hypothesis,
  QualityScorecard,
  ResearchReport,
  ResearchRun,
  SuiteInfo,
  SystemSettings,
  SystemStatus,
  VerificationReport,
} from "../types";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorJson = await response.json();
      if (errorJson.detail) {
        errorDetail = typeof errorJson.detail === "string" ? errorJson.detail : JSON.stringify(errorJson.detail);
      }
    } catch {
      // fallback to status text
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  // System
  getHealth: () => request<{ status: string; app: string; version: string }>("/system/health"),
  getSystemStatus: () => request<SystemStatus>("/system/status"),
  getSettings: () => request<SystemSettings>("/settings"),

  // Research Runs
  listResearchRuns: (status?: string) => {
    const q = status ? `?status=${encodeURIComponent(status)}` : "";
    return request<ResearchRun[]>(`/research${q}`);
  },
  getResearchRun: (runId: string) => request<ResearchRun>(`/research/${runId}`),
  createResearchRun: (payload: { research_question: string; title?: string; configuration?: any; budget?: any }) =>
    request<ResearchRun>("/research", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startResearchRun: (runId: string, reason?: string) =>
    request<ResearchRun>(`/research/${runId}/start`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  pauseResearchRun: (runId: string, reason?: string) =>
    request<ResearchRun>(`/research/${runId}/pause`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resumeResearchRun: (runId: string, reason?: string) =>
    request<ResearchRun>(`/research/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  getRunHypotheses: (runId: string) => request<Hypothesis[]>(`/research/${runId}/hypotheses`),
  getRunExperiments: (runId: string) => request<Experiment[]>(`/research/${runId}/experiments`),
  getRunClaims: (runId: string) => request<Claim[]>(`/research/${runId}/claims`),
  getRunEvents: (runId: string, limit = 100) => request<AuditEvent[]>(`/research/${runId}/events?limit=${limit}`),
  verifyResearchRun: (runId: string) =>
    request<VerificationReport>(`/research/${runId}/verify`, { method: "POST" }),
  getResearchReport: (runId: string) => request<ResearchReport>(`/research/${runId}/report`),

  // Experiments
  listExperiments: (runId?: string, status?: string) => {
    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    if (status) params.set("status", status);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return request<Experiment[]>(`/experiments${qs}`);
  },
  getExperiment: (experimentId: string) => request<Experiment>(`/experiments/${experimentId}`),
  getExperimentRuns: (experimentId: string) => request<Execution[]>(`/experiments/${experimentId}/runs`),
  getExperimentReproducibility: (experimentId: string) =>
    request<any>(`/experiments/${experimentId}/reproducibility`),
  compareExperiments: (ids: string[]) =>
    request<ExperimentComparison>(`/experiments/compare?ids=${encodeURIComponent(ids.join(","))}`),

  // Executions
  getExecution: (executionId: string) => request<Execution>(`/runs/${executionId}`),
  getExecutionResults: (executionId: string) => request<any[]>(`/runs/${executionId}/results`),
  getExecutionArtifacts: (executionId: string) => request<Artifact[]>(`/runs/${executionId}/artifacts`),
  getExecutionLogs: (executionId: string) =>
    request<{ execution_id: string; stdout: string; stderr: string; exit_code: string }>(`/runs/${executionId}/logs`),

  // Evidence
  listClaims: (runId?: string, status?: string) => {
    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    if (status) params.set("status", status);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return request<Claim[]>(`/evidence/claims${qs}`);
  },
  getClaim: (claimId: string) => request<Claim>(`/evidence/claims/${claimId}`),
  getClaimLineage: (claimId: string) => request<ClaimLineage>(`/evidence/lineage/${claimId}`),

  // Reports
  listReports: () => request<any[]>("/reports"),
  getReportByRunId: (runId: string) => request<ResearchReport>(`/reports/${runId}`),

  // Artifacts
  listArtifacts: (runId?: string, type?: string) => {
    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    if (type) params.set("type", type);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return request<Artifact[]>(`/artifacts${qs}`);
  },
  getArtifact: (artifactId: string) => request<Artifact>(`/artifacts/${artifactId}`),
  verifyArtifact: (artifactId: string) => request<any>(`/artifacts/${artifactId}/verify`),
  getArtifactContentUrl: (artifactId: string) => `${BASE_URL}/artifacts/${artifactId}/content`,

  // Evaluation & Quality Center (REX Epic 11)
  listEvaluationSuites: () => request<SuiteInfo[]>("/evaluation/suites"),
  listEvaluationRuns: (limit = 50, offset = 0) =>
    request<EvaluationRun[]>(`/evaluation/runs?limit=${limit}&offset=${offset}`),
  getEvaluationRun: (runId: string) => request<EvaluationRun>(`/evaluation/runs/${runId}`),
  triggerEvaluation: (suite: string) =>
    request<EvaluationRun>("/evaluation/runs", {
      method: "POST",
      body: JSON.stringify({ suite }),
    }),
  getQualityScorecard: () => request<QualityScorecard>("/evaluation/scorecard"),
  getGateCompliance: () => request<GateComplianceResponse>("/evaluation/gates"),
  getEvaluationComparisons: () => request<EvaluationComparison[]>("/evaluation/comparisons"),

  // SSE Event Stream
  subscribeRunEvents: (runId: string, onEvent: (event: AuditEvent) => void, onError?: (err: any) => void) => {
    const eventSource = new EventSource(`${BASE_URL}/research/${runId}/events/stream`);
    eventSource.addEventListener("research_event", (e) => {
      try {
        const parsed = JSON.parse(e.data);
        onEvent(parsed);
      } catch (err) {
        console.error("Failed to parse event", err);
      }
    });
    eventSource.onerror = (err) => {
      if (onError) onError(err);
    };
    return () => {
      eventSource.close();
    };
  },
};
