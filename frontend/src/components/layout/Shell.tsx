import React, { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CommandPalette } from "./CommandPalette";
import { MobileNav } from "./MobileNav";
import { BreadcrumbItem } from "../ui/Breadcrumbs";

interface ShellProps {
  breadcrumbs?: BreadcrumbItem[];
  activeRunTitle?: string;
  isAutonomousRunning?: boolean;
}

export const Shell: React.FC<ShellProps> = ({
  breadcrumbs,
  activeRunTitle,
  isAutonomousRunning = true,
}) => {
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-rex-bg text-rex-primary font-sans">
      {/* Desktop / Tablet Sidebar */}
      <Sidebar
        activeRunTitle={activeRunTitle}
        isAutonomousRunning={isAutonomousRunning}
        className="hidden md:flex"
      />

      {/* Main Content Column */}
      <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden">
        {/* Sticky Top Bar */}
        <TopBar
          breadcrumbs={breadcrumbs}
          onOpenCommandPalette={() => setIsCommandPaletteOpen(true)}
        />

        {/* Scrollable Workspace */}
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 pb-20 md:pb-6 focus:outline-none">
          <div className="max-w-7xl mx-auto w-full">
            <Outlet />
          </div>
        </main>

        {/* Mobile Bottom Navigation */}
        <MobileNav />
      </div>

      {/* Global Command Palette */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
      />
    </div>
  );
};
