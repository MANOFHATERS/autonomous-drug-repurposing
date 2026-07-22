'use client';

import { Card, CardContent } from '@/components/ui/card';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 27. PRIVACY POLICY SCREEN
// ═══════════════════════════════════════════
export function PrivacyPolicyScreen() {
  const sections = [
    { title: '1. Information We Collect', content: 'DrugOS collects information that you provide directly to us, including your name, email address, organization affiliation, and research interests. We also automatically collect certain information when you use our platform, such as your IP address, browser type, operating system, referring URLs, and information about how you interact with the platform. This includes query logs, page views, and feature usage data that helps us improve our services and provide better drug repurposing insights.' },
    { title: '2. How We Use Your Information', content: 'We use the information we collect to provide, maintain, and improve the DrugOS platform, process your queries and drug repurposing analyses, send you technical notices and support messages, respond to your comments and questions, monitor and analyze trends and usage, detect and prevent fraud, and facilitate contests and promotional activities. Your research data is processed solely to deliver drug repurposing results and is never shared with third parties without explicit consent.' },
    { title: '3. Information Sharing', content: 'DrugOS does not sell, trade, or rent your personal information to third parties. We may share information with your organization administrators as part of team collaboration features, with service providers who assist in operating the platform, and when required by law. All data sharing complies with HIPAA, GDPR, and other applicable regulations.' },
    { title: '4. Data Security', content: 'We implement industry-standard security measures including encryption at rest (AES-256) and in transit (TLS 1.3), role-based access controls, regular security audits, and SOC 2 Type II compliance. We maintain Business Associate Agreements (BAA) for healthcare data processing. Our infrastructure is hosted on HIPAA-compliant cloud providers with multi-region redundancy.' },
    { title: '5. Your Rights', content: 'Under GDPR, you have the right to access, rectify, erase, and port your personal data. You may also object to or restrict certain processing activities. Under CCPA, you have the right to know, delete, and opt-out of the sale of your personal information. We provide self-service tools and support to exercise these rights.' },
    { title: '6. Data Retention', content: 'We retain your personal data for as long as your account is active or as needed to provide services. Query results and research data are retained according to your organization retention policy. You may request deletion of your data at any time through account settings or by contacting our privacy team.' },
  ];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Privacy Policy" desc="Last updated: June 1, 2026" />
      <Card><CardContent className="p-6 max-w-3xl mx-auto"><h2 className="text-xl font-bold mb-6">DrugOS Privacy Policy</h2><div className="space-y-6">{sections.map(s => (<div key={s.title}><h3 className="font-semibold text-lg mb-2">{s.title}</h3><p className="text-sm text-muted-foreground leading-relaxed">{s.content}</p></div>))}</div></CardContent></Card>
    </div></FadeIn>
  );
}
