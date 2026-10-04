import React, { useEffect } from "react";
import { X } from "lucide-react";

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
}

export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = "max-w-md",
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden flex justify-end">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity duration-200"
        onClick={onClose}
      />

      {/* Slide-over panel */}
      <div
        className={`relative z-10 w-full ${width} bg-rex-surface border-l border-rex-border shadow-2xl flex flex-col h-full transform transition-transform duration-200 ease-out`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-rex-border/80 bg-rex-elevated/40">
          <div>
            <h2 className="text-sm font-semibold text-rex-primary tracking-tight">{title}</h2>
            {subtitle && <p className="text-xs text-rex-muted mt-0.5">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-rex-muted hover:text-rex-primary hover:bg-rex-elevated transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="px-5 py-3 border-t border-rex-border bg-rex-elevated/40 flex items-center justify-end gap-2">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};
