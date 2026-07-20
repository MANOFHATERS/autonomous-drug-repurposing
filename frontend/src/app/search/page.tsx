import { AppShell, AppSearchPage } from "@/components/drugos/app-router";

export const metadata = { title: "Search — DrugOS" };

export default function Page() {
  return (
    <AppShell section="search">
      <AppSearchPage />
    </AppShell>
  );
}
