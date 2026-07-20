import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Feedback — DrugOS" };

export default function Page() {
  return (
    <AppShell section="feedback">
      <CoreScreenBridge section="feedback" />
    </AppShell>
  );
}
