import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Regulatory Pathway — DrugOS" };

export default function Page() {
  return (
    <AppShell section="regulatory">
      <CoreScreenBridge section="regulatory" />
    </AppShell>
  );
}
