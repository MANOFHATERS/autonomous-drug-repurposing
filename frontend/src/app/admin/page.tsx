import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Admin — DrugOS" };

export default function Page() {
  return (
    <AppShell section="admin">
      <CoreScreenBridge section="admin" />
    </AppShell>
  );
}
