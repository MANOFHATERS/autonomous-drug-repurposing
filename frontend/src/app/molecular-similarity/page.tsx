import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Molecular Similarity — DrugOS" };

export default function Page() {
  return (
    <AppShell section="molecular-similarity">
      <CoreScreenBridge section="molecular-similarity" />
    </AppShell>
  );
}
