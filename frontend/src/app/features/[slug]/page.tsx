import { PublicLayout, FeaturePage } from "@/components/drugos/app-router";

interface PageProps {
  params: Promise<{ slug: string }>;
}

/**
 * FE-001 ROOT FIX (v129): features/[slug] dynamic route.
 *
 * Replaces the legacy `/?p=features&slug=disease-search` query-string URL
 * with a real Next.js dynamic route `/features/disease-search`.
 */
export default async function Page({ params }: PageProps) {
  const { slug } = await params;
  return (
    <PublicLayout>
      <FeaturePage slug={slug} />
    </PublicLayout>
  );
}
