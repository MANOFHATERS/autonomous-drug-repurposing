import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Roadmap — DrugOS" };

export default function Page() {
  return (
    <AppShell section="roadmap">
      <CoreScreenBridge section="roadmap" />
    </AppShell>
  );
}
