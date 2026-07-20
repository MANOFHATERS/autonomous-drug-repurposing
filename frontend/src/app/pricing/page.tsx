import { PublicLayout, PricingPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Pricing — DrugOS",
  description: "Simple, transparent pricing for drug repurposing research.",
};

export default function Page() {
  return (
    <PublicLayout>
      <PricingPage />
    </PublicLayout>
  );
}
