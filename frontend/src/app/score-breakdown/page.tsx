import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Score Breakdown — DrugOS" };

export default function Page() {
  return (
    <AppShell section="score-breakdown">
      <CoreScreenBridge section="score-breakdown" />
    </AppShell>
  );
}
