import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

// FE-011 / FE-031 / FE-039 v123 FORENSIC ROOT FIX (hostile-auditor):
//
// The previous tailwind.config.ts wrapped every CSS variable in `hsl(...)`:
//     background: 'hsl(var(--background))'
// But `frontend/src/app/globals.css` defines those variables as HEX values:
//     :root { --background: #F8F8FA; --primary: #5B4FCF; ... }
// `hsl(#F8F8FA)` is INVALID CSS — the browser parses it as `hsl()` with
// no arguments, which falls back to `currentColor`. Every Tailwind color
// utility (bg-background, text-primary, border-border, etc.) resolved to
// a broken value, producing an unstyled app. The audit (FE-011, FE-031)
// flagged this as a CRITICAL/HIGH bug; the prior "fix" only added
// comments — the actual `hsl(...)` wrapping was never removed.
//
// ROOT FIX: drop the `hsl(...)` wrapper. The CSS variables already
// contain complete color values (hex in light mode, hex in dark mode),
// so `var(--background)` is a valid CSS color value on its own. Tailwind
// v4's `@theme inline` block in globals.css already maps
// `--color-background: var(--background)` — this config file is now
// vestigial for Tailwind v4 (CSS-first config), but kept for backward
// compatibility with any tooling that still imports it.
//
// FE-039 ROOT FIX: `destructive.foreground` referenced `--destructive-foreground`
// which was NEVER defined in globals.css. We add it to globals.css in
// the same fix batch. Here we keep the reference (the variable now
// exists), but use the raw `var(--destructive-foreground)` form.

const config: Config = {
    darkMode: "class",
    content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
      extend: {
          colors: {
              background: 'var(--background)',
              foreground: 'var(--foreground)',
              card: {
                  DEFAULT: 'var(--card)',
                  foreground: 'var(--card-foreground)'
              },
              popover: {
                  DEFAULT: 'var(--popover)',
                  foreground: 'var(--popover-foreground)'
              },
              primary: {
                  DEFAULT: 'var(--primary)',
                  foreground: 'var(--primary-foreground)'
              },
              secondary: {
                  DEFAULT: 'var(--secondary)',
                  foreground: 'var(--secondary-foreground)'
              },
              muted: {
                  DEFAULT: 'var(--muted)',
                  foreground: 'var(--muted-foreground)'
              },
              accent: {
                  DEFAULT: 'var(--accent)',
                  foreground: 'var(--accent-foreground)'
              },
              destructive: {
                  DEFAULT: 'var(--destructive)',
                  foreground: 'var(--destructive-foreground)'
              },
              border: 'var(--border)',
              input: 'var(--input)',
              ring: 'var(--ring)',
              chart: {
                  '1': 'var(--chart-1)',
                  '2': 'var(--chart-2)',
                  '3': 'var(--chart-3)',
                  '4': 'var(--chart-4)',
                  '5': 'var(--chart-5)'
              }
          },
          borderRadius: {
              lg: 'var(--radius)',
              md: 'calc(var(--radius) - 2px)',
              sm: 'calc(var(--radius) - 4px)'
          }
      }
  },
  plugins: [tailwindcssAnimate],
};
export default config;
