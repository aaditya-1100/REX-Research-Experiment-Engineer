import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FileCheck, ShieldCheck, ArrowRight, Search, CheckCircle, AlertTriangle } from "lucide-react";
import { Table, Column } from "../components/ui/Table";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { api } from "../api/client";
import { Claim } from "../types";

export const EvidencePage: React.FC = () => {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [verificationResult, setVerificationResult] = useState<any | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);

  const navigate = useNavigate();

  useEffect(() => {
    async function loadClaims() {
      setIsLoading(true);
      try {
        const data = await api.listClaims(
          undefined,
          statusFilter === "all" ? undefined : statusFilter
        );
        setClaims(data);
      } catch (err) {
        console.error("Failed to load claims", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadClaims();
  }, [statusFilter]);

  const handleRunVerification = async () => {
    if (claims.length === 0) return;
    setIsVerifying(true);
    try {
      const rep = await api.verifyResearchRun(claims[0].research_run_id);
      setVerificationResult(rep);
    } catch (err) {
      alert(`Verification failed: ${err}`);
    } finally {
      setIsVerifying(false);
    }
  };

  const filtered = claims.filter(
    (c) =>
      c.statement.toLowerCase().includes(search.toLowerCase()) ||
      c.id.toLowerCase().includes(search.toLowerCase()) ||
      c.claim_type.toLowerCase().includes(search.toLowerCase())
  );

  const columns: Column<Claim>[] = [
    {
      key: "id",
      header: "Claim ID",
      width: "120px",
      render: (c) => <span className="font-mono font-semibold text-rex-primary">{c.id}</span>,
    },
    {
      key: "statement",
      header: "Scientific Claim Statement",
      render: (c) => (
        <div className="max-w-lg">
          <div className="font-semibold text-rex-primary">{c.statement}</div>
          <div className="text-[11px] text-rex-muted font-mono mt-0.5">
            Type: {c.claim_type} • Confidence: {c.confidence ? `${Math.round(c.confidence * 100)}%` : "N/A"}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "130px",
      render: (c) => <Badge variant={c.status}>{c.status}</Badge>,
    },
    {
      key: "links",
      header: "Evidence Links",
      width: "130px",
      render: (c) => (
        <span className="font-mono text-xs text-rex-secondary">
          {c.evidence_links_count} links
        </span>
      ),
    },
    {
      key: "action",
      header: "Lineage",
      width: "110px",
      align: "right",
      render: (c) => (
        <Button
          size="sm"
          variant="outline"
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/evidence/lineage/${c.id}`);
          }}
        >
          View DAG
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-rex-border/60">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-rex-primary">Evidence & Lineage Plane</h1>
          <p className="text-xs text-rex-secondary mt-0.5">
            Cryptographically anchored scientific claims linked to analyses, raw results, and execution bytecode.
          </p>
        </div>

        <Button
          variant="primary"
          leftIcon={<ShieldCheck className="w-3.5 h-3.5" />}
          isLoading={isVerifying}
          onClick={handleRunVerification}
        >
          Run Verification Audit
        </Button>
      </div>

      {/* Verification Summary Banner (if verified) */}
      {verificationResult && (
        <Card
          title={
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs">Verification Audit Outcome</span>
              <Badge variant={verificationResult.is_passed ? "verified" : "failed"}>
                {verificationResult.status.toUpperCase()}
              </Badge>
            </div>
          }
          subtitle={`Completed ${new Date(verificationResult.completed_at).toLocaleTimeString()}`}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {verificationResult.checks.map((chk: any) => (
              <div key={chk.name} className="p-3 bg-rex-elevated/40 border border-rex-border rounded-lg text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-rex-primary truncate">{chk.name}</span>
                  <Badge variant={chk.status} size="sm">{chk.status}</Badge>
                </div>
                <p className="text-[11px] text-rex-secondary">{chk.message}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Controls Bar: Filters & Search */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-1 bg-rex-surface border border-rex-border rounded-lg p-1">
          {["all", "verified", "supported", "unsupported"].map((st) => (
            <button
              key={st}
              onClick={() => setStatusFilter(st)}
              className={`px-3 py-1 rounded text-xs font-medium capitalize transition-colors ${
                statusFilter === st
                  ? "bg-rex-elevated text-rex-primary font-semibold shadow-xs"
                  : "text-rex-secondary hover:text-rex-primary"
              }`}
            >
              {st}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-64">
          <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-rex-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search claims..."
            className="w-full bg-rex-surface border border-rex-border rounded-md pl-8 pr-3 py-1.5 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent"
          />
        </div>
      </div>

      {/* Claims Table */}
      <Table
        columns={columns}
        data={filtered}
        keyExtractor={(c) => c.id}
        onRowClick={(c) => navigate(`/evidence/lineage/${c.id}`)}
        isLoading={isLoading}
        emptyMessage="No scientific claims found matching the filter."
      />
    </div>
  );
};
