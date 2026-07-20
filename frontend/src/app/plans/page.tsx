import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Plans — DrugOS" };

export default function Page() {
  return (
    <AppShell section="plans">
      <CoreScreenBridge section="plans" />
    </AppShell>
  );
}
