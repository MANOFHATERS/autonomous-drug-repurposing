import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Billing — DrugOS" };

export default function Page() {
  return (
    <AppShell section="billing">
      <CoreScreenBridge section="billing" />
    </AppShell>
  );
}
