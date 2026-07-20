import { AppShell, AppDashboard } from "@/components/drugos/app-router";

export const metadata = { title: "Dashboard — DrugOS" };

/**
 * FE-001 ROOT FIX (v129): real /dashboard route.
 *
 * Replaces the legacy `/?p=app&s=dashboard` query-string URL with a real
 * Next.js App Router route at `/dashboard`. The AppShell wraps the page
 * with the sidebar + topbar; AppDashboard renders the dashboard content
 * (real usage metrics via useUsageMetrics, real recent queries via
 * useRecentQueries — no fabricated numbers).
 */
export default function Page() {
  return (
    <AppShell section="dashboard">
      <AppDashboard />
    </AppShell>
  );
}
