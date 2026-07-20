import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Projects — DrugOS" };

export default function Page() {
  return (
    <AppShell section="projects">
      <CoreScreenBridge section="projects" />
    </AppShell>
  );
}
