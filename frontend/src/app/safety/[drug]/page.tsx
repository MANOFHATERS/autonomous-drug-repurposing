import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

interface PageProps {
  params: Promise<{ drug: string }>;
}

/**
 * FE-001 ROOT FIX (v129): /safety/[drug] — drug-specific safety profile.
 *
 * Renders SafetyProfileScreen which fetches real openFDA adverse event data
 * via /api/safety/[drug].
 */
export default async function Page({ params }: PageProps) {
  const { drug } = await params;
  const drugName = decodeURIComponent(drug);
  return (
    <AppShell section="safety">
      <CoreScreenBridge section="safety" id={drugName} />
    </AppShell>
  );
}
