import React from "react";

interface CardProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
  onClick?: () => void;
}

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  action,
  children,
  footer,
  className = "",
  onClick,
}) => {
  return (
    <div
      onClick={onClick}
      className={`bg-rex-surface border border-rex-border rounded-lg shadow-xs overflow-hidden transition-colors ${
        onClick ? "cursor-pointer hover:border-rex-border/80 hover:bg-rex-elevated/40" : ""
      } ${className}`}
    >
      {(title || action) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-rex-border/60">
          <div>
            {title && <h3 className="text-xs font-semibold text-rex-primary tracking-tight">{title}</h3>}
            {subtitle && <p className="text-[11px] text-rex-muted mt-0.5">{subtitle}</p>}
          </div>
          {action && <div className="flex items-center gap-2">{action}</div>}
        </div>
      )}
      <div className="p-4">{children}</div>
      {footer && (
        <div className="px-4 py-2.5 bg-rex-elevated/40 border-t border-rex-border/60 text-xs text-rex-secondary flex items-center justify-between">
          {footer}
        </div>
      )}
    </div>
  );
};
