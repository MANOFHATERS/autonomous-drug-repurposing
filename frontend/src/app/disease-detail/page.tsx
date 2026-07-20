import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Disease Detail — DrugOS" };

export default function Page() {
  return (
    <AppShell section="disease-detail">
      <CoreScreenBridge section="disease-detail" />
    </AppShell>
  );
}
