import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Changelog — DrugOS" };

export default function Page() {
  return (
    <AppShell section="changelog">
      <CoreScreenBridge section="changelog" />
    </AppShell>
  );
}
