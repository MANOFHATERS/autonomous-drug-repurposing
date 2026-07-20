import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Drug Interactions — DrugOS" };

export default function Page() {
  return (
    <AppShell section="interactions">
      <CoreScreenBridge section="interactions" />
    </AppShell>
  );
}
