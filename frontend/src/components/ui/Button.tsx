import React from "react";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = "secondary",
  size = "md",
  isLoading = false,
  leftIcon,
  rightIcon,
  disabled,
  className = "",
  ...props
}) => {
  let variantClasses = "";
  if (variant === "primary") {
    variantClasses =
      "bg-rex-primary text-rex-bg hover:opacity-90 active:scale-[0.99] font-medium shadow-sm border border-transparent";
  } else if (variant === "secondary") {
    variantClasses =
      "bg-rex-surface text-rex-primary hover:bg-rex-elevated border border-rex-border shadow-xs";
  } else if (variant === "outline") {
    variantClasses =
      "bg-transparent text-rex-primary hover:bg-rex-surface border border-rex-border";
  } else if (variant === "ghost") {
    variantClasses =
      "bg-transparent text-rex-secondary hover:text-rex-primary hover:bg-rex-surface/60 border border-transparent";
  } else if (variant === "danger") {
    variantClasses =
      "bg-rex-error text-white hover:bg-rex-error/90 border border-transparent shadow-xs";
  }

  let sizeClasses = "px-3 py-1.5 text-xs h-8";
  if (size === "sm") {
    sizeClasses = "px-2.5 py-1 text-[11px] h-7";
  } else if (size === "lg") {
    sizeClasses = "px-4 py-2 text-sm h-10";
  }

  return (
    <button
      disabled={disabled || isLoading}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-sans transition-all duration-150 focus:outline-none focus:ring-1 focus:ring-rex-accent disabled:opacity-50 disabled:cursor-not-allowed ${variantClasses} ${sizeClasses} ${className}`}
      {...props}
    >
      {isLoading ? (
        <svg
          className="animate-spin -ml-0.5 mr-1.5 h-3.5 w-3.5 text-current"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          ></circle>
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          ></path>
        </svg>
      ) : (
        leftIcon
      )}
      {children}
      {!isLoading && rightIcon}
    </button>
  );
};
