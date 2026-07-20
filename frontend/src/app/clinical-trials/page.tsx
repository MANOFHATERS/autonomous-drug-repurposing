import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Clinical Trials — DrugOS" };

export default function Page() {
  return (
    <AppShell section="clinical-trials">
      <CoreScreenBridge section="clinical-trials" />
    </AppShell>
  );
}
