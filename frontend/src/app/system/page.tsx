import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "System — DrugOS" };

export default function Page() {
  return (
    <AppShell section="system">
      <CoreScreenBridge section="system" />
    </AppShell>
  );
}
