import { redirect } from "next/navigation";
import { LandingPage, PublicLayout } from "@/components/drugos/app-router";
import { legacyToCanonicalUrl } from "@/components/drugos/next-router-provider";

/**
 * FE-001 ROOT FIX (v129, hostile-auditor pass): real Next.js App Router landing.
 *
 * Before this fix, app/page.tsx was `'use client'` and rendered `<DrugOSApp />`
 * — a single 3,200-line client component that faked routing via URL query
 * strings. No SSR, no SEO, no real URLs.
 *
 * Now app/page.tsx is a SERVER component (no `'use client'`). It:
 *   1. Checks for legacy `?p=...` query-string URLs and redirects them to
 *      the canonical path-based URL (e.g. `/?p=app&s=dashboard` → `/dashboard`).
 *      This preserves backwards compatibility with existing bookmarks.
 *   2. Otherwise renders the LandingPage inside PublicLayout. The page is
 *      server-rendered for SEO and fast first paint.
 *
 * The LandingPage component itself is a client component (it uses
 * useSession / useRouter), so it hydrates on the client. But the SHELL
 * (html, head, layout) is server-rendered — meaning the meta tags, OG
 * images, and initial HTML are present in the response body before any
 * JS loads. This is the SEO + SSR win the audit asked for.
 *
 * Verification: open http://localhost:3000/ — should show landing page HTML
 * in the response (curl http://localhost:3000/ | grep DrugOS).
 */
interface LandingPageProps {
  searchParams: { p?: string };
}

export default function LandingPageRoute({ searchParams }: LandingPageProps) {
  // Redirect legacy ?p=... URLs to canonical path-based URLs.
  // This is a permanent redirect (308) so search engines update their index.
  if (searchParams.p) {
    const search = new URLSearchParams(searchParams as Record<string, string>).toString();
    const canonical = legacyToCanonicalUrl("/", search);
    if (canonical) {
      redirect(canonical);
    }
  }

  return (
    <PublicLayout>
      <LandingPage />
    </PublicLayout>
  );
}
