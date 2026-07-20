import { PublicLayout, StatusPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "System Status — DrugOS",
  description: "Real-time status of DrugOS backend services.",
};

export default function Page() {
  return (
    <PublicLayout>
      <StatusPage />
    </PublicLayout>
  );
}
