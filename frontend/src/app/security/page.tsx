import { PublicLayout, SecurityPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Security — DrugOS",
  description: "DrugOS security architecture, compliance, and data handling.",
};

export default function Page() {
  return (
    <PublicLayout>
      <SecurityPage />
    </PublicLayout>
  );
}
