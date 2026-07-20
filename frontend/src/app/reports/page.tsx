import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Reports — DrugOS" };

export default function Page() {
  return (
    <AppShell section="reports">
      <CoreScreenBridge section="reports" />
    </AppShell>
  );
}
