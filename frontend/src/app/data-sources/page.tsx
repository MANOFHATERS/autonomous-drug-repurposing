import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Data Sources — DrugOS" };

export default function Page() {
  return (
    <AppShell section="data-sources">
      <CoreScreenBridge section="data-sources" />
    </AppShell>
  );
}
