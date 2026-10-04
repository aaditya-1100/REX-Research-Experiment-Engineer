import React, { useState, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  FlaskConical,
  Layers,
  FileCheck,
  FileText,
  Activity,
  Play,
  Pause,
  CheckCircle,
  AlertTriangle,
  Clock,
  ArrowRight,
  ShieldCheck,
  Download,
  ExternalLink,
} from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Tabs, TabItem } from "../components/ui/Tabs";
import { Table, Column } from "../components/ui/Table";
import { Drawer } from "../components/ui/Drawer";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { api } from "../api/client";
import {
  AuditEvent,
  Claim,
  Experiment,
  Hypothesis,
  ResearchReport,
  ResearchRun,
} from "../types";

export const ResearchDetailPage: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const [run, setRun] = useState<ResearchRun | null>(null);
  const [activeTab, setActiveTab] = useState("overview");
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Drawer state for contextual inspector
  const [selectedDrawerItem, setSelectedDrawerItem] = useState<{
    type: "hypothesis" | "experiment" | "claim";
    data: any;
  } | null>(null);

  // Load run details
  useEffect(() => {
    if (!runId) return;

    async function loadRunData() {
      setIsLoading(true);
      try {
        const [r, hyps, exps, clms, evts] = await Promise.all([
          api.getResearchRun(runId!),
          api.getRunHypotheses(runId!),
          api.getRunExperiments(runId!),
          api.getRunClaims(runId!),
          api.getRunEvents(runId!, 50),
        ]);
        setRun(r);
        setHypotheses(hyps);
        setExperiments(exps);
        setClaims(clms);
        setEvents(evts);

        // Try load report
        try {
          const rep = await api.getResearchReport(runId!);
          setReport(rep);
        } catch {
          // report not yet generated
        }
      } catch (err) {
        console.error("Failed to load research run details", err);
      } finally {
        setIsLoading(false);
      }
    }

    loadRunData();

    // Subscribe to SSE event stream
    const unsubscribe = api.subscribeRunEvents(runId, (newEvent) => {
      setEvents((prev) => [newEvent, ...prev]);
    });

    return () => unsubscribe();
  }, [runId]);

  const handleStart = async () => {
    if (!run) return;
    try {
      await api.startResearchRun(run.id);
      const [updated, evts] = await Promise.all([
        api.getResearchRun(run.id),
        api.getRunEvents(run.id, 50),
      ]);
      setRun(updated);
      setEvents(evts);
    } catch (err) {
      console.error("Failed to start run", err);
    }
  };

  const handlePause = async () => {
    if (!run) return;
    try {
      await api.pauseResearchRun(run.id);
      const [updated, evts] = await Promise.all([
        api.getResearchRun(run.id),
        api.getRunEvents(run.id, 50),
      ]);
      setRun(updated);
      setEvents(evts);
    } catch (err) {
      console.error("Failed to pause run", err);
    }
  };

  const handleResume = async () => {
    if (!run) return;
    try {
      await api.resumeResearchRun(run.id);
      const [updated, evts] = await Promise.all([
        api.getResearchRun(run.id),
        api.getRunEvents(run.id, 50),
      ]);
      setRun(updated);
      setEvents(evts);
    } catch (err) {
      console.error("Failed to resume run", err);
    }
  };

  if (isLoading || !run) {
    return (
      <div className="py-20 text-center space-y-2">
        <div className="w-6 h-6 border-2 border-rex-primary border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-xs text-rex-muted">Loading research investigation workspace...</p>
      </div>
    );
  }

  const tabs: TabItem[] = [
    { id: "overview", label: "Overview", icon: <FlaskConical className="w-3.5 h-3.5" /> },
    { id: "experiments", label: "Experiments", count: experiments.length, icon: <Layers className="w-3.5 h-3.5" /> },
    { id: "evidence", label: "Evidence", count: claims.length, icon: <FileCheck className="w-3.5 h-3.5" /> },
    { id: "report", label: "Report", icon: <FileText className="w-3.5 h-3.5" /> },
    { id: "activity", label: "Activity", count: events.length, icon: <Activity className="w-3.5 h-3.5" /> },
  ];

  const experimentColumns: Column<Experiment>[] = [
    {
      key: "id",
      header: "Experiment ID",
      width: "120px",
      render: (e) => <span className="font-mono font-semibold text-rex-primary">{e.id}</span>,
    },
    {
      key: "objective",
      header: "Objective & Specification",
      render: (e) => (
        <div>
          <div className="font-semibold text-rex-primary">{e.objective}</div>
          <div className="text-[11px] text-rex-muted mt-0.5 font-mono">
            Method: {e.specification.method || "Standard"} • Reps: {e.specification.repetitions || 1}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "110px",
      render: (e) => <Badge variant={e.status}>{e.status}</Badge>,
    },
    {
      key: "metrics",
      header: "Key Output Metrics",
      width: "180px",
      render: (e) => {
        const metricKeys = Object.keys(e.latest_metrics || {});
        if (metricKeys.length === 0) return <span className="text-rex-muted font-mono text-[11px]">No metrics</span>;
        return (
          <div className="space-y-0.5 font-mono text-[11px]">
            {metricKeys.slice(0, 2).map((k) => (
              <div key={k} className="flex justify-between gap-2">
                <span className="text-rex-secondary capitalize">{k}:</span>
                <span className="font-semibold text-rex-primary">{e.latest_metrics[k]}</span>
              </div>
            ))}
          </div>
        );
      },
    },
    {
      key: "actions",
      header: "Action",
      width: "90px",
      align: "right",
      render: (e) => (
        <Button
          size="sm"
          variant="outline"
          onClick={(evt) => {
            evt.stopPropagation();
            navigate(`/experiments/${e.id}`);
          }}
        >
          Details
        </Button>
      ),
    },
  ];

  const claimColumns: Column<Claim>[] = [
    {
      key: "id",
      header: "Claim ID",
      width: "110px",
      render: (c) => <span className="font-mono font-semibold text-rex-primary">{c.id}</span>,
    },
    {
      key: "statement",
      header: "Scientific Claim Statement",
      render: (c) => (
        <div>
          <div className="text-xs text-rex-primary font-medium">{c.statement}</div>
          <div className="text-[10px] text-rex-muted font-mono mt-0.5">
            Type: {c.claim_type} • Confidence: {c.confidence ? `${Math.round(c.confidence * 100)}%` : "N/A"}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      header: "Verification",
      width: "120px",
      render: (c) => <Badge variant={c.status}>{c.status}</Badge>,
    },
    {
      key: "lineage",
      header: "Evidence Lineage",
      width: "120px",
      align: "right",
      render: (c) => (
        <Link
          to={`/evidence/lineage/${c.id}`}
          onClick={(e) => e.stopPropagation()}
          className="text-xs text-rex-info hover:underline flex items-center justify-end gap-1 font-medium"
        >
          Trace DAG <ArrowRight className="w-3 h-3" />
        </Link>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header & Breadcrumb */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Research", to: "/research" },
            { label: "Runs", to: "/research" },
            { label: run.id, isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-rex-primary">
                {run.title || run.id}
              </h1>
              <Badge variant={run.status} size="md">
                {run.status}
              </Badge>
            </div>
            <p className="text-xs text-rex-secondary mt-1 font-medium leading-relaxed max-w-3xl">
              {run.research_question}
            </p>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              variant="outline"
              leftIcon={<ShieldCheck className="w-3.5 h-3.5 text-rex-success" />}
              onClick={() => navigate("/evidence")}
            >
              Verify Run
            </Button>
            {run.status === "INITIALIZE" && (
              <Button
                variant="primary"
                leftIcon={<Play className="w-3.5 h-3.5" />}
                onClick={handleStart}
              >
                Start Run
              </Button>
            )}
            {run.status === "PAUSED" && (
              <Button
                variant="primary"
                leftIcon={<Play className="w-3.5 h-3.5" />}
                onClick={handleResume}
              >
                Resume Run
              </Button>
            )}
            {run.status !== "INITIALIZE" && run.status !== "PAUSED" && !["FAILED", "ABORTED", "DECIDE"].includes(run.status) && (
              <Button
                variant="secondary"
                leftIcon={<Pause className="w-3.5 h-3.5" />}
                onClick={handlePause}
              >
                Pause Run
              </Button>
            )}
          </div>
        </div>

        {/* Stats Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 pt-2">
          <div className="p-2.5 bg-rex-surface border border-rex-border rounded-lg">
            <span className="text-[10px] text-rex-muted uppercase font-mono">Hypotheses</span>
            <div className="text-base font-bold font-mono text-rex-primary mt-0.5">
              {run.stats.hypotheses_count}
            </div>
          </div>
          <div className="p-2.5 bg-rex-surface border border-rex-border rounded-lg">
            <span className="text-[10px] text-rex-muted uppercase font-mono">Experiments</span>
            <div className="text-base font-bold font-mono text-rex-primary mt-0.5">
              {run.stats.experiments_count}
            </div>
          </div>
          <div className="p-2.5 bg-rex-surface border border-rex-border rounded-lg">
            <span className="text-[10px] text-rex-muted uppercase font-mono">Verified Results</span>
            <div className="text-base font-bold font-mono text-rex-success mt-0.5">
              {run.stats.verified_results_count}
            </div>
          </div>
          <div className="p-2.5 bg-rex-surface border border-rex-border rounded-lg">
            <span className="text-[10px] text-rex-muted uppercase font-mono">Pending Tasks</span>
            <div className="text-base font-bold font-mono text-rex-info mt-0.5">
              {run.stats.pending_count}
            </div>
          </div>
          <div className="p-2.5 bg-rex-surface border border-rex-border rounded-lg">
            <span className="text-[10px] text-rex-muted uppercase font-mono">Cost Estimate</span>
            <div className="text-base font-bold font-mono text-rex-primary mt-0.5">
              ${run.stats.compute_cost_estimate.toFixed(2)}
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {/* Tab 1: Overview */}
      {activeTab === "overview" && (
        <div className="space-y-6">
          {/* Autonomous Current Action Card */}
          <Card
            title={
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase font-mono text-rex-accent font-semibold tracking-wider">
                  Autonomous Research Loop
                </span>
                <Badge variant={run.status}>{run.status}</Badge>
              </div>
            }
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-xs font-semibold text-rex-primary">
                    {run.current_action || "Investigating problem space"}
                  </h3>
                  <p className="text-[11px] text-rex-secondary mt-0.5">
                    {run.current_action_reason || "Iteration requires control validation against baseline."}
                  </p>
                </div>
                <span className="text-xs font-mono font-semibold text-rex-primary">
                  {Math.round((run.current_action_progress || 0.6) * 100)}%
                </span>
              </div>

              {/* Progress Bar */}
              <div className="w-full bg-rex-border/60 rounded-full h-1.5 overflow-hidden">
                <div
                  className="bg-rex-info h-1.5 rounded-full transition-all duration-500"
                  style={{ width: `${Math.round((run.current_action_progress || 0.6) * 100)}%` }}
                />
              </div>

              <div className="flex items-center justify-between pt-1 text-xs">
                <div className="text-[11px] text-rex-muted font-mono">
                  Next: <span className="text-rex-secondary">{run.next_action || "Execute scheduled experiment"}</span>
                </div>
                <div className="flex items-center gap-2">
                  {run.status === "PAUSED" ? (
                    <Button
                      size="sm"
                      variant="primary"
                      leftIcon={<Play className="w-3 h-3" />}
                      onClick={handleResume}
                    >
                      Resume
                    </Button>
                  ) : run.status === "INITIALIZE" ? (
                    <Button
                      size="sm"
                      variant="primary"
                      leftIcon={<Play className="w-3 h-3" />}
                      onClick={handleStart}
                    >
                      Start
                    </Button>
                  ) : !["FAILED", "ABORTED", "DECIDE"].includes(run.status) ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      leftIcon={<Pause className="w-3 h-3" />}
                      onClick={handlePause}
                    >
                      Pause
                    </Button>
                  ) : null}
                </div>
              </div>
            </div>
          </Card>

          {/* Hypotheses List */}
          <Card
            title="Formulated Scientific Hypotheses"
            subtitle="Testable propositions governing the experimental search space"
          >
            {hypotheses.length === 0 ? (
              <div className="text-xs text-rex-muted py-6 text-center italic">
                No hypotheses formulated yet. The Investigator agent will generate hypotheses during the HYPOTHESES stage.
              </div>
            ) : (
              <div className="space-y-3">
                {hypotheses.map((hyp) => (
                  <div
                    key={hyp.id}
                    onClick={() => setSelectedDrawerItem({ type: "hypothesis", data: hyp })}
                    className="p-3 bg-rex-surface border border-rex-border rounded-lg hover:border-rex-border/80 hover:bg-rex-elevated/40 cursor-pointer transition-colors space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-semibold text-rex-primary">
                        {hyp.id}
                      </span>
                      <Badge variant={hyp.status}>{hyp.status}</Badge>
                    </div>
                    <p className="text-xs text-rex-primary font-medium">{hyp.statement}</p>
                    <div className="text-[11px] text-rex-secondary">
                      <span className="text-rex-muted">Falsification condition:</span>{" "}
                      <span className="font-mono text-rex-secondary">{hyp.falsification_condition}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab 2: Experiments */}
      {activeTab === "experiments" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-rex-primary uppercase tracking-wider font-mono">
              Designed Experiments ({experiments.length})
            </h3>
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate("/experiments/compare")}
            >
              Compare Experiments
            </Button>
          </div>
          <Table
            columns={experimentColumns}
            data={experiments}
            keyExtractor={(e) => e.id}
            onRowClick={(e) => navigate(`/experiments/${e.id}`)}
            emptyMessage="No experiments designed under this research run yet."
          />
        </div>
      )}

      {/* Tab 3: Evidence & Claims */}
      {activeTab === "evidence" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-rex-primary uppercase tracking-wider font-mono">
              Empirical Claims & Evidence Links ({claims.length})
            </h3>
            <Button
              size="sm"
              variant="primary"
              leftIcon={<ShieldCheck className="w-3.5 h-3.5" />}
              onClick={() => navigate("/evidence")}
            >
              Verify All Claims
            </Button>
          </div>
          <Table
            columns={claimColumns}
            data={claims}
            keyExtractor={(c) => c.id}
            onRowClick={(c) => setSelectedDrawerItem({ type: "claim", data: c })}
            emptyMessage="No scientific claims asserted yet."
          />
        </div>
      )}

      {/* Tab 4: Report */}
      {activeTab === "report" && (
        <div className="space-y-4">
          {report ? (
            <Card
              title={
                <div className="flex items-center justify-between w-full">
                  <span>{report.title}</span>
                  <Badge variant={report.is_fully_grounded ? "verified" : "warning"}>
                    {report.is_fully_grounded ? "✓ Fully Grounded" : "⚠️ Unsupported Claims Present"}
                  </Badge>
                </div>
              }
              subtitle={`Generated ${new Date(report.generated_at).toLocaleString()}`}
              action={
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<Download className="w-3 h-3" />}
                  onClick={() => {
                    const blob = new Blob([report.markdown], { type: "text/markdown" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `report_${run.id}.md`;
                    a.click();
                  }}
                >
                  Download MD
                </Button>
              }
            >
              <div className="prose prose-sm dark:prose-invert max-w-none text-xs text-rex-secondary space-y-4">
                <div className="p-3 bg-rex-elevated/40 border border-rex-border rounded-md font-medium text-rex-primary">
                  {report.executive_summary}
                </div>
                <div className="whitespace-pre-wrap font-mono text-xs p-4 bg-rex-elevated/20 border border-rex-border/60 rounded-lg overflow-x-auto max-h-[60vh]">
                  {report.markdown}
                </div>
              </div>
            </Card>
          ) : (
            <Card>
              <div className="text-center py-10 space-y-3">
                <FileText className="w-8 h-8 text-rex-muted mx-auto" />
                <h3 className="text-sm font-semibold text-rex-primary">Report Not Yet Generated</h3>
                <p className="text-xs text-rex-secondary max-w-sm mx-auto">
                  Generate an auditable, evidence-grounded research report synthesizing all experiments, metrics, and conclusions.
                </p>
                <Button
                  variant="primary"
                  onClick={async () => {
                    const rep = await api.getResearchReport(run.id);
                    setReport(rep);
                  }}
                >
                  Generate Report Now
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* Tab 5: Real-time Activity Timeline */}
      {activeTab === "activity" && (
        <Card title="Live Audit Event Timeline" subtitle="Server-Sent Events streaming in real-time">
          <div className="space-y-3">
            {events.length === 0 ? (
              <div className="text-xs text-rex-muted py-8 text-center italic">No audit events recorded yet.</div>
            ) : (
              events.map((ev) => (
                <div
                  key={ev.event_id}
                  className="flex items-start gap-3 p-3 bg-rex-surface border border-rex-border rounded-lg"
                >
                  <div className="p-1.5 rounded bg-rex-elevated border border-rex-border flex-shrink-0 mt-0.5">
                    <Activity className="w-3.5 h-3.5 text-rex-info" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-rex-primary font-mono truncate">
                        {ev.event_type}
                      </span>
                      <span className="text-[10px] text-rex-muted font-mono whitespace-nowrap">
                        {new Date(ev.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="text-[11px] text-rex-secondary mt-1">
                      Actor: <span className="font-mono text-rex-primary">{ev.actor}</span>
                    </div>
                    {ev.payload && Object.keys(ev.payload).length > 0 && (
                      <pre className="mt-2 text-[10px] font-mono text-rex-muted bg-rex-elevated/70 p-2 rounded overflow-x-auto">
                        {JSON.stringify(ev.payload, null, 2)}
                      </pre>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>
      )}

      {/* Contextual Inspector Drawer */}
      <Drawer
        isOpen={Boolean(selectedDrawerItem)}
        onClose={() => setSelectedDrawerItem(null)}
        title={
          selectedDrawerItem?.type === "hypothesis"
            ? `Hypothesis: ${selectedDrawerItem.data.id}`
            : selectedDrawerItem?.type === "claim"
            ? `Claim: ${selectedDrawerItem.data.id}`
            : `Details`
        }
        subtitle="Contextual Inspector"
      >
        {selectedDrawerItem && (
          <div className="space-y-4 text-xs">
            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                Statement
              </span>
              <p className="text-xs text-rex-primary mt-1 font-medium">
                {selectedDrawerItem.data.statement || selectedDrawerItem.data.text}
              </p>
            </div>

            {selectedDrawerItem.type === "hypothesis" && (
              <>
                <div>
                  <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                    Rationale
                  </span>
                  <p className="text-xs text-rex-secondary mt-1">{selectedDrawerItem.data.rationale || "N/A"}</p>
                </div>
                <div>
                  <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                    Falsification Condition
                  </span>
                  <p className="text-xs text-rex-secondary mt-1 font-mono">{selectedDrawerItem.data.falsification_condition}</p>
                </div>
                <div>
                  <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                    Expected Direction
                  </span>
                  <Badge variant="neutral" className="mt-1">
                    {selectedDrawerItem.data.expected_direction}
                  </Badge>
                </div>
              </>
            )}

            {selectedDrawerItem.type === "claim" && (
              <>
                <div>
                  <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                    Verification Status
                  </span>
                  <Badge variant={selectedDrawerItem.data.status} className="mt-1">
                    {selectedDrawerItem.data.status}
                  </Badge>
                </div>
                <div>
                  <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                    Confidence
                  </span>
                  <p className="text-xs text-rex-secondary mt-1 font-mono">
                    {selectedDrawerItem.data.confidence ? `${Math.round(selectedDrawerItem.data.confidence * 100)}%` : "Unrated"}
                  </p>
                </div>
                <div className="pt-2">
                  <Button
                    variant="primary"
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      navigate(`/evidence/lineage/${selectedDrawerItem.data.id}`);
                      setSelectedDrawerItem(null);
                    }}
                  >
                    Open Complete Lineage Trace
                  </Button>
                </div>
              </>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};
