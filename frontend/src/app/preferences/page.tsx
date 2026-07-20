import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Preferences — DrugOS" };

export default function Page() {
  return (
    <AppShell section="preferences">
      <CoreScreenBridge section="preferences" />
    </AppShell>
  );
}
