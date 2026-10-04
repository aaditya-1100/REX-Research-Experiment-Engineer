import React from "react";
import { Search, Sun, Moon, Bell } from "lucide-react";
import { useTheme } from "../../context/ThemeContext";
import { BreadcrumbItem, Breadcrumbs } from "../ui/Breadcrumbs";

interface TopBarProps {
  breadcrumbs?: BreadcrumbItem[];
  onOpenCommandPalette: () => void;
  className?: string;
}

export const TopBar: React.FC<TopBarProps> = ({
  breadcrumbs = [],
  onOpenCommandPalette,
  className = "",
}) => {
  const { theme, toggleTheme } = useTheme();

  return (
    <header
      className={`h-13 bg-rex-surface border-b border-rex-border px-5 flex items-center justify-between select-none ${className}`}
    >
      {/* Left: Breadcrumbs */}
      <div className="flex items-center min-w-0 flex-1 mr-4">
        <Breadcrumbs items={breadcrumbs} />
      </div>

      {/* Right: Search / Palette, Theme Toggle, Profile */}
      <div className="flex items-center gap-2.5">
        {/* Command Palette Trigger */}
        <button
          onClick={onOpenCommandPalette}
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-rex-elevated/70 border border-rex-border text-xs text-rex-muted hover:text-rex-primary hover:border-rex-border/80 transition-colors w-48 sm:w-64 justify-between"
        >
          <div className="flex items-center gap-1.5 truncate">
            <Search className="w-3.5 h-3.5" />
            <span className="truncate">Search or command...</span>
          </div>
          <kbd className="hidden sm:inline-block px-1.5 py-0.5 text-[10px] font-mono bg-rex-surface border border-rex-border rounded text-rex-secondary">
            ⌘K
          </kbd>
        </button>

        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          className="p-1.5 rounded-md text-rex-secondary hover:text-rex-primary hover:bg-rex-elevated transition-colors border border-transparent hover:border-rex-border/60"
        >
          {theme === "dark" ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-700" />}
        </button>

        {/* Notifications */}
        <button
          title="Notifications"
          className="p-1.5 rounded-md text-rex-secondary hover:text-rex-primary hover:bg-rex-elevated transition-colors border border-transparent hover:border-rex-border/60 relative"
        >
          <Bell className="w-4 h-4" />
          <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-rex-accent" />
        </button>

        {/* User Avatar */}
        <div
          title="Aaditya — REX HQ Lead"
          className="w-7 h-7 rounded-full bg-rex-elevated border border-rex-border flex items-center justify-center text-xs font-semibold text-rex-primary font-mono cursor-pointer hover:border-rex-primary/40 transition-colors"
        >
          A
        </div>
      </div>
    </header>
  );
};
