import { AppShell, AppSearchResultsPage } from "@/components/drugos/app-router";

interface PageProps {
  params: Promise<{ query: string }>;
}

/**
 * FE-001 ROOT FIX (v129): real /search/results/[query] route.
 *
 * Replaces the legacy `/?p=app&s=search&sub=results&id=Alzheimer` query-string
 * URL with a real Next.js dynamic route at `/search/results/Alzheimer`.
 *
 * The disease name is URL-decoded by Next.js from the path segment and passed
 * to AppSearchResultsPage, which fetches real RL candidates via useRlCandidates.
 */
export default async function Page({ params }: PageProps) {
  const { query } = await params;
  const diseaseName = decodeURIComponent(query);
  return (
    <AppShell section="search">
      <AppSearchResultsPage diseaseId={diseaseName} />
    </AppShell>
  );
}
