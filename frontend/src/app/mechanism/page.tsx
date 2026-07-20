import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Mechanism of Action — DrugOS" };

export default function Page() {
  return (
    <AppShell section="mechanism">
      <CoreScreenBridge section="mechanism" />
    </AppShell>
  );
}
