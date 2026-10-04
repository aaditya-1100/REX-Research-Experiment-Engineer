/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        rex: {
          bg: "var(--color-bg)",
          surface: "var(--color-surface)",
          elevated: "var(--color-surface-elevated)",
          border: "var(--color-border)",
          "border-subtle": "var(--color-border-subtle)",
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          muted: "var(--color-text-muted)",
          success: "var(--color-success)",
          "success-subtle": "var(--color-success-subtle)",
          warning: "var(--color-warning)",
          "warning-subtle": "var(--color-warning-subtle)",
          error: "var(--color-error)",
          "error-subtle": "var(--color-error-subtle)",
          info: "var(--color-info)",
          "info-subtle": "var(--color-info-subtle)",
          accent: "var(--color-accent)",
          "accent-subtle": "var(--color-accent-subtle)",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
}
