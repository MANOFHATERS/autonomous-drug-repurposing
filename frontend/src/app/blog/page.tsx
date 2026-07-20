import { PublicLayout, BlogPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Blog — DrugOS",
  description: "Updates, research notes, and case studies from the DrugOS team.",
};

export default function Page() {
  return (
    <PublicLayout>
      <BlogPage />
    </PublicLayout>
  );
}
