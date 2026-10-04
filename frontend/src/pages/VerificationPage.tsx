import React, { useState, useEffect } from "react";
import {
  ShieldCheck,
  CheckCircle,
  XCircle,
  AlertTriangle,
  RotateCcw,
  FileCheck,
  Hash,
  Activity,
  Layers,
} from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { api } from "../api/client";
import { ResearchRun, VerificationReport } from "../types";

export const VerificationPage: React.FC = () => {
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [report, setReport] = useState<VerificationReport | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadRuns() {
      setIsLoading(true);
      try {
        const data = await api.listResearchRuns();
        setRuns(data);
        if (data.length > 0) {
          setSelectedRunId(data[0].id);
          // Run verification
          const rep = await api.verifyResearchRun(data[0].id);
          setReport(rep);
        }
      } catch (err) {
        console.error("Failed to load runs for verification", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadRuns();
  }, []);

  const handleRunVerification = async () => {
    if (!selectedRunId) return;
    setIsVerifying(true);
    try {
      const rep = await api.verifyResearchRun(selectedRunId);
      setReport(rep);
    } catch (err) {
      alert(`Verification failed: ${err}`);
    } finally {
      setIsVerifying(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Evidence", to: "/evidence" },
            { label: "Verification Interface", isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-rex-primary">
              Formal Verification Interface (REX-041)
            </h1>
            <p className="text-xs text-rex-secondary mt-0.5">
              Independent mathematical and cryptographic verification protocol ensuring zero empirical fabrication.
            </p>
          </div>

          <div className="flex items-center gap-3">
            {runs.length > 1 && (
              <select
                value={selectedRunId}
                onChange={(e) => setSelectedRunId(e.target.value)}
                className="bg-rex-surface border border-rex-border rounded-md px-3 py-1.5 text-xs text-rex-primary font-mono focus:outline-none focus:ring-1 focus:ring-rex-accent"
              >
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id} — {r.title || r.research_question.slice(0, 30)}
                  </option>
                ))}
              </select>
            )}

            <Button
              variant="primary"
              leftIcon={<ShieldCheck className="w-3.5 h-3.5" />}
              isLoading={isVerifying}
              onClick={handleRunVerification}
            >
              Verify Now
            </Button>
          </div>
        </div>
      </div>

      {report ? (
        <div className="space-y-6">
          {/* Aggregate Outcome Banner */}
          <div
            className={`p-5 rounded-xl border ${
              report.is_passed
                ? "bg-rex-success-subtle border-rex-success/30 text-rex-success"
                : "bg-rex-error-subtle border-rex-error/30 text-rex-error"
            } space-y-2`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                {report.is_passed ? (
                  <CheckCircle className="w-7 h-7 flex-shrink-0" />
                ) : (
                  <XCircle className="w-7 h-7 flex-shrink-0" />
                )}
                <div>
                  <h3 className="text-base font-bold">
                    {report.is_passed
                      ? "Research Verification Passed (100% Mechanically Grounded)"
                      : "Research Verification Failed — Integrity Gaps Detected"}
                  </h3>
                  <p className="text-xs opacity-90 mt-0.5">
                    Target Run: <span className="font-mono font-semibold">{report.research_run_id}</span> • Completed at{" "}
                    {new Date(report.completed_at).toLocaleTimeString()}
                  </p>
                </div>
              </div>

              <Badge variant={report.is_passed ? "verified" : "failed"} size="md">
                {report.status.toUpperCase()}
              </Badge>
            </div>
          </div>

          {/* 4 Core Verification Pillars */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {report.checks.map((chk) => (
              <Card
                key={chk.name}
                title={
                  <div className="flex items-center justify-between w-full">
                    <span className="text-xs font-semibold">{chk.name}</span>
                    <Badge variant={chk.status}>{chk.status}</Badge>
                  </div>
                }
              >
                <div className="space-y-2 text-xs">
                  <p className="text-rex-secondary leading-relaxed">{chk.message}</p>
                  {chk.details && Object.keys(chk.details).length > 0 && (
                    <pre className="p-2 bg-rex-elevated/70 rounded text-[10px] font-mono text-rex-muted overflow-x-auto">
                      {JSON.stringify(chk.details, null, 2)}
                    </pre>
                  )}
                </div>
              </Card>
            ))}
          </div>

          {/* Verified Claims Inspection */}
          <Card
            title={`Claim Provenance Verification (${report.claims_verified.length})`}
            subtitle="Lineage integrity per individual scientific claim"
          >
            {report.claims_verified.length === 0 ? (
              <div className="text-xs text-rex-muted py-4 text-center italic">No claims asserted yet.</div>
            ) : (
              <div className="space-y-2.5">
                {report.claims_verified.map((c: any) => (
                  <div
                    key={c.claim_id}
                    className="flex items-center justify-between p-3 bg-rex-surface border border-rex-border rounded-lg text-xs"
                  >
                    <div>
                      <div className="font-mono font-semibold text-rex-primary">{c.claim_id}</div>
                      <div className="text-rex-secondary mt-0.5">{c.statement}</div>
                    </div>
                    <Badge variant={c.is_lineage_intact ? "verified" : "warning"}>
                      {c.is_lineage_intact ? "Lineage Intact" : "Broken Lineage"}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Artifact Hash Inspection */}
          <Card
            title={`Cryptographic Byte Hashes (${report.artifacts_verified.length})`}
            subtitle="SHA-256 byte-level verification against physical filesystem records"
          >
            {report.artifacts_verified.length === 0 ? (
              <div className="text-xs text-rex-muted py-4 text-center italic">No artifacts checked.</div>
            ) : (
              <div className="space-y-2.5">
                {report.artifacts_verified.map((a: any, idx: number) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-3 bg-rex-surface border border-rex-border rounded-lg text-xs"
                  >
                    <div className="min-w-0 pr-4">
                      <div className="font-mono font-semibold text-rex-primary truncate">{a.path}</div>
                      <div className="text-[10px] text-rex-muted font-mono mt-0.5 truncate">
                        Expected: {a.expected_hash}
                      </div>
                    </div>
                    <Badge variant={a.is_valid ? "verified" : "failed"}>
                      {a.is_valid ? "Hash Match" : "Hash Mismatch / Missing"}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      ) : (
        <Card>
          <div className="text-center py-12 space-y-3">
            <ShieldCheck className="w-8 h-8 text-rex-muted mx-auto" />
            <h3 className="text-sm font-semibold text-rex-primary">Ready to Verify</h3>
            <p className="text-xs text-rex-secondary max-w-sm mx-auto">
              Execute the 12-step verification protocol to validate lineage completeness, cryptographic hashes, and statistical determinism.
            </p>
            <Button variant="primary" onClick={handleRunVerification}>
              Run Formal Verification
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
};
