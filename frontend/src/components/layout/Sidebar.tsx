import React from "react";
import { NavLink } from "react-router-dom";
import {
  Home,
  FlaskConical,
  Layers,
  FileCheck,
  FileText,
  Archive,
  ShieldCheck,
  Settings,
  Activity,
} from "lucide-react";

interface SidebarProps {
  activeRunTitle?: string;
  isAutonomousRunning?: boolean;
  className?: string;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeRunTitle = "R-014 Distribution Shift",
  isAutonomousRunning = true,
  className = "",
}) => {
  const navItems = [
    { to: "/", label: "Home", icon: <Home className="w-4 h-4" /> },
    { to: "/research", label: "Research", icon: <FlaskConical className="w-4 h-4" /> },
    { to: "/experiments", label: "Experiments", icon: <Layers className="w-4 h-4" /> },
    { to: "/evidence", label: "Evidence", icon: <FileCheck className="w-4 h-4" /> },
    { to: "/reports", label: "Reports", icon: <FileText className="w-4 h-4" /> },
    { to: "/artifacts", label: "Artifacts", icon: <Archive className="w-4 h-4" /> },
    { to: "/evaluation", label: "Quality Center", icon: <ShieldCheck className="w-4 h-4" /> },
    { to: "/settings", label: "Settings", icon: <Settings className="w-4 h-4" /> },
  ];

  return (
    <aside
      className={`w-56 bg-rex-surface border-r border-rex-border flex flex-col justify-between h-screen p-3 select-none flex-shrink-0 ${className}`}
    >
      <div>
        {/* Brand / Logo */}
        <div className="flex items-center gap-2 px-2 py-3 mb-3 border-b border-rex-border/60">
          <div className="w-6 h-6 rounded bg-rex-primary text-rex-bg flex items-center justify-center font-bold font-mono text-xs shadow-xs">
            R
          </div>
          <div>
            <span className="font-bold text-sm tracking-tight text-rex-primary font-mono">REX</span>
            <span className="text-[10px] text-rex-muted ml-1.5 font-medium">Research OS</span>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-rex-elevated text-rex-primary font-semibold shadow-xs"
                    : "text-rex-secondary hover:text-rex-primary hover:bg-rex-elevated/40"
                }`
              }
            >
              {item.icon}
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Autonomous Research Status Mini-Widget */}
      <div className="p-3 bg-rex-elevated/60 border border-rex-border/80 rounded-lg space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider font-semibold text-rex-muted">
            REX Autonomous
          </span>
          <span className="flex h-1.5 w-1.5 relative">
            {isAutonomousRunning && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rex-success opacity-75" />
            )}
            <span
              className={`relative inline-flex rounded-full h-1.5 w-1.5 ${
                isAutonomousRunning ? "bg-rex-success" : "bg-rex-muted"
              }`}
            />
          </span>
        </div>
        <div className="text-xs font-semibold text-rex-primary truncate font-mono">
          {activeRunTitle}
        </div>
        <div className="text-[11px] text-rex-secondary flex items-center gap-1">
          <Activity className="w-3 h-3 text-rex-info" />
          <span>{isAutonomousRunning ? "Loop Active" : "Standby"}</span>
        </div>
      </div>
    </aside>
  );
};
