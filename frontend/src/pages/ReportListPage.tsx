import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, ArrowRight, Download, CheckCircle, AlertTriangle } from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { api } from "../api/client";

export const ReportListPage: React.FC = () => {
  const [reports, setReports] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    async function loadReports() {
      setIsLoading(true);
      try {
        const data = await api.listReports();
        setReports(data);
      } catch (err) {
        console.error("Failed to load reports", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadReports();
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="pb-2 border-b border-rex-border/60">
        <h1 className="text-xl font-bold tracking-tight text-rex-primary">Research Reports</h1>
        <p className="text-xs text-rex-secondary mt-0.5">
          Auditable, evidence-grounded research documentation generated directly from verified database records.
        </p>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-xs text-rex-muted">Loading reports...</div>
      ) : reports.length === 0 ? (
        <Card>
          <div className="text-center py-12 space-y-3">
            <FileText className="w-8 h-8 text-rex-muted mx-auto" />
            <h3 className="text-sm font-semibold text-rex-primary">No Reports Available</h3>
            <p className="text-xs text-rex-secondary max-w-sm mx-auto">
              Reports are compiled from completed or active research investigations.
            </p>
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {reports.map((rep) => (
            <Card
              key={rep.research_run_id}
              title={
                <div className="flex items-center justify-between w-full">
                  <span className="font-mono text-xs">{rep.research_run_id}</span>
                  <Badge variant={rep.is_fully_grounded ? "verified" : "warning"}>
                    {rep.is_fully_grounded ? "✓ Fully Grounded" : "⚠️ Unsupported Claims"}
                  </Badge>
                </div>
              }
              subtitle={`Generated ${new Date(rep.generated_at).toLocaleDateString()}`}
              action={
                <Button
                  size="sm"
                  variant="outline"
                  rightIcon={<ArrowRight className="w-3 h-3" />}
                  onClick={() => navigate(`/reports/${rep.research_run_id}`)}
                >
                  Read Paper
                </Button>
              }
            >
              <div className="space-y-3 text-xs">
                <h4 className="font-semibold text-rex-primary line-clamp-1">{rep.title}</h4>
                <p className="text-rex-secondary line-clamp-2 leading-relaxed">
                  {rep.executive_summary}
                </p>

                <div className="flex items-center gap-3 pt-2 text-[11px] font-mono text-rex-muted border-t border-rex-border/40">
                  <span>{rep.experiments_count} experiments</span>
                  <span>•</span>
                  <span>{rep.metrics_count} metrics</span>
                  <span>•</span>
                  <span className={rep.unsupported_claims_count > 0 ? "text-rex-warning" : "text-rex-success"}>
                    {rep.unsupported_claims_count} unverified claims
                  </span>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};
