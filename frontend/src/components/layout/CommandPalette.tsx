import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search,
  FlaskConical,
  Layers,
  FileCheck,
  FileText,
  Play,
  Pause,
  Sun,
  Moon,
  PlusCircle,
  GitCompare,
  ShieldCheck,
} from "lucide-react";
import { useTheme } from "../../context/ThemeContext";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

interface CommandItem {
  id: string;
  category: "Research" | "Experiments" | "Evidence" | "Reports" | "System";
  label: string;
  sublabel?: string;
  shortcut?: string;
  icon: React.ReactNode;
  action: () => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({ isOpen, onClose }) => {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();

  const commands: CommandItem[] = [
    // Research
    {
      id: "new-research",
      category: "Research",
      label: "New research run",
      sublabel: "Start a new autonomous investigation",
      shortcut: "⌘ R",
      icon: <PlusCircle className="w-4 h-4 text-rex-accent" />,
      action: () => {
        navigate("/research");
        onClose();
      },
    },
    {
      id: "view-research-list",
      category: "Research",
      label: "Find research runs",
      sublabel: "Browse active and archived runs",
      shortcut: "⌘ F",
      icon: <FlaskConical className="w-4 h-4 text-rex-info" />,
      action: () => {
        navigate("/research");
        onClose();
      },
    },

    // Experiments
    {
      id: "list-experiments",
      category: "Experiments",
      label: "Search experiments",
      sublabel: "Inspect executions, logs, and artifacts",
      shortcut: "⌘ E",
      icon: <Layers className="w-4 h-4 text-rex-success" />,
      action: () => {
        navigate("/experiments");
        onClose();
      },
    },
    {
      id: "compare-experiments",
      category: "Experiments",
      label: "Compare experiments",
      sublabel: "Side-by-side metric overlay chart",
      shortcut: "⌘ C",
      icon: <GitCompare className="w-4 h-4 text-rex-warning" />,
      action: () => {
        navigate("/experiments/compare");
        onClose();
      },
    },

    // Evidence
    {
      id: "evidence-claims",
      category: "Evidence",
      label: "Find scientific claims",
      sublabel: "Inspect claims and evidence status",
      shortcut: "⌘ K",
      icon: <FileCheck className="w-4 h-4 text-rex-accent" />,
      action: () => {
        navigate("/evidence");
        onClose();
      },
    },
    {
      id: "evidence-verification",
      category: "Evidence",
      label: "Run verification audit",
      sublabel: "Formal 12-step cryptographic check",
      shortcut: "⌘ V",
      icon: <ShieldCheck className="w-4 h-4 text-rex-success" />,
      action: () => {
        navigate("/evidence");
        onClose();
      },
    },

    // Reports
    {
      id: "view-reports",
      category: "Reports",
      label: "Open research reports",
      sublabel: "Evidence-grounded synthesized papers",
      shortcut: "⌘ P",
      icon: <FileText className="w-4 h-4 text-rex-primary" />,
      action: () => {
        navigate("/reports");
        onClose();
      },
    },

    // System
    {
      id: "toggle-theme",
      category: "System",
      label: `Switch to ${theme === "dark" ? "Light" : "Dark"} theme`,
      sublabel: "Toggle interface appearance",
      shortcut: "⌘ T",
      icon: theme === "dark" ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-700" />,
      action: () => {
        toggleTheme();
        onClose();
      },
    },
  ];

  const filtered = commands.filter(
    (c) =>
      c.label.toLowerCase().includes(query.toLowerCase()) ||
      c.category.toLowerCase().includes(query.toLowerCase()) ||
      (c.sublabel && c.sublabel.toLowerCase().includes(query.toLowerCase()))
  );

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        // Toggle palette
        if (isOpen) onClose();
      }
      if (!isOpen) return;

      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev < filtered.length - 1 ? prev + 1 : 0));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : filtered.length - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filtered[selectedIndex]) {
          filtered[selectedIndex].action();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, filtered, selectedIndex, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto flex items-start justify-center pt-20 p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      {/* Palette Container */}
      <div className="relative z-10 w-full max-w-xl bg-rex-surface border border-rex-border rounded-xl shadow-2xl overflow-hidden flex flex-col">
        {/* Search Input Bar */}
        <div className="flex items-center px-4 py-3 border-b border-rex-border/80 gap-3">
          <Search className="w-4 h-4 text-rex-muted" />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command or search..."
            className="flex-1 bg-transparent text-sm text-rex-primary placeholder-rex-muted focus:outline-none"
          />
          <kbd className="px-1.5 py-0.5 text-[10px] font-mono bg-rex-elevated border border-rex-border rounded text-rex-muted">
            ESC
          </kbd>
        </div>

        {/* Results List */}
        <div className="max-h-80 overflow-y-auto p-2 space-y-1">
          {filtered.length === 0 ? (
            <div className="py-8 text-center text-xs text-rex-muted">
              No matching commands or navigation paths found.
            </div>
          ) : (
            filtered.map((item, index) => {
              const isSelected = index === selectedIndex;
              return (
                <div
                  key={item.id}
                  onClick={() => item.action()}
                  onMouseEnter={() => setSelectedIndex(index)}
                  className={`flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer transition-colors text-xs ${
                    isSelected ? "bg-rex-elevated text-rex-primary" : "text-rex-secondary hover:text-rex-primary"
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="p-1 rounded bg-rex-surface border border-rex-border/60">
                      {item.icon}
                    </div>
                    <div className="truncate">
                      <div className="font-medium text-rex-primary">{item.label}</div>
                      {item.sublabel && (
                        <div className="text-[11px] text-rex-muted truncate">{item.sublabel}</div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-rex-muted">
                      {item.category}
                    </span>
                    {item.shortcut && (
                      <kbd className="px-1.5 py-0.5 text-[10px] font-mono bg-rex-surface border border-rex-border rounded text-rex-muted">
                        {item.shortcut}
                      </kbd>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer shortcuts */}
        <div className="px-4 py-2 border-t border-rex-border/60 bg-rex-elevated/40 text-[11px] text-rex-muted flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span>↑↓ Navigate</span>
            <span>↵ Select</span>
            <span>ESC Close</span>
          </div>
          <span className="font-mono">REX OS v0.8.0</span>
        </div>
      </div>
    </div>
  );
};
