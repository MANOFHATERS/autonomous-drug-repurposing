import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Invoices — DrugOS" };

export default function Page() {
  return (
    <AppShell section="invoices">
      <CoreScreenBridge section="invoices" />
    </AppShell>
  );
}
