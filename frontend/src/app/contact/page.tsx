import { PublicLayout, ContactPage } from "@/components/drugos/app-router";

export const metadata = {
  title: "Contact — DrugOS",
  description: "Get in touch with the DrugOS team.",
};

export default function Page() {
  return (
    <PublicLayout>
      <ContactPage />
    </PublicLayout>
  );
}
