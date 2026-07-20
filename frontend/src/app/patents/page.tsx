import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Patents — DrugOS" };

export default function Page() {
  return (
    <AppShell section="patents">
      <CoreScreenBridge section="patents" />
    </AppShell>
  );
}
