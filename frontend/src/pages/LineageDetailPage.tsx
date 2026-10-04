import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, ShieldCheck, CheckCircle, AlertTriangle } from "lucide-react";
import { Button } from "../components/ui/Button";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { LineageDag } from "../components/evidence/LineageDag";
import { Drawer } from "../components/ui/Drawer";
import { api } from "../api/client";
import { ClaimLineage, LineageNode } from "../types";

export const LineageDetailPage: React.FC = () => {
  const { claimId } = useParams<{ claimId: string }>();
  const navigate = useNavigate();

  const [lineage, setLineage] = useState<ClaimLineage | null>(null);
  const [selectedNode, setSelectedNode] = useState<LineageNode | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!claimId) return;

    async function loadLineage() {
      setIsLoading(true);
      try {
        const data = await api.getClaimLineage(claimId!);
        setLineage(data);
      } catch (err) {
        console.error("Failed to load claim lineage", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadLineage();
  }, [claimId]);

  if (isLoading || !lineage) {
    return (
      <div className="py-20 text-center space-y-2">
        <div className="w-6 h-6 border-2 border-rex-primary border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-xs text-rex-muted">Tracing deterministic evidence provenance...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header & Breadcrumb */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Evidence", to: "/evidence" },
            { label: "Claims", to: "/evidence" },
            { label: lineage.claim_id, isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-rex-primary font-mono">
              Provenance Lineage: {lineage.claim_id}
            </h1>
            <p className="text-xs text-rex-secondary mt-0.5">
              Unbroken mechanical audit trail connecting claim to raw execution bytecode.
            </p>
          </div>

          <Button
            variant="outline"
            leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
            onClick={() => navigate("/evidence")}
          >
            Back to Claims
          </Button>
        </div>
      </div>

      {/* Lineage DAG Component */}
      <LineageDag
        lineage={lineage}
        selectedNodeId={selectedNode?.id}
        onSelectNode={(node) => setSelectedNode(node)}
      />

      {/* Node Inspector Drawer */}
      <Drawer
        isOpen={Boolean(selectedNode)}
        onClose={() => setSelectedNode(null)}
        title={selectedNode ? `${selectedNode.type.toUpperCase()}: ${selectedNode.id}` : "Node"}
        subtitle="Cryptographic & Provenance Inspector"
      >
        {selectedNode && (
          <div className="space-y-4 text-xs">
            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                Label
              </span>
              <p className="text-xs text-rex-primary font-semibold mt-1">{selectedNode.label}</p>
              {selectedNode.sublabel && (
                <p className="text-[11px] text-rex-secondary mt-0.5 font-mono">{selectedNode.sublabel}</p>
              )}
            </div>

            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                Verification Standing
              </span>
              <div className="flex items-center gap-2 mt-1">
                {selectedNode.status === "verified" ? (
                  <CheckCircle className="w-4 h-4 text-rex-success" />
                ) : (
                  <AlertTriangle className="w-4 h-4 text-rex-warning" />
                )}
                <span className="font-semibold capitalize text-rex-primary">{selectedNode.status}</span>
              </div>
            </div>

            {selectedNode.hash && (
              <div>
                <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold">
                  SHA-256 Digest
                </span>
                <p className="font-mono text-[10px] text-rex-secondary bg-rex-elevated p-2 rounded break-all mt-1">
                  {selectedNode.hash}
                </p>
              </div>
            )}

            <div>
              <span className="text-[10px] uppercase font-mono text-rex-muted block font-semibold mb-1">
                Node Properties
              </span>
              <pre className="bg-rex-bg border border-rex-border p-3 rounded font-mono text-[11px] text-rex-primary overflow-x-auto">
                {JSON.stringify(selectedNode.details, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
