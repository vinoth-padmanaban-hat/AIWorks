import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "system-ui", "sans-serif"],
        display: [
          "var(--font-display)",
          "Cormorant",
          "Georgia",
          "serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      letterSpacing: {
        body: "0.16px",
        "body-lg": "0.18px",
      },
      colors: {
        surface: "var(--surface)",
        "surface-secondary": "var(--surface-secondary)",
        "surface-muted": "var(--surface-muted)",
        "surface-warm": "var(--surface-warm)",
        ink: "var(--ink)",
        "ink-secondary": "var(--ink-secondary)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        "accent-on-accent": "var(--accent-on-accent)",
        "accent-link": "var(--accent-link)",
        border: "var(--border)",
        "border-subtle": "var(--border-subtle)",
        card: "var(--card)",
        "card-elevated": "var(--card-elevated)",
        "ring-focus": "var(--ring-focus)",
      },
      ringColor: {
        focus: "rgb(147 197 253 / 0.5)",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        "elev-outline": "var(--shadow-elev-outline)",
        warm: "rgba(78, 50, 23, 0.04) 0px 6px 16px",
      },
    },
  },
  plugins: [],
};

export default config;
