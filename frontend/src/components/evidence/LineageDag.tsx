import React from "react";
import {
  FileText,
  Activity,
  Layers,
  Terminal,
  FlaskConical,
  GitCommit,
  Database,
  Sliders,
  Archive,
  CheckCircle,
  AlertTriangle,
  XCircle,
  ExternalLink,
} from "lucide-react";
import { ClaimLineage, LineageNode } from "../../types";
import { Badge } from "../ui/Badge";

interface LineageDagProps {
  lineage: ClaimLineage;
  onSelectNode?: (node: LineageNode) => void;
  selectedNodeId?: string;
  className?: string;
}

export const LineageDag: React.FC<LineageDagProps> = ({
  lineage,
  onSelectNode,
  selectedNodeId,
  className = "",
}) => {
  const getNodeIcon = (type: string) => {
    switch (type.toLowerCase()) {
      case "claim":
        return <FileText className="w-4 h-4 text-rex-accent" />;
      case "analysis":
        return <Activity className="w-4 h-4 text-rex-info" />;
      case "result":
        return <Layers className="w-4 h-4 text-rex-success" />;
      case "execution":
        return <Terminal className="w-4 h-4 text-rex-warning" />;
      case "experiment":
        return <FlaskConical className="w-4 h-4 text-rex-accent" />;
      case "code":
        return <GitCommit className="w-4 h-4 text-rex-info" />;
      case "dataset":
        return <Database className="w-4 h-4 text-rex-success" />;
      case "configuration":
        return <Sliders className="w-4 h-4 text-rex-secondary" />;
      case "artifact":
        return <Archive className="w-4 h-4 text-rex-primary" />;
      default:
        return <FileText className="w-4 h-4 text-rex-muted" />;
    }
  };

  const getStatusIcon = (status: string) => {
    const s = status.toLowerCase();
    if (s === "verified" || s === "pass") {
      return <CheckCircle className="w-3.5 h-3.5 text-rex-success" />;
    }
    if (s === "unsupported" || s === "tampered" || s === "fail") {
      return <XCircle className="w-3.5 h-3.5 text-rex-error" />;
    }
    return <AlertTriangle className="w-3.5 h-3.5 text-rex-warning" />;
  };

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Lineage Header Status */}
      <div className="flex items-center justify-between p-3.5 bg-rex-surface border border-rex-border rounded-lg">
        <div>
          <span className="text-xs font-semibold text-rex-primary font-mono">{lineage.claim_id}</span>
          <p className="text-xs text-rex-secondary mt-0.5 max-w-xl">{lineage.statement}</p>
        </div>
        <div className="flex items-center gap-2">
          {lineage.is_complete ? (
            <Badge variant="verified" size="md">
              ✓ Fully Grounded Lineage
            </Badge>
          ) : (
            <Badge variant="warning" size="md">
              ⚠️ Incomplete Evidence Lineage
            </Badge>
          )}
        </div>
      </div>

      {/* Warnings / Gaps Banner if any */}
      {lineage.gaps && lineage.gaps.length > 0 && (
        <div className="p-3 bg-rex-warning-subtle border border-rex-warning/30 rounded-lg text-xs text-rex-warning space-y-1">
          <div className="font-semibold flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" />
            <span>Lineage Gaps Detected</span>
          </div>
          <ul className="list-disc list-inside text-[11px] pl-1 space-y-0.5 text-rex-secondary">
            {lineage.gaps.map((gap, idx) => (
              <li key={idx}>{gap}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Deterministic Traceable Vertical Pipeline */}
      <div className="relative pl-6 space-y-3 before:absolute before:left-3.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-rex-border">
        {lineage.nodes.map((node, index) => {
          const isSelected = selectedNodeId === node.id;
          return (
            <div
              key={node.id}
              onClick={() => onSelectNode && onSelectNode(node)}
              className={`relative flex items-start gap-3 p-3 bg-rex-surface border rounded-lg transition-all cursor-pointer ${
                isSelected
                  ? "border-rex-primary ring-1 ring-rex-primary shadow-sm"
                  : "border-rex-border hover:border-rex-border/80 hover:bg-rex-elevated/40"
              }`}
            >
              {/* Node connector dot */}
              <div
                className="absolute -left-[31px] top-3.5 w-4 h-4 rounded-full bg-rex-surface border-2 border-rex-border flex items-center justify-center"
              >
                <div className="w-1.5 h-1.5 rounded-full bg-rex-primary" />
              </div>

              {/* Node Icon */}
              <div className="p-2 rounded bg-rex-elevated border border-rex-border/60 flex-shrink-0">
                {getNodeIcon(node.type)}
              </div>

              {/* Node Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] uppercase tracking-wider font-semibold font-mono text-rex-muted">
                      {node.type}
                    </span>
                    <span className="text-xs font-semibold text-rex-primary font-mono">
                      {node.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {getStatusIcon(node.status)}
                    <Badge variant={node.status} size="sm">
                      {node.status}
                    </Badge>
                  </div>
                </div>

                <div className="text-xs text-rex-primary mt-1 font-medium">{node.label}</div>
                {node.sublabel && (
                  <div className="text-[11px] text-rex-secondary mt-0.5 font-mono truncate">
                    {node.sublabel}
                  </div>
                )}

                {node.hash && (
                  <div className="mt-1.5 text-[10px] text-rex-muted font-mono bg-rex-elevated/80 px-2 py-0.5 rounded inline-block">
                    SHA256: {node.hash}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
