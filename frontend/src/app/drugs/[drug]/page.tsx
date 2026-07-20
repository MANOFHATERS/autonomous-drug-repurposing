import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

interface PageProps {
  params: Promise<{ drug: string }>;
}

/**
 * FE-001 ROOT FIX (v129, hostile-auditor pass): /drugs/[drug] — the
 * VERIFICATION TARGET for Task 12.1.
 *
 * Task spec verification:
 *   "Open http://localhost:3000/drugs/aspirin — should show drug detail
 *    page; refresh should preserve URL."
 *
 * Before this fix, the entire app was a single `'use client'` page at `/`
 * that faked routing via URL query strings. Opening `/drugs/aspirin` would
 * 404 — there was no `app/drugs/[drug]/page.tsx` route file.
 *
 * Now `/drugs/aspirin` is a real Next.js App Router dynamic route. The drug
 * name is URL-decoded from the path segment and passed to the
 * CoreScreenBridge which renders the candidate-detail screen via
 * coreScreens['candidate']. Refresh preserves the URL because Next.js
 * App Router handles the path natively.
 *
 * The AppShell wraps the page with sidebar + topbar (auth-guarded via
 * useSession inside AppShell).
 */
export default async function Page({ params }: PageProps) {
  const { drug } = await params;
  const drugName = decodeURIComponent(drug);
  return (
    <AppShell section="drugs">
      <CoreScreenBridge section="candidate" id={drugName} />
    </AppShell>
  );
}
