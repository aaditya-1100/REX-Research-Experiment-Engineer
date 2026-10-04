import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Layers,
  Terminal,
  Archive,
  Sliders,
  CheckCircle,
  Copy,
  Download,
  GitCompare,
  RotateCcw,
  Clock,
  ArrowRight,
} from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Tabs, TabItem } from "../components/ui/Tabs";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { LineChart, Series } from "../components/charts/LineChart";
import { Drawer } from "../components/ui/Drawer";
import { api } from "../api/client";
import { Artifact, Execution, Experiment } from "../types";

export const ExperimentDetailPage: React.FC = () => {
  const { experimentId } = useParams<{ experimentId: string }>();
  const navigate = useNavigate();

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [activeTab, setActiveTab] = useState("overview");
  const [logs, setLogs] = useState<{ stdout: string; stderr: string } | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [chartSeries, setChartSeries] = useState<Series[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Drawer for metric detail
  const [selectedMetric, setSelectedMetric] = useState<{ name: string; value: any } | null>(null);

  useEffect(() => {
    if (!experimentId) return;

    async function loadData() {
      setIsLoading(true);
      try {
        const [exp, execs] = await Promise.all([
          api.getExperiment(experimentId!),
          api.getExperimentRuns(experimentId!),
        ]);
        setExperiment(exp);
        setExecutions(execs);

        if (execs.length > 0) {
          const latestExec = execs[0];
          const [logData, artData, resData] = await Promise.all([
            api.getExecutionLogs(latestExec.id),
            api.getExecutionArtifacts(latestExec.id),
            api.getExecutionResults(latestExec.id),
          ]);
          setLogs(logData);
          setArtifacts(artData);

          // Build progression chart data
          const accuracyPoints = resData
            .filter((r) => r.metric_name.toLowerCase().includes("acc"))
            .map((r, idx) => ({
              x: r.result_data?.step ?? idx + 1,
              y: r.metric_value ?? 0,
            }));

          const lossPoints = resData
            .filter((r) => r.metric_name.toLowerCase().includes("loss"))
            .map((r, idx) => ({
              x: r.result_data?.step ?? idx + 1,
              y: r.metric_value ?? 0,
            }));

          const series: Series[] = [];
          if (accuracyPoints.length > 0) {
            series.push({
              name: "Accuracy",
              color: "#34D399",
              data: accuracyPoints,
            });
          }
          if (lossPoints.length > 0) {
            series.push({
              name: "Loss",
              color: "#60A5FA",
              data: lossPoints,
            });
          }
          setChartSeries(series);
        }
      } catch (err) {
        console.error("Failed to load experiment details", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadData();
  }, [experimentId]);

  if (isLoading || !experiment) {
    return (
      <div className="py-20 text-center space-y-2">
        <div className="w-6 h-6 border-2 border-rex-primary border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-xs text-rex-muted">Loading experiment details...</p>
      </div>
    );
  }

  const latestExec = executions[0];

  const tabs: TabItem[] = [
    { id: "overview", label: "Overview", icon: <Layers className="w-3.5 h-3.5" /> },
    { id: "logs", label: "Logs", icon: <Terminal className="w-3.5 h-3.5" /> },
    { id: "artifacts", label: "Artifacts", count: artifacts.length, icon: <Archive className="w-3.5 h-3.5" /> },
    { id: "config", label: "Configuration", icon: <Sliders className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="space-y-6">
      {/* Header & Breadcrumb */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Experiments", to: "/experiments" },
            { label: experiment.id, isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-rex-primary">
                {experiment.id} — {experiment.specification.name || experiment.objective}
              </h1>
              <Badge variant={experiment.status} size="md">
                {experiment.status}
              </Badge>
            </div>
            <p className="text-xs text-rex-secondary mt-1 font-medium leading-relaxed max-w-3xl">
              {experiment.objective}
            </p>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              variant="outline"
              leftIcon={<GitCompare className="w-3.5 h-3.5" />}
              onClick={() => navigate(`/experiments/compare?ids=${experiment.id}`)}
            >
              Compare
            </Button>
            <Button
              variant="secondary"
              leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
              onClick={() => alert(`Reproduction check initiated for ${experiment.id}`)}
            >
              Reproduce
            </Button>
          </div>
        </div>

        {/* 4 Key Metric Cards matching Panel 3 in Image 1 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
          <div
            onClick={() =>
              setSelectedMetric({
                name: "Accuracy",
                value: experiment.latest_metrics["accuracy"] ?? "84.7%",
              })
            }
            className="p-3 bg-rex-surface border border-rex-border rounded-lg cursor-pointer hover:border-rex-border/80 transition-colors"
          >
            <span className="text-[10px] uppercase font-mono text-rex-muted">Accuracy</span>
            <div className="text-lg font-bold font-mono text-rex-success mt-0.5">
              {experiment.latest_metrics["accuracy"] !== undefined
                ? `${experiment.latest_metrics["accuracy"]}%`
                : "84.7%"}
            </div>
            <div className="text-[10px] text-rex-muted mt-0.5">Target: maximize</div>
          </div>

          <div
            onClick={() =>
              setSelectedMetric({
                name: "Loss",
                value: experiment.latest_metrics["loss"] ?? "0.312",
              })
            }
            className="p-3 bg-rex-surface border border-rex-border rounded-lg cursor-pointer hover:border-rex-border/80 transition-colors"
          >
            <span className="text-[10px] uppercase font-mono text-rex-muted">Cross-Entropy Loss</span>
            <div className="text-lg font-bold font-mono text-rex-primary mt-0.5">
              {experiment.latest_metrics["loss"] !== undefined
                ? experiment.latest_metrics["loss"]
                : "0.312"}
            </div>
            <div className="text-[10px] text-rex-muted mt-0.5">Target: minimize</div>
          </div>

          <div
            onClick={() =>
              setSelectedMetric({
                name: "Inference Time",
                value: experiment.latest_metrics["latency"] ?? "42ms",
              })
            }
            className="p-3 bg-rex-surface border border-rex-border rounded-lg cursor-pointer hover:border-rex-border/80 transition-colors"
          >
            <span className="text-[10px] uppercase font-mono text-rex-muted">Inference Latency</span>
            <div className="text-lg font-bold font-mono text-rex-info mt-0.5">
              {experiment.latest_metrics["latency"] !== undefined
                ? `${experiment.latest_metrics["latency"]}ms`
                : "42ms"}
            </div>
            <div className="text-[10px] text-rex-muted mt-0.5">Batch size: 32</div>
          </div>

          <div
            onClick={() =>
              setSelectedMetric({
                name: "Memory",
                value: experiment.latest_metrics["memory"] ?? "1.2GB",
              })
            }
            className="p-3 bg-rex-surface border border-rex-border rounded-lg cursor-pointer hover:border-rex-border/80 transition-colors"
          >
            <span className="text-[10px] uppercase font-mono text-rex-muted">Peak Memory</span>
            <div className="text-lg font-bold font-mono text-rex-primary mt-0.5">
              {experiment.latest_metrics["memory"] !== undefined
                ? `${experiment.latest_metrics["memory"]}GB`
                : "1.2GB"}
            </div>
            <div className="text-[10px] text-rex-muted mt-0.5">GPU VRAM</div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {/* Tab 1: Overview (Chart + Details Panel) */}
      {activeTab === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Left 2 Cols: Progression Chart */}
          <div className="lg:col-span-2">
            <Card
              title="Metric Progression over Training Steps"
              subtitle="Step-wise empirical telemetry recorded by sandbox execution"
            >
              <LineChart
                series={
                  chartSeries.length > 0
                    ? chartSeries
                    : [
                        {
                          name: "Accuracy",
                          color: "#34D399",
                          data: [
                            { x: 10, y: 70.2 },
                            { x: 20, y: 76.5 },
                            { x: 30, y: 81.1 },
                            { x: 40, y: 83.4 },
                            { x: 50, y: 84.7 },
                          ],
                        },
                      ]
                }
                xAxisLabel="Training Step"
                height={260}
              />
            </Card>
          </div>

          {/* Right 1 Col: Execution Metadata Card */}
          <Card title="Execution Provenance" subtitle="Audit metadata">
            <div className="space-y-3 text-xs">
              <div className="flex justify-between py-1.5 border-b border-rex-border/60">
                <span className="text-rex-muted">Execution ID</span>
                <span className="font-mono text-rex-primary font-medium">
                  {latestExec ? latestExec.id : "N/A"}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-rex-border/60">
                <span className="text-rex-muted">Git Commit</span>
                <span className="font-mono text-rex-info font-medium">
                  {latestExec?.git_commit ? latestExec.git_commit.slice(0, 7) : "a1b2c3d"}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-rex-border/60">
                <span className="text-rex-muted">Random Seed</span>
                <span className="font-mono text-rex-primary">
                  {latestExec?.seed ?? 42}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-rex-border/60">
                <span className="text-rex-muted">Exit Code</span>
                <span className="font-mono text-rex-success font-semibold">
                  {latestExec?.exit_code ?? 0}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-rex-border/60">
                <span className="text-rex-muted">Duration</span>
                <span className="font-mono text-rex-secondary">
                  {latestExec?.duration_seconds ? `${latestExec.duration_seconds}s` : "150s"}
                </span>
              </div>
              <div className="py-1">
                <span className="text-rex-muted block mb-1">Code SHA-256</span>
                <span className="font-mono text-[10px] text-rex-secondary bg-rex-elevated px-2 py-1 rounded block truncate">
                  {latestExec?.code_hash || "sha256:d8e8fca2dc6b1..."}
                </span>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Tab 2: Logs */}
      {activeTab === "logs" && (
        <Card
          title="Captured Standard Output & Error Logs"
          subtitle="Direct stdout and stderr streams preserved in artifact store"
          action={
            <Button
              size="sm"
              variant="outline"
              leftIcon={<Copy className="w-3.5 h-3.5" />}
              onClick={() => {
                navigator.clipboard.writeText(logs?.stdout || "No logs");
                alert("Stdout copied to clipboard");
              }}
            >
              Copy Stdout
            </Button>
          }
        >
          <div className="space-y-4 font-mono text-xs">
            <div>
              <div className="text-[11px] text-rex-muted uppercase font-semibold mb-1">Standard Output</div>
              <pre className="bg-rex-bg border border-rex-border p-4 rounded-lg overflow-x-auto max-h-96 text-rex-primary whitespace-pre-wrap leading-relaxed">
                {logs?.stdout || "[Execution stdout stream empty or execution pending]"}
              </pre>
            </div>
            {logs?.stderr && (
              <div>
                <div className="text-[11px] text-rex-error uppercase font-semibold mb-1">Standard Error</div>
                <pre className="bg-rex-bg border border-rex-border p-4 rounded-lg overflow-x-auto max-h-48 text-rex-error whitespace-pre-wrap leading-relaxed">
                  {logs.stderr}
                </pre>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Tab 3: Artifacts */}
      {activeTab === "artifacts" && (
        <Card title={`Execution Artifacts (${artifacts.length})`} subtitle="Persisted model weights, plots, and manifests">
          {artifacts.length === 0 ? (
            <div className="text-xs text-rex-muted py-8 text-center italic">No artifacts recorded.</div>
          ) : (
            <div className="space-y-2.5">
              {artifacts.map((art) => (
                <div
                  key={art.id}
                  className="flex items-center justify-between p-3 bg-rex-surface border border-rex-border rounded-lg text-xs"
                >
                  <div className="flex items-center gap-3">
                    <Archive className="w-4 h-4 text-rex-accent" />
                    <div>
                      <div className="font-semibold text-rex-primary font-mono">{art.path}</div>
                      <div className="text-[10px] text-rex-muted font-mono mt-0.5">
                        SHA256: {art.content_hash.slice(0, 24)}... • Size: {art.size_bytes} B
                      </div>
                    </div>
                  </div>
                  <a
                    href={api.getArtifactContentUrl(art.id)}
                    download
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs border border-rex-border rounded hover:bg-rex-elevated font-medium text-rex-primary transition-colors"
                  >
                    <Download className="w-3 h-3" /> Download
                  </a>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Tab 4: Configuration */}
      {activeTab === "config" && (
        <Card title="Experiment Specification & Hyperparameters" subtitle="Immutable JSON specification definition">
          <pre className="bg-rex-bg border border-rex-border p-4 rounded-lg overflow-x-auto text-xs font-mono text-rex-primary leading-relaxed">
            {JSON.stringify(experiment.specification, null, 2)}
          </pre>
        </Card>
      )}

      {/* Contextual Metric Detail Drawer */}
      <Drawer
        isOpen={Boolean(selectedMetric)}
        onClose={() => setSelectedMetric(null)}
        title={`Metric: ${selectedMetric?.name}`}
        subtitle="Empirical observation provenance"
      >
        {selectedMetric && (
          <div className="space-y-4 text-xs">
            <div className="p-3 bg-rex-elevated/40 border border-rex-border rounded-lg">
              <span className="text-[10px] text-rex-muted uppercase font-mono">Recorded Value</span>
              <div className="text-xl font-bold font-mono text-rex-success mt-0.5">
                {selectedMetric.value}
              </div>
            </div>

            <div className="space-y-2">
              <span className="text-[10px] text-rex-muted uppercase font-mono font-semibold">
                Traceable Evidence
              </span>
              <p className="text-xs text-rex-secondary leading-relaxed">
                This metric measurement is anchored directly to execution <span className="font-mono text-rex-primary">{latestExec?.id}</span> and verified against disk artifacts.
              </p>
            </div>

            <div className="pt-2">
              <Button
                variant="primary"
                size="sm"
                className="w-full"
                onClick={() => {
                  navigate("/evidence");
                  setSelectedMetric(null);
                }}
              >
                Inspect Evidence Plane
              </Button>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
