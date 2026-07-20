import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Pathways — DrugOS" };

export default function Page() {
  return (
    <AppShell section="pathways">
      <CoreScreenBridge section="pathways" />
    </AppShell>
  );
}
