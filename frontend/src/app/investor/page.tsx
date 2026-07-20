import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Investor — DrugOS" };

export default function Page() {
  return (
    <AppShell section="investor">
      <CoreScreenBridge section="investor" />
    </AppShell>
  );
}
