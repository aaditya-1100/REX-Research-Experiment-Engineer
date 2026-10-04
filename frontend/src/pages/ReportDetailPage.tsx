import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  FileText,
  Download,
  ArrowLeft,
  CheckCircle,
  AlertTriangle,
  Layers,
  Activity,
  Sliders,
} from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Tabs, TabItem } from "../components/ui/Tabs";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { api } from "../api/client";
import { ResearchReport } from "../types";

export const ReportDetailPage: React.FC = () => {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();

  const [report, setReport] = useState<ResearchReport | null>(null);
  const [activeTab, setActiveTab] = useState("summary");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!reportId) return;

    async function loadReport() {
      setIsLoading(true);
      try {
        const data = await api.getReportByRunId(reportId!);
        setReport(data);
      } catch (err) {
        console.error("Failed to load report", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadReport();
  }, [reportId]);

  if (isLoading || !report) {
    return (
      <div className="py-20 text-center space-y-2">
        <div className="w-6 h-6 border-2 border-rex-primary border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-xs text-rex-muted">Loading research publication...</p>
      </div>
    );
  }

  const tabs: TabItem[] = [
    { id: "summary", label: "Executive Summary", icon: <FileText className="w-3.5 h-3.5" /> },
    { id: "experiments", label: "Methods & Experiments", count: report.experiments.length, icon: <Layers className="w-3.5 h-3.5" /> },
    { id: "results", label: "Results & Statistics", count: report.metrics.length, icon: <Activity className="w-3.5 h-3.5" /> },
    { id: "claims", label: "Claims & Conclusions", count: report.conclusions.length, icon: <CheckCircle className="w-3.5 h-3.5" /> },
    { id: "markdown", label: "Markdown View", icon: <Sliders className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="space-y-6">
      {/* Header & Breadcrumb */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Reports", to: "/reports" },
            { label: report.research_run_id, isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-rex-primary">
                {report.title}
              </h1>
              <Badge variant={report.is_fully_grounded ? "verified" : "warning"} size="md">
                {report.is_fully_grounded ? "✓ Fully Grounded" : "⚠️ Unsupported Claims Present"}
              </Badge>
            </div>
            <p className="text-xs text-rex-secondary mt-1 font-mono">
              Investigation: {report.research_run_id} • Compiled {new Date(report.generated_at).toLocaleString()}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={() => {
                const blob = new Blob([report.markdown], { type: "text/markdown" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `report_${report.research_run_id}.md`;
                a.click();
              }}
            >
              Export Markdown
            </Button>
            <Button
              variant="secondary"
              leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
              onClick={() => navigate("/reports")}
            >
              Reports
            </Button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {/* Tab 1: Executive Summary */}
      {activeTab === "summary" && (
        <div className="space-y-6">
          <Card title="Abstract & Executive Summary">
            <p className="text-xs text-rex-primary leading-relaxed font-sans">
              {report.executive_summary}
            </p>
          </Card>

          {/* Unsupported Claims Quarantine Notice */}
          {report.unsupported_claims && report.unsupported_claims.length > 0 && (
            <div className="p-4 bg-rex-warning-subtle border border-rex-warning/40 rounded-lg space-y-2">
              <div className="flex items-center gap-2 font-semibold text-xs text-rex-warning">
                <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                <span>Epistemic Quarantine: Unsupported Claims Identified ({report.unsupported_claims.length})</span>
              </div>
              <p className="text-xs text-rex-secondary">
                The following statements lack unbroken empirical provenance and have been quarantined from verified findings:
              </p>
              <div className="space-y-1.5 pt-1">
                {report.unsupported_claims.map((uc: any, idx: number) => (
                  <div key={idx} className="p-2 bg-rex-surface border border-rex-warning/30 rounded text-xs text-rex-primary">
                    <span className="font-mono text-rex-warning mr-2">[{uc.claim_id || `C-0${idx + 1}`}]</span>
                    {uc.statement || JSON.stringify(uc)}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Hypotheses Overview */}
          <Card title={`Formulated Hypotheses (${report.hypotheses.length})`}>
            <div className="space-y-2.5">
              {report.hypotheses.map((h: any, idx: number) => (
                <div key={idx} className="p-3 bg-rex-surface border border-rex-border rounded-lg text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-semibold text-rex-primary">{h.id || `H-${idx + 1}`}</span>
                    <Badge variant={h.status}>{h.status || "active"}</Badge>
                  </div>
                  <p className="text-rex-primary font-medium">{h.statement}</p>
                  {h.falsification_condition && (
                    <div className="text-[11px] text-rex-secondary">
                      <span className="text-rex-muted">Falsification:</span> {h.falsification_condition}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* Tab 2: Experiments */}
      {activeTab === "experiments" && (
        <Card title="Executed Experimental Methodology">
          <div className="space-y-3">
            {report.experiments.map((exp: any, idx: number) => (
              <div key={idx} className="p-3 bg-rex-surface border border-rex-border rounded-lg text-xs space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-semibold text-rex-primary">{exp.id || `EXP-0${idx + 1}`}</span>
                  <Badge variant={exp.status}>{exp.status || "completed"}</Badge>
                </div>
                <div className="font-medium text-rex-primary">{exp.objective}</div>
                {exp.specification && (
                  <div className="text-[11px] font-mono text-rex-muted">
                    Method: {exp.specification.method || "Standard"} • Variables: {JSON.stringify(exp.specification.variables || {})}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Tab 3: Results */}
      {activeTab === "results" && (
        <Card title="Empirical Metric Observations">
          <div className="space-y-2.5">
            {report.metrics.map((m: any, idx: number) => (
              <div key={idx} className="flex items-center justify-between p-3 bg-rex-surface border border-rex-border rounded-lg text-xs">
                <div>
                  <span className="font-semibold capitalize text-rex-primary">{m.metric_name}</span>
                  <div className="text-[10px] text-rex-muted font-mono mt-0.5">
                    Execution: {m.execution_id || "exec_01"}
                  </div>
                </div>
                <span className="font-mono font-bold text-rex-success text-sm">
                  {m.metric_value} {m.metric_unit || ""}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Tab 4: Conclusions */}
      {activeTab === "claims" && (
        <Card title="Grounded Scientific Conclusions">
          <div className="space-y-3">
            {report.conclusions.map((c: any, idx: number) => (
              <div key={idx} className="flex items-start gap-2.5 p-3 bg-rex-surface border border-rex-border rounded-lg text-xs">
                <CheckCircle className="w-4 h-4 text-rex-success flex-shrink-0 mt-0.5" />
                <div className="text-rex-primary leading-relaxed font-medium">
                  {typeof c === "string" ? c : c.statement || JSON.stringify(c)}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Tab 5: Markdown */}
      {activeTab === "markdown" && (
        <Card title="Raw Markdown Publication Document">
          <pre className="bg-rex-bg border border-rex-border p-5 rounded-lg overflow-x-auto text-xs font-mono text-rex-primary whitespace-pre-wrap leading-relaxed max-h-[70vh]">
            {report.markdown}
          </pre>
        </Card>
      )}
    </div>
  );
};
