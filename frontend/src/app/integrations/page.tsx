import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Integrations — DrugOS" };

export default function Page() {
  return (
    <AppShell section="integrations">
      <CoreScreenBridge section="integrations" />
    </AppShell>
  );
}
