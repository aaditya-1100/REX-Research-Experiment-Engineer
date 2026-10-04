import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Layers, GitCompare, Search, ArrowRight } from "lucide-react";
import { Table, Column } from "../components/ui/Table";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { api } from "../api/client";
import { Experiment } from "../types";

export const ExperimentListPage: React.FC = () => {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const navigate = useNavigate();

  useEffect(() => {
    async function loadExperiments() {
      setIsLoading(true);
      try {
        const data = await api.listExperiments(
          undefined,
          statusFilter === "all" ? undefined : statusFilter
        );
        setExperiments(data);
      } catch (err) {
        console.error("Failed to load experiments", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadExperiments();
  }, [statusFilter]);

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const filtered = experiments.filter(
    (e) =>
      e.objective.toLowerCase().includes(search.toLowerCase()) ||
      e.id.toLowerCase().includes(search.toLowerCase()) ||
      (e.specification.method && e.specification.method.toLowerCase().includes(search.toLowerCase()))
  );

  const columns: Column<Experiment>[] = [
    {
      key: "select",
      header: "",
      width: "40px",
      render: (e) => (
        <input
          type="checkbox"
          checked={selectedIds.includes(e.id)}
          onClick={(evt) => toggleSelect(e.id, evt)}
          onChange={() => {}}
          className="rounded border-rex-border text-rex-primary focus:ring-rex-accent"
        />
      ),
    },
    {
      key: "id",
      header: "Experiment ID",
      width: "120px",
      render: (e) => <span className="font-mono font-semibold text-rex-primary">{e.id}</span>,
    },
    {
      key: "objective",
      header: "Objective & Method",
      render: (e) => (
        <div className="max-w-md">
          <div className="font-semibold text-rex-primary truncate">{e.objective}</div>
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
      header: "Latest Measured Metrics",
      width: "200px",
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
      key: "created_at",
      header: "Designed",
      width: "100px",
      align: "right",
      render: (e) => (
        <span className="text-rex-muted font-mono text-[11px]">
          {new Date(e.created_at).toLocaleDateString()}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-rex-border/60">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-rex-primary">Experiment Explorer</h1>
          <p className="text-xs text-rex-secondary mt-0.5">
            Deterministic experiment specifications, execution runs, metrics, and reproducible artifacts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selectedIds.length >= 2 && (
            <Button
              variant="primary"
              leftIcon={<GitCompare className="w-3.5 h-3.5" />}
              onClick={() => navigate(`/experiments/compare?ids=${selectedIds.join(",")}`)}
            >
              Compare Selected ({selectedIds.length})
            </Button>
          )}
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-1 bg-rex-surface border border-rex-border rounded-lg p-1">
          {["all", "completed", "running", "failed"].map((st) => (
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
            placeholder="Search experiments..."
            className="w-full bg-rex-surface border border-rex-border rounded-md pl-8 pr-3 py-1.5 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent"
          />
        </div>
      </div>

      {/* Table */}
      <Table
        columns={columns}
        data={filtered}
        keyExtractor={(e) => e.id}
        onRowClick={(e) => navigate(`/experiments/${e.id}`)}
        isLoading={isLoading}
        emptyMessage="No experiments found."
      />
    </div>
  );
};
