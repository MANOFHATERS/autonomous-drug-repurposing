import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Prediction Explorer — DrugOS" };

export default function Page() {
  return (
    <AppShell section="prediction-explorer">
      <CoreScreenBridge section="prediction-explorer" />
    </AppShell>
  );
}
