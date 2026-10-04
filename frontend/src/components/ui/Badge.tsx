import React from "react";

export type BadgeVariant =
  | "verified"
  | "supported"
  | "running"
  | "completed"
  | "failed"
  | "unsupported"
  | "warning"
  | "proposed"
  | "neutral";

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant | string;
  size?: "sm" | "md";
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  size = "sm",
  className = "",
}) => {
  const normVariant = (variant || "neutral").toLowerCase();

  let colorClasses = "bg-rex-border/40 text-rex-secondary border-rex-border";

  if (
    normVariant === "verified" ||
    normVariant === "supported" ||
    normVariant === "completed" ||
    normVariant === "pass" ||
    normVariant === "validated"
  ) {
    colorClasses = "bg-rex-success-subtle text-rex-success border-rex-success/30";
  } else if (
    normVariant === "running" ||
    normVariant === "active" ||
    normVariant === "testing" ||
    normVariant === "executing"
  ) {
    colorClasses = "bg-rex-info-subtle text-rex-info border-rex-info/30";
  } else if (
    normVariant === "failed" ||
    normVariant === "tampered" ||
    normVariant === "falsified" ||
    normVariant === "rejected" ||
    normVariant === "fail"
  ) {
    colorClasses = "bg-rex-error-subtle text-rex-error border-rex-error/30";
  } else if (
    normVariant === "unsupported" ||
    normVariant === "warning" ||
    normVariant === "inconclusive"
  ) {
    colorClasses = "bg-rex-warning-subtle text-rex-warning border-rex-warning/30";
  } else if (
    normVariant === "proposed" ||
    normVariant === "draft" ||
    normVariant === "designed" ||
    normVariant === "pending"
  ) {
    colorClasses = "bg-rex-elevated text-rex-muted border-rex-border/60";
  }

  const sizeClasses = size === "sm" ? "text-[11px] px-2 py-0.5" : "text-xs px-2.5 py-1";

  return (
    <span
      className={`inline-flex items-center font-mono font-medium rounded-full border tracking-wide transition-colors ${colorClasses} ${sizeClasses} ${className}`}
    >
      {children}
    </span>
  );
};
