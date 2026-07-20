import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Audit Logs — DrugOS" };

export default function Page() {
  return (
    <AppShell section="audit-logs">
      <CoreScreenBridge section="audit-logs" />
    </AppShell>
  );
}
