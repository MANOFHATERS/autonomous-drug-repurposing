'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 999-1098). Public "Security" page. Preserved VERBATIM
// — only the import block at the top is new.

import { ShieldCheck, Heart, FileCheck, Globe2, Lock, FileText, Download } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { SectionHeading } from '../_app-layout'

export function SecurityPage() {
  const certifications = [
    { name: 'SOC 2 Type II', desc: 'Annual audit confirming security controls', icon: <ShieldCheck className="w-8 h-8" /> },
    { name: 'HIPAA Compliant', desc: 'Full compliance with BAAs available', icon: <Heart className="w-8 h-8" /> },
    { name: '21 CFR Part 11', desc: 'GxP validated mode for FDA submissions', icon: <FileCheck className="w-8 h-8" /> },
    { name: 'GDPR Compliant', desc: 'EU data protection regulation compliance', icon: <Globe2 className="w-8 h-8" /> },
  ]

  const encryption = [
    { title: 'Data at Rest', desc: 'AES-256 encryption for all stored data' },
    { title: 'Data in Transit', desc: 'TLS 1.3 for all network communications' },
    { title: 'Key Management', desc: 'AWS KMS with customer-managed keys' },
    { title: 'Field-Level Encryption', desc: 'PHI fields encrypted separately' },
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center max-w-3xl mx-auto mb-16">
        <h1 className="text-4xl font-bold text-foreground">Security & Trust</h1>
        <p className="text-lg text-muted-foreground mt-4 leading-relaxed">
          DrugOS is built with security-first principles. Your research data is protected by enterprise-grade encryption, compliance frameworks, and rigorous access controls.
        </p>
      </div>

      {/* Certifications */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-16">
        {certifications.map(cert => (
          <Card key={cert.name} className="text-center hover:shadow-md transition-shadow">
            <CardContent className="pt-6">
              <div className="w-16 h-16 rounded-2xl bg-[#1D9E75]/10 text-[#1D9E75] flex items-center justify-center mx-auto mb-4">
                {cert.icon}
              </div>
              <h3 className="text-lg font-semibold text-foreground">{cert.name}</h3>
              <p className="text-sm text-muted-foreground mt-1">{cert.desc}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Encryption */}
      <SectionHeading title="Encryption & Data Protection" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16">
        {encryption.map(e => (
          <Card key={e.title}>
            <CardContent className="pt-6 flex items-start gap-4">
              <Lock className="w-6 h-6 text-[#5B4FCF] shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-foreground">{e.title}</h3>
                <p className="text-sm text-muted-foreground mt-1">{e.desc}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Data Residency */}
      <SectionHeading title="Data Residency" subtitle="Your data stays where you need it" />
      <Card className="mb-16">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[
              { region: 'US-East', desc: 'Virginia, USA', flag: '🇺🇸' },
              { region: 'EU-West', desc: 'Frankfurt, Germany', flag: '🇩🇪' },
              { region: 'APAC', desc: 'Singapore', flag: '🇸🇬' },
            ].map(r => (
              <div key={r.region} className="flex items-center gap-3 p-4 rounded-xl bg-accent">
                <span className="text-2xl">{r.flag}</span>
                <div>
                  <p className="font-medium text-foreground">{r.region}</p>
                  <p className="text-sm text-muted-foreground">{r.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Resources */}
      <SectionHeading title="Downloadable Resources" />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { name: 'SOC 2 Report', icon: <FileText className="w-5 h-5" /> },
          { name: 'HIPAA BAA Template', icon: <FileText className="w-5 h-5" /> },
          { name: 'Security Whitepaper', icon: <FileText className="w-5 h-5" /> },
          { name: 'Penetration Test Summary', icon: <FileText className="w-5 h-5" /> },
        ].map(r => (
          <Card key={r.name} className="cursor-pointer hover:shadow-md transition-shadow">
            <CardContent className="pt-6 flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center">{r.icon}</div>
              <div>
                <p className="font-medium text-foreground text-sm">{r.name}</p>
                <p className="text-xs text-[#5B4FCF] flex items-center gap-1">Download <Download className="w-3 h-3" /></p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
