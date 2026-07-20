import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Users — DrugOS" };

export default function Page() {
  return (
    <AppShell section="users">
      <CoreScreenBridge section="users" />
    </AppShell>
  );
}
