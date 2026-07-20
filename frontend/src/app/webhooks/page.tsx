import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Webhooks — DrugOS" };

export default function Page() {
  return (
    <AppShell section="webhooks">
      <CoreScreenBridge section="webhooks" />
    </AppShell>
  );
}
