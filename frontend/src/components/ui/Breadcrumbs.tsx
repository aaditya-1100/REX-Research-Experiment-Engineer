import React from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  to?: string;
  isCurrent?: boolean;
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[];
  className?: string;
}

export const Breadcrumbs: React.FC<BreadcrumbsProps> = ({ items, className = "" }) => {
  if (!items || items.length === 0) return null;

  return (
    <nav className={`flex items-center text-xs text-rex-muted gap-1.5 overflow-x-auto ${className}`}>
      {items.map((item, idx) => {
        const isLast = idx === items.length - 1;
        return (
          <React.Fragment key={idx}>
            {idx > 0 && <ChevronRight className="w-3 h-3 text-rex-border flex-shrink-0" />}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                className="hover:text-rex-primary transition-colors whitespace-nowrap"
              >
                {item.label}
              </Link>
            ) : (
              <span
                className={`whitespace-nowrap ${
                  isLast || item.isCurrent
                    ? "font-medium text-rex-primary font-mono"
                    : "text-rex-secondary"
                }`}
              >
                {item.label}
              </span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
};
