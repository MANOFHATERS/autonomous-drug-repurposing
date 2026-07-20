import { PublicLayout, CaseStudiesPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Case Studies — DrugOS",
  description: "How DrugOS has been used to discover drug repurposing candidates.",
};

export default function Page() {
  return (
    <PublicLayout>
      <CaseStudiesPage />
    </PublicLayout>
  );
}
