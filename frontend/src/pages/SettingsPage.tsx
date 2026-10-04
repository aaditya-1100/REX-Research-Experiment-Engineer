import React, { useState, useEffect } from "react";
import { Settings, Shield, HardDrive, Cpu, BookOpen, Lock } from "lucide-react";
import { Card } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { api } from "../api/client";
import { SystemSettings } from "../types";

export const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadSettings() {
      setIsLoading(true);
      try {
        const data = await api.getSettings();
        setSettings(data);
      } catch (err) {
        console.error("Failed to load settings", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadSettings();
  }, []);

  if (isLoading || !settings) {
    return <div className="py-20 text-center text-xs text-rex-muted">Loading system configuration...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="pb-2 border-b border-rex-border/60">
        <h1 className="text-xl font-bold tracking-tight text-rex-primary">System Settings</h1>
        <p className="text-xs text-rex-secondary mt-0.5">
          Configuration boundaries, sandbox resource constraints, and active provider credentials.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Environment & App */}
        <Card
          title={
            <div className="flex items-center gap-2">
              <Settings className="w-4 h-4 text-rex-info" />
              <span>Application & Environment</span>
            </div>
          }
        >
          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Environment</span>
              <Badge variant="neutral">{settings.app.environment}</Badge>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Version</span>
              <span className="font-mono text-rex-primary font-semibold">{settings.app.version}</span>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Log Level</span>
              <span className="font-mono text-rex-secondary">{settings.app.log_level}</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-rex-muted">Debug Mode</span>
              <span className="font-mono text-rex-secondary">{settings.app.debug ? "Enabled" : "Disabled"}</span>
            </div>
          </div>
        </Card>

        {/* Persistence & Storage */}
        <Card
          title={
            <div className="flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-rex-success" />
              <span>Persistence & Storage</span>
            </div>
          }
        >
          <div className="space-y-2.5 text-xs">
            <div className="py-1 border-b border-rex-border/40">
              <span className="text-rex-muted block mb-0.5">Database URL</span>
              <span className="font-mono text-[11px] text-rex-primary break-all">
                {settings.persistence.database_url}
              </span>
            </div>
            <div className="py-1 border-b border-rex-border/40">
              <span className="text-rex-muted block mb-0.5">Artifact Storage Root</span>
              <span className="font-mono text-[11px] text-rex-secondary break-all">
                {settings.persistence.artifact_root}
              </span>
            </div>
            <div className="py-1">
              <span className="text-rex-muted block mb-0.5">Sandbox Workspace Root</span>
              <span className="font-mono text-[11px] text-rex-secondary break-all">
                {settings.persistence.workspace_root}
              </span>
            </div>
          </div>
        </Card>

        {/* Docker Sandbox */}
        <Card
          title={
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-rex-accent" />
              <span>Docker Sandbox Isolation</span>
            </div>
          }
        >
          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Isolation Status</span>
              <Badge variant={settings.docker.enabled ? "verified" : "warning"}>
                {settings.docker.enabled ? "Active" : "Disabled"}
              </Badge>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Base Image</span>
              <span className="font-mono text-rex-primary">{settings.docker.image}</span>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Network Disabled</span>
              <span className="font-mono text-rex-success">{settings.docker.network_disabled ? "YES (air-gapped)" : "NO"}</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-rex-muted">Execution Timeout</span>
              <span className="font-mono text-rex-secondary">{settings.docker.timeout_seconds}s</span>
            </div>
          </div>
        </Card>

        {/* Autonomous Budgets */}
        <Card
          title={
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-rex-warning" />
              <span>Autonomous Resource Budgets</span>
            </div>
          }
        >
          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Max Experiments / Run</span>
              <span className="font-mono text-rex-primary font-semibold">{settings.budgets.max_experiments}</span>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Max Executions / Run</span>
              <span className="font-mono text-rex-primary font-semibold">{settings.budgets.max_executions}</span>
            </div>
            <div className="flex justify-between py-1 border-b border-rex-border/40">
              <span className="text-rex-muted">Max Runtime Seconds</span>
              <span className="font-mono text-rex-secondary">{settings.budgets.max_runtime_seconds}s</span>
            </div>
            <div className="flex justify-between py-1">
              <span className="text-rex-muted">Max Concurrent Executions</span>
              <span className="font-mono text-rex-secondary">{settings.budgets.max_concurrent_executions}</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
};
