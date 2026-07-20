import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Literature — DrugOS" };

export default function Page() {
  return (
    <AppShell section="literature">
      <CoreScreenBridge section="literature" />
    </AppShell>
  );
}
