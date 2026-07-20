import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Profile — DrugOS" };

export default function Page() {
  return (
    <AppShell section="profile">
      <CoreScreenBridge section="profile" />
    </AppShell>
  );
}
