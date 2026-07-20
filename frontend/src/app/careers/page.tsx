import { PublicLayout, CareersPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Careers — DrugOS",
  description: "Join the DrugOS team and help reshape drug repurposing.",
};

export default function Page() {
  return (
    <PublicLayout>
      <CareersPage />
    </PublicLayout>
  );
}
