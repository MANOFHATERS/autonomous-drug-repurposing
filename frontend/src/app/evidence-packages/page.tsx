import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Evidence Packages — DrugOS" };

export default function Page() {
  return (
    <AppShell section="evidence-packages">
      <CoreScreenBridge section="evidence-packages" />
    </AppShell>
  );
}
