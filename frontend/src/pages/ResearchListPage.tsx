import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { PlusCircle, FlaskConical, Search } from "lucide-react";
import { Table, Column } from "../components/ui/Table";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Modal } from "../components/ui/Modal";
import { api } from "../api/client";
import { ResearchRun } from "../types";

export const ResearchListPage: React.FC = () => {
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [filter, setFilter] = useState<"all" | "active" | "completed">("all");
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // New Run Form State
  const [newTitle, setNewTitle] = useState("");
  const [newQuestion, setNewQuestion] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const navigate = useNavigate();

  const loadRuns = async () => {
    setIsLoading(true);
    try {
      const data = await api.listResearchRuns(filter === "all" ? undefined : filter);
      setRuns(data);
    } catch (err) {
      console.error("Failed to load runs", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadRuns();
  }, [filter]);

  const handleCreateRun = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newQuestion.trim()) return;

    setIsSubmitting(true);
    try {
      const created = await api.createResearchRun({
        title: newTitle.trim(),
        research_question: newQuestion.trim(),
      });
      setIsModalOpen(false);
      setNewTitle("");
      setNewQuestion("");
      navigate(`/research/${created.id}`);
    } catch (err) {
      alert(`Error creating research run: ${err}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  const filteredRuns = runs.filter(
    (r) =>
      r.title.toLowerCase().includes(search.toLowerCase()) ||
      r.research_question.toLowerCase().includes(search.toLowerCase()) ||
      r.id.toLowerCase().includes(search.toLowerCase())
  );

  const columns: Column<ResearchRun>[] = [
    {
      key: "id",
      header: "Run ID",
      width: "120px",
      render: (r) => <span className="font-mono font-semibold text-rex-primary">{r.id}</span>,
    },
    {
      key: "question",
      header: "Research Question & Title",
      render: (r) => (
        <div className="max-w-md">
          <div className="font-semibold text-rex-primary truncate">{r.title || r.id}</div>
          <div className="text-[11px] text-rex-secondary truncate mt-0.5">{r.research_question}</div>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "120px",
      render: (r) => <Badge variant={r.status}>{r.status}</Badge>,
    },
    {
      key: "progress",
      header: "Progress",
      width: "160px",
      render: (r) => {
        const pct = Math.round((r.current_action_progress || 0.4) * 100);
        return (
          <div className="w-full space-y-1">
            <div className="flex justify-between text-[10px] font-mono text-rex-muted">
              <span>{r.current_action || "Investigating"}</span>
              <span>{pct}%</span>
            </div>
            <div className="w-full bg-rex-border/60 rounded-full h-1">
              <div
                className="bg-rex-info h-1 rounded-full"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      },
    },
    {
      key: "stats",
      header: "Metrics",
      width: "180px",
      render: (r) => (
        <div className="text-[11px] font-mono text-rex-secondary space-x-2">
          <span>{r.stats.hypotheses_count} hyp</span>
          <span>•</span>
          <span>{r.stats.experiments_count} exp</span>
          <span>•</span>
          <span className="text-rex-success">{r.stats.verified_results_count} verified</span>
        </div>
      ),
    },
    {
      key: "created_at",
      header: "Created",
      width: "110px",
      align: "right",
      render: (r) => (
        <span className="text-rex-muted font-mono text-[11px]">
          {new Date(r.created_at).toLocaleDateString()}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-rex-border/60">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-rex-primary">Research Runs</h1>
          <p className="text-xs text-rex-secondary mt-0.5">
            Table-first directory of active and historical autonomous research investigations.
          </p>
        </div>
        <Button
          variant="primary"
          leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
          onClick={() => setIsModalOpen(true)}
        >
          New Research
        </Button>
      </div>

      {/* Controls Bar: Filters & Search */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        {/* Filter Pills */}
        <div className="flex items-center gap-1 bg-rex-surface border border-rex-border rounded-lg p-1">
          {(["all", "active", "completed"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setFilter(tab)}
              className={`px-3 py-1 rounded text-xs font-medium capitalize transition-colors ${
                filter === tab
                  ? "bg-rex-elevated text-rex-primary font-semibold shadow-xs"
                  : "text-rex-secondary hover:text-rex-primary"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="relative w-full sm:w-64">
          <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-rex-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter research runs..."
            className="w-full bg-rex-surface border border-rex-border rounded-md pl-8 pr-3 py-1.5 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent"
          />
        </div>
      </div>

      {/* Table */}
      <Table
        columns={columns}
        data={filteredRuns}
        keyExtractor={(r) => r.id}
        onRowClick={(r) => navigate(`/research/${r.id}`)}
        isLoading={isLoading}
        emptyMessage="No research runs found matching the filter."
      />

      {/* New Research Run Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Initiate Research Investigation"
        subtitle="Formulate the central scientific ML question to launch the autonomous agent loop."
      >
        <form onSubmit={handleCreateRun} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-rex-primary mb-1">
              Investigation Title
            </label>
            <input
              type="text"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="e.g., Vision Robustness to Out-of-Distribution Corruptions"
              className="w-full bg-rex-surface border border-rex-border rounded-md px-3 py-2 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-rex-primary mb-1">
              Scientific Research Question <span className="text-rex-error">*</span>
            </label>
            <textarea
              required
              rows={3}
              value={newQuestion}
              onChange={(e) => setNewQuestion(e.target.value)}
              placeholder="e.g., Does CutMix augmentation significantly improve model robustness on ImageNet-C without degrading clean test accuracy?"
              className="w-full bg-rex-surface border border-rex-border rounded-md px-3 py-2 text-xs text-rex-primary placeholder-rex-muted focus:outline-none focus:ring-1 focus:ring-rex-accent leading-relaxed"
            />
          </div>

          <div className="p-3 bg-rex-elevated/40 border border-rex-border/60 rounded-md text-[11px] text-rex-muted">
            <span className="font-semibold text-rex-secondary">Default Resource Bounds:</span> Max 10 experiments, $50 compute budget, Docker isolation enabled, fail-closed reproducibility checks.
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setIsModalOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              Launch Research Run
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
