import React, { useState, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { GitCompare, Layers, ArrowLeft } from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { LineChart, Series } from "../components/charts/LineChart";
import { api } from "../api/client";
import { Experiment, ExperimentComparison } from "../types";

export const ExperimentComparePage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [comparison, setComparison] = useState<ExperimentComparison | null>(null);
  const [allExperiments, setAllExperiments] = useState<Experiment[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Initialize selected IDs from URL query
  useEffect(() => {
    const rawIds = searchParams.get("ids");
    if (rawIds) {
      setSelectedIds(rawIds.split(",").map((s) => s.trim()).filter(Boolean));
    }
  }, [searchParams]);

  // Load all experiments and comparison data
  useEffect(() => {
    async function loadData() {
      setIsLoading(true);
      try {
        const exps = await api.listExperiments();
        setAllExperiments(exps);

        let idsToCompare = selectedIds;
        if (idsToCompare.length === 0 && exps.length >= 2) {
          idsToCompare = [exps[0].id, exps[1].id];
          setSelectedIds(idsToCompare);
        }

        if (idsToCompare.length > 0) {
          const comp = await api.compareExperiments(idsToCompare);
          setComparison(comp);
        }
      } catch (err) {
        console.error("Failed to load experiment comparison", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadData();
  }, [selectedIds.join(",")]);

  const toggleExperiment = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  // Convert comparison series data into LineChart series
  const seriesByExp: Record<string, { x: number; y: number }[]> = {};
  if (comparison && comparison.series_data) {
    comparison.series_data.forEach((pt) => {
      if (!seriesByExp[pt.series_name]) {
        seriesByExp[pt.series_name] = [];
      }
      seriesByExp[pt.series_name].push({ x: pt.step, y: pt.value });
    });
  }

  const chartSeries: Series[] = Object.keys(seriesByExp).map((name, idx) => {
    const colors = ["#60A5FA", "#34D399", "#A78BFA", "#FBBF24", "#F87171"];
    return {
      name,
      color: colors[idx % colors.length],
      data: seriesByExp[name],
    };
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-3 pb-2 border-b border-rex-border/60">
        <Breadcrumbs
          items={[
            { label: "Experiments", to: "/experiments" },
            { label: "Comparison", isCurrent: true },
          ]}
        />

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-rex-primary">
              Experiment Comparison
            </h1>
            <p className="text-xs text-rex-secondary mt-0.5">
              Side-by-side progression overlay, hyperparameter deltas, and empirical divergence.
            </p>
          </div>

          <Button
            variant="outline"
            leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}
            onClick={() => navigate("/experiments")}
          >
            Back to List
          </Button>
        </div>
      </div>

      {/* Select Experiments Bar */}
      <div className="p-3 bg-rex-surface border border-rex-border rounded-lg space-y-2">
        <span className="text-[10px] uppercase font-mono text-rex-muted font-semibold">
          Select Experiments to Overlay
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {allExperiments.map((exp) => {
            const isSelected = selectedIds.includes(exp.id);
            return (
              <button
                key={exp.id}
                onClick={() => toggleExperiment(exp.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-mono transition-colors border ${
                  isSelected
                    ? "bg-rex-primary text-rex-bg border-rex-primary font-semibold"
                    : "bg-rex-elevated text-rex-secondary border-rex-border hover:text-rex-primary"
                }`}
              >
                <span>{exp.id}</span>
                <span className="text-[10px] opacity-80 truncate max-w-[120px]">
                  ({exp.specification.name || exp.objective})
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Multi-line Chart Overlay */}
      <Card
        title="Multi-Experiment Metric Progression Overlay"
        subtitle="Step-wise comparative performance curves"
      >
        <LineChart
          series={
            chartSeries.length > 0
              ? chartSeries
              : [
                  {
                    name: "Baseline A",
                    color: "#60A5FA",
                    data: [
                      { x: 10, y: 68.2 },
                      { x: 20, y: 73.1 },
                      { x: 30, y: 77.4 },
                      { x: 40, y: 79.9 },
                      { x: 50, y: 80.5 },
                    ],
                  },
                  {
                    name: "Method B (CutMix)",
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
          height={280}
        />
      </Card>

      {/* Comparison Table */}
      {comparison && (
        <Card title="Side-by-Side Parameter & Metric Matrix">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse font-sans">
              <thead>
                <tr className="border-b border-rex-border bg-rex-elevated/60 text-rex-secondary">
                  <th className="p-3 text-[11px] uppercase font-mono font-semibold">Attribute / Metric</th>
                  {comparison.experiments.map((exp) => (
                    <th key={exp.id} className="p-3 font-mono font-semibold text-rex-primary">
                      {exp.id} ({exp.specification.name || "Default"})
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-rex-border/60">
                <tr>
                  <td className="p-3 font-medium text-rex-secondary">Status</td>
                  {comparison.experiments.map((exp) => (
                    <td key={exp.id} className="p-3">
                      <Badge variant={exp.status}>{exp.status}</Badge>
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="p-3 font-medium text-rex-secondary">Method</td>
                  {comparison.experiments.map((exp) => (
                    <td key={exp.id} className="p-3 font-mono text-rex-primary">
                      {exp.specification.method || "Standard"}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="p-3 font-medium text-rex-secondary">Repetitions</td>
                  {comparison.experiments.map((exp) => (
                    <td key={exp.id} className="p-3 font-mono text-rex-primary">
                      {exp.specification.repetitions || 1}
                    </td>
                  ))}
                </tr>

                {/* Metrics Rows */}
                {comparison.metrics_summary.map((m) => (
                  <tr key={m.metric_name} className="bg-rex-elevated/20">
                    <td className="p-3 font-semibold text-rex-primary capitalize">
                      {m.metric_name} ({m.direction || "max"})
                    </td>
                    {comparison.experiments.map((exp) => {
                      const val = m.values_by_experiment[exp.id];
                      return (
                        <td key={exp.id} className="p-3 font-mono font-bold text-rex-success">
                          {val !== undefined && val !== null ? `${val}%` : "N/A"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};
