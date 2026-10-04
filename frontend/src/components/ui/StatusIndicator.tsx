import React from "react";

interface StatusIndicatorProps {
  status: string;
  label?: string;
  showDot?: boolean;
  className?: string;
}

export const StatusIndicator: React.FC<StatusIndicatorProps> = ({
  status,
  label,
  showDot = true,
  className = "",
}) => {
  const norm = (status || "").toLowerCase();

  let dotColor = "bg-rex-muted";
  let textColor = "text-rex-secondary";
  let isPulsing = false;

  if (norm === "running" || norm === "active" || norm === "testing" || norm === "executing") {
    dotColor = "bg-rex-info";
    textColor = "text-rex-info font-medium";
    isPulsing = true;
  } else if (
    norm === "completed" ||
    norm === "complete" ||
    norm === "verified" ||
    norm === "pass" ||
    norm === "supported"
  ) {
    dotColor = "bg-rex-success";
    textColor = "text-rex-success font-medium";
  } else if (
    norm === "failed" ||
    norm === "fail" ||
    norm === "error" ||
    norm === "tampered" ||
    norm === "falsified"
  ) {
    dotColor = "bg-rex-error";
    textColor = "text-rex-error font-medium";
  } else if (norm === "warning" || norm === "unsupported" || norm === "inconclusive") {
    dotColor = "bg-rex-warning";
    textColor = "text-rex-warning font-medium";
  }

  return (
    <div className={`inline-flex items-center gap-1.5 text-xs ${className}`}>
      {showDot && (
        <span className="relative flex h-2 w-2">
          {isPulsing && (
            <span
              className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${dotColor}`}
            />
          )}
          <span className={`relative inline-flex rounded-full h-2 w-2 ${dotColor}`} />
        </span>
      )}
      <span className={textColor}>{label || status}</span>
    </div>
  );
};
