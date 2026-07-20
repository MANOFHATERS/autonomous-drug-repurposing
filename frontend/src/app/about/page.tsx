import { PublicLayout, AboutPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "About — DrugOS",
  description: "Learn about DrugOS, the autonomous drug repurposing platform.",
};

export default function Page() {
  return (
    <PublicLayout>
      <AboutPage />
    </PublicLayout>
  );
}
