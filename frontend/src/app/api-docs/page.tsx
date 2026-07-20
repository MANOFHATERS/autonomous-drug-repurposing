import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Api Docs — DrugOS" };

export default function Page() {
  return (
    <AppShell section="api-docs">
      <CoreScreenBridge section="api-docs" />
    </AppShell>
  );
}
