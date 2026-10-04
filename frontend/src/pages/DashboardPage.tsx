import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Play,
  Pause,
  PlusCircle,
  FlaskConical,
  FileText,
  Activity,
  CheckCircle,
  Layers,
  FileCheck,
  DollarSign,
  ArrowRight,
  TrendingUp,
} from "lucide-react";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { api } from "../api/client";
import { ResearchRun, SystemStatus } from "../types";

export const DashboardPage: React.FC = () => {
  const [activeRun, setActiveRun] = useState<ResearchRun | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [recentEvents, setRecentEvents] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    async function loadData() {
      try {
        const [runs, status] = await Promise.all([
          api.listResearchRuns(),
          api.getSystemStatus(),
        ]);

        if (runs.length > 0) {
          const run = runs[0];
          setActiveRun(run);
          const events = await api.getRunEvents(run.id, 6);
          setRecentEvents(events);
        }
        setSystemStatus(status);
      } catch (err) {
        console.error("Failed to load dashboard data", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadData();
  }, []);

  return (
    <div className="space-y-6">
      {/* Top Banner / Greeting */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-rex-border/60">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-rex-primary">
            Good morning, Aaditya
          </h1>
          <div className="flex items-center gap-2 mt-1">
            <span className="flex h-2 w-2 rounded-full bg-rex-success animate-pulse" />
            <p className="text-xs text-rex-secondary">
              REX is researching • Autonomous loop is active and bounded
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
            onClick={() => navigate("/research")}
          >
            New Research Run
          </Button>
        </div>
      </div>

      {/* Main Grid: Current Action & System Status */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left 2 Cols: Active Research Run & Current Action */}
        <div className="lg:col-span-2 space-y-5">
          {activeRun ? (
            <Card
              title={
                <div className="flex items-center justify-between w-full">
                  <span className="font-mono text-xs text-rex-muted">Active Run: {activeRun.id}</span>
                  <Badge variant={activeRun.status}>{activeRun.status}</Badge>
                </div>
              }
              subtitle={activeRun.title}
              action={
                <Link
                  to={`/research/${activeRun.id}`}
                  className="text-xs text-rex-info hover:underline flex items-center gap-1 font-medium"
                >
                  Workspace <ArrowRight className="w-3 h-3" />
                </Link>
              }
            >
              <div className="space-y-4">
                {/* Question */}
                <div className="p-3 bg-rex-elevated/40 rounded-md border border-rex-border/60">
                  <span className="text-[10px] uppercase tracking-wider font-semibold text-rex-muted font-mono">
                    Research Question
                  </span>
                  <p className="text-xs text-rex-primary mt-1 font-medium leading-relaxed">
                    {activeRun.research_question}
                  </p>
                </div>

                {/* Current Action Card */}
                <div className="p-4 bg-rex-elevated/70 border border-rex-border/80 rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-[10px] uppercase tracking-wider font-semibold text-rex-accent font-mono">
                        Current Autonomous Action
                      </span>
                      <h4 className="text-xs font-semibold text-rex-primary mt-0.5">
                        {activeRun.current_action || "Investigating problem space"}
                      </h4>
                    </div>
                    <span className="text-xs font-mono font-semibold text-rex-primary">
                      {Math.round((activeRun.current_action_progress || 0.6) * 100)}%
                    </span>
                  </div>

                  {/* Progress Bar */}
                  <div className="w-full bg-rex-border/60 rounded-full h-1.5 overflow-hidden">
                    <div
                      className="bg-rex-info h-1.5 rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.round((activeRun.current_action_progress || 0.6) * 100)}%`,
                      }}
                    />
                  </div>

                  {/* Reason & Next */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs pt-1">
                    <div>
                      <span className="text-[11px] text-rex-muted block font-medium">Reason</span>
                      <p className="text-[11px] text-rex-secondary mt-0.5 truncate">
                        {activeRun.current_action_reason || "Iteration requires control validation"}
                      </p>
                    </div>
                    <div>
                      <span className="text-[11px] text-rex-muted block font-medium">Next Action</span>
                      <p className="text-[11px] text-rex-secondary mt-0.5 truncate font-mono">
                        {activeRun.next_action || "Execute planned experiment"}
                      </p>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center justify-end gap-2 pt-2 border-t border-rex-border/40">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => navigate(`/research/${activeRun.id}`)}
                    >
                      View Workspace
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      leftIcon={<Pause className="w-3 h-3" />}
                      onClick={() => api.pauseResearchRun(activeRun.id)}
                    >
                      Pause
                    </Button>
                  </div>
                </div>

                {/* Run Metrics Strip */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
                  <div className="p-3 bg-rex-surface border border-rex-border/80 rounded-md">
                    <span className="text-[10px] text-rex-muted uppercase font-medium">Hypotheses</span>
                    <div className="text-lg font-bold font-mono text-rex-primary mt-0.5">
                      {activeRun.stats.hypotheses_count}
                    </div>
                  </div>
                  <div className="p-3 bg-rex-surface border border-rex-border/80 rounded-md">
                    <span className="text-[10px] text-rex-muted uppercase font-medium">Experiments</span>
                    <div className="text-lg font-bold font-mono text-rex-primary mt-0.5">
                      {activeRun.stats.experiments_count}
                    </div>
                  </div>
                  <div className="p-3 bg-rex-surface border border-rex-border/80 rounded-md">
                    <span className="text-[10px] text-rex-muted uppercase font-medium">Verified Results</span>
                    <div className="text-lg font-bold font-mono text-rex-success mt-0.5">
                      {activeRun.stats.verified_results_count}
                    </div>
                  </div>
                  <div className="p-3 bg-rex-surface border border-rex-border/80 rounded-md">
                    <span className="text-[10px] text-rex-muted uppercase font-medium">Compute Cost</span>
                    <div className="text-lg font-bold font-mono text-rex-primary mt-0.5">
                      ${activeRun.stats.compute_cost_estimate.toFixed(2)}
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          ) : (
            <Card>
              <div className="text-center py-10 space-y-3">
                <FlaskConical className="w-8 h-8 text-rex-muted mx-auto" />
                <h3 className="text-sm font-semibold text-rex-primary">No Active Research Run</h3>
                <p className="text-xs text-rex-secondary max-w-sm mx-auto">
                  Start your first autonomous research investigation to begin generating hypotheses and designing experiments.
                </p>
                <Button variant="primary" onClick={() => navigate("/research")}>
                  Create Research Run
                </Button>
              </div>
            </Card>
          )}

          {/* Recent Activity Stream */}
          <Card title="Recent Activity Timeline" subtitle="Immutable audit trail of research events">
            <div className="space-y-3">
              {recentEvents.length === 0 ? (
                <div className="text-xs text-rex-muted py-4 text-center">No recent events recorded.</div>
              ) : (
                recentEvents.map((ev) => (
                  <div
                    key={ev.event_id}
                    className="flex items-start gap-3 p-2.5 rounded-md hover:bg-rex-elevated/40 transition-colors"
                  >
                    <div className="p-1.5 rounded bg-rex-elevated border border-rex-border/60 flex-shrink-0 mt-0.5">
                      <Activity className="w-3.5 h-3.5 text-rex-info" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-rex-primary truncate font-mono">
                          {ev.event_type}
                        </span>
                        <span className="text-[10px] text-rex-muted font-mono whitespace-nowrap">
                          {new Date(ev.timestamp).toLocaleTimeString()}
                        </span>
                      </div>
                      <div className="text-[11px] text-rex-secondary mt-0.5">
                        Actor: <span className="font-mono text-rex-primary">{ev.actor}</span>
                        {ev.payload?.reason && (
                          <span className="ml-2 text-rex-muted">— {ev.payload.reason}</span>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>

        {/* Right 1 Col: System Status & Quick Actions */}
        <div className="space-y-5">
          {/* System Status Card */}
          <Card title="System Status" subtitle="Local Research Workstation">
            <div className="space-y-3.5">
              <div className="flex items-center justify-between text-xs pb-2 border-b border-rex-border/60">
                <span className="text-rex-secondary">Research Engine</span>
                <Badge variant="verified">Running</Badge>
              </div>
              <div className="flex items-center justify-between text-xs pb-2 border-b border-rex-border/60">
                <span className="text-rex-secondary">Docker Sandbox</span>
                <span className="font-mono text-xs text-rex-success font-medium">Isolated</span>
              </div>
              <div className="flex items-center justify-between text-xs pb-2 border-b border-rex-border/60">
                <span className="text-rex-secondary">Total Investigations</span>
                <span className="font-mono text-xs text-rex-primary font-semibold">
                  {systemStatus?.total_runs_count || 1}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs pb-2 border-b border-rex-border/60">
                <span className="text-rex-secondary">Active Experiments</span>
                <span className="font-mono text-xs text-rex-primary font-semibold">
                  {systemStatus?.experiments_count || 0}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs pb-2 border-b border-rex-border/60">
                <span className="text-rex-secondary">Evidence Claims</span>
                <span className="font-mono text-xs text-rex-primary font-semibold">
                  {systemStatus?.claims_count || 0}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-rex-secondary">Today's Budget</span>
                <span className="font-mono text-xs text-rex-primary font-semibold">
                  ${activeRun?.stats.compute_cost_estimate || 0.0} / $50.00
                </span>
              </div>
            </div>
          </Card>

          {/* Quick Actions */}
          <Card title="Quick Actions">
            <div className="space-y-2">
              <button
                onClick={() => navigate("/research")}
                className="w-full flex items-center justify-between p-2.5 rounded-md border border-rex-border hover:bg-rex-elevated transition-colors text-left group"
              >
                <div className="flex items-center gap-2.5">
                  <PlusCircle className="w-4 h-4 text-rex-accent" />
                  <div>
                    <div className="text-xs font-medium text-rex-primary">New Research Run</div>
                    <div className="text-[10px] text-rex-muted">Formulate question & budget</div>
                  </div>
                </div>
                <ArrowRight className="w-3.5 h-3.5 text-rex-muted group-hover:text-rex-primary transition-colors" />
              </button>

              <button
                onClick={() => navigate("/experiments")}
                className="w-full flex items-center justify-between p-2.5 rounded-md border border-rex-border hover:bg-rex-elevated transition-colors text-left group"
              >
                <div className="flex items-center gap-2.5">
                  <Layers className="w-4 h-4 text-rex-success" />
                  <div>
                    <div className="text-xs font-medium text-rex-primary">Inspect Experiments</div>
                    <div className="text-[10px] text-rex-muted">Review executions & logs</div>
                  </div>
                </div>
                <ArrowRight className="w-3.5 h-3.5 text-rex-muted group-hover:text-rex-primary transition-colors" />
              </button>

              <button
                onClick={() => navigate("/reports")}
                className="w-full flex items-center justify-between p-2.5 rounded-md border border-rex-border hover:bg-rex-elevated transition-colors text-left group"
              >
                <div className="flex items-center gap-2.5">
                  <FileText className="w-4 h-4 text-rex-info" />
                  <div>
                    <div className="text-xs font-medium text-rex-primary">View Latest Report</div>
                    <div className="text-[10px] text-rex-muted">Synthesized paper with lineage</div>
                  </div>
                </div>
                <ArrowRight className="w-3.5 h-3.5 text-rex-muted group-hover:text-rex-primary transition-colors" />
              </button>

              <button
                onClick={() => navigate("/evidence")}
                className="w-full flex items-center justify-between p-2.5 rounded-md border border-rex-border hover:bg-rex-elevated transition-colors text-left group"
              >
                <div className="flex items-center gap-2.5">
                  <CheckCircle className="w-4 h-4 text-rex-warning" />
                  <div>
                    <div className="text-xs font-medium text-rex-primary">Verification Audit</div>
                    <div className="text-[10px] text-rex-muted">Cryptographic proof check</div>
                  </div>
                </div>
                <ArrowRight className="w-3.5 h-3.5 text-rex-muted group-hover:text-rex-primary transition-colors" />
              </button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
