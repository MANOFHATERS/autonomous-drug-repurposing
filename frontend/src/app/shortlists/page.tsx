import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Shortlists — DrugOS" };

export default function Page() {
  return (
    <AppShell section="shortlists">
      <CoreScreenBridge section="shortlists" />
    </AppShell>
  );
}
