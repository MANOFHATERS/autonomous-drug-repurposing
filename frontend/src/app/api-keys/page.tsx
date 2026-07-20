import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Api Keys — DrugOS" };

export default function Page() {
  return (
    <AppShell section="api-keys">
      <CoreScreenBridge section="api-keys" />
    </AppShell>
  );
}
