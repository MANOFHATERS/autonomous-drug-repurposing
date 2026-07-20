import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Team — DrugOS" };

export default function Page() {
  return (
    <AppShell section="team">
      <CoreScreenBridge section="team" />
    </AppShell>
  );
}
