import React, { useState, useEffect } from "react";
import { Archive, Download, Search, CheckCircle, ShieldCheck } from "lucide-react";
import { Table, Column } from "../components/ui/Table";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Drawer } from "../components/ui/Drawer";
import { api } from "../api/client";
import { Artifact } from "../types";

export const ArtifactListPage: React.FC = () => {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [typeFilter, setTypeFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  // Drawer for artifact verification and inspect
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [verificationData, setVerificationData] = useState<any | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);

  useEffect(() => {
    async function loadArtifacts() {
      setIsLoading(true);
      try {
        const data = await api.listArtifacts(
          undefined,
          typeFilter === "all" ? undefined : typeFilter
        );
        setArtifacts(data);
      } catch (err) {
        console.error("Failed to load artifacts", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadArtifacts();
  }, [typeFilter]);

  const inspectArtifact = async (art: Artifact) => {
    setSelectedArtifact(art);
    setVerificationData(null);
    setIsVerifying(true);
    try {
      const res = await api.verifyArtifact(art.id);
      setVerificationData(res);
    } catch (err) {
      console.error("Verification failed", err);
    } finally {
      setIsVerifying(false);
    }
  };

  const filtered = artifacts.filter(
    (a) =>
      a.path.toLowerCase().includes(search.toLowerCase()) ||
      a.artifact_type.toLowerCase().includes(search.toLowerCase()) ||
      a.content_hash.toLowerCase().includes(search.toLowerCase())
  );

  const columns: Column<Artifact>[] = [
    {
      key: "id",
      header: "Artifact ID",
      width: "120px",
      render: (a) => <span className="font-mono font-semibold text-rex-primary">{a.id}</span>,
    },
    {
      key: "path",
      header: "Relative File Path & Digest",
      render: (a) => (
        <div className="max-w-md">
          <div className="font-semibold text-rex-primary font-mono truncate">{a.path}</div>
          <div className="text-[10px] text-rex-muted font-mono mt-0.5 truncate">
            SHA256: {a.content_hash}
          </div>
        </div>
      ),
    },
    {
      key: "artifact_type",
      header: "Type",
      width: "110px",
      render: (a) => <Badge variant="neutral">{a.artifact_type}</Badge>,
    },
    {
      key: "size_bytes",
      header: "Size",
      width: "100px",
      render: (a) => (
        <span className="font-mono text-xs text-rex-secondary">
          {a.size_bytes >= 1024 ? `${(a.size_bytes / 1024).toFixed(1)} KB` : `${a.size_bytes} B`}
        </span>
      ),
    },
    {
      key: "action",
      header: "Action",
      width: "120px",
      align: "right",
      render: (a) => (
        <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
          <Button size="sm" variant="outline" onClick={() => inspectArtifact(a)}>
            Verify
          </Button>
          <a
            href={api.getArtifactContentUrl(a.id)}
            download
            className="p-1 rounded text-rex-secondary hover:text-rex-primary hover:bg-rex-elevated transition-colors"
            title="Download file"
          >
            <Download className="w-3.5 h-3.5" />
          </a>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="pb-2 border-b border-rex-border/60">
        <h1 className="text-xl font-bold tracking-tight text-rex-primary">Artifact Explorer</h1>
        <p className="text-xs text-rex-secondary mt-0.5">
          Disk-stored execution manifests, model checkpoints, plots, and raw empirical metrics.
        </p>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-1 bg-rex-surface border border-rex-border rounded-lg p-1">
          {["all", "metric", "plot", "log", "model"].map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`px-3 py-1 rounded text-xs font-medium capitalize transition-colors ${
                typeFilter === t
                  ? "bg-rex-elevated text-rex-primary font-semibold shadow-xs"
                  : "text-rex-secondary hover:text-rex-primary"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-64">
          <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-rex-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search artifacts..."
            className="w-full bg-rex-surface border border-rex-border rounded-md pl-8 pr-3 py-1.5 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent"
          />
        </div>
      </div>

      {/* Artifacts Table */}
      <Table
        columns={columns}
        data={filtered}
        keyExtractor={(a) => a.id}
        onRowClick={(a) => inspectArtifact(a)}
        isLoading={isLoading}
        emptyMessage="No filesystem artifacts recorded matching criteria."
      />

      {/* Artifact Inspector Drawer */}
      <Drawer
        isOpen={Boolean(selectedArtifact)}
        onClose={() => setSelectedArtifact(null)}
        title={selectedArtifact?.id || "Artifact"}
        subtitle="Cryptographic Integrity Check"
      >
        {selectedArtifact && (
          <div className="space-y-4 text-xs">
            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                Path
              </span>
              <p className="font-mono text-xs text-rex-primary break-all mt-1">{selectedArtifact.path}</p>
            </div>

            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                Recorded Content Hash (SHA-256)
              </span>
              <p className="font-mono text-[11px] text-rex-secondary bg-rex-elevated p-2 rounded break-all mt-1">
                {selectedArtifact.content_hash}
              </p>
            </div>

            {verificationData && (
              <div className="p-3 bg-rex-elevated/50 border border-rex-border rounded-lg space-y-2">
                <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                  Physical Disk Verification
                </span>
                <div className="flex items-center justify-between">
                  <span className="text-rex-secondary">On-Disk Status:</span>
                  <Badge variant={verificationData.is_valid ? "verified" : "failed"}>
                    {verificationData.status.toUpperCase()}
                  </Badge>
                </div>
                {verificationData.actual_hash && (
                  <div>
                    <span className="text-[10px] text-rex-muted block">Computed Bytes Hash:</span>
                    <span className="font-mono text-[10px] text-rex-primary break-all">
                      {verificationData.actual_hash}
                    </span>
                  </div>
                )}
              </div>
            )}

            <div className="pt-2">
              <a
                href={api.getArtifactContentUrl(selectedArtifact.id)}
                download
                className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-rex-primary text-rex-bg rounded font-medium text-xs hover:opacity-90 transition-opacity"
              >
                <Download className="w-4 h-4" /> Download Raw File
              </a>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
