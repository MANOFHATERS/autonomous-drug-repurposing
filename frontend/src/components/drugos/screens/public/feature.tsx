'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1500-1622). Public "Feature Deep-Dive" page rendered
// when the route is `features` with a `slug` param. Preserved VERBATIM —
// only the import block at the top is new.

import React from 'react'
import { Search, Network, Shield, FileText, Code, ArrowRight, Target, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useRouter } from '../../next-router-provider'

export function FeaturePage({ slug }: { slug: string }) {
  const { navigate } = useRouter()

  const featureData: Record<string, { title: string; subtitle: string; icon: React.ReactNode; description: string; useCases: string[]; highlights: string[] }> = {
    'disease-search': {
      title: 'Disease Search & Candidate Ranking',
      subtitle: 'Find the best drug repurposing candidates for any disease',
      icon: <Search className="w-8 h-8" />,
      description: 'Search any disease from our database of 7,000+ conditions and instantly receive AI-ranked drug repurposing candidates. Our composite scoring algorithm combines knowledge graph signals, molecular similarity, safety profiles, clinical evidence, and IP status into a single actionable score.',
      useCases: ['Rare disease drug discovery', 'Orphan drug identification', 'Combination therapy exploration', 'Pipeline gap analysis'],
      highlights: ['Composite score with 5 signal types', 'Filter by safety tier, phase, IP status', 'Export results as CSV or PDF', 'Save and compare queries over time'],
    },
    'knowledge-graph': {
      title: 'Knowledge Graph Explorer',
      subtitle: 'Interactive biomedical knowledge graph with 500K+ nodes',
      icon: <Network className="w-8 h-8" />,
      description: 'Explore the DrugOS knowledge graph interactively. Visualize relationships between drugs, diseases, genes, proteins, and pathways. Our graph integrates data from 10+ sources including DrugBank, ChEMBL, OpenTargets, and STRING.',
      useCases: ['Mechanism of action exploration', 'Target identification', 'Pathway analysis', 'Drug-target-disease mapping'],
      highlights: ['5 node types, 8 edge types', 'Evidence-weighted edges', 'Interactive force-directed layout', 'Drill-down from any node'],
    },
    'safety-profiling': {
      title: 'Safety & Off-Target Profiling',
      subtitle: 'Comprehensive safety assessment with contraindication detection',
      icon: <Shield className="w-8 h-8" />,
      description: 'Assess the safety profile of any repurposing candidate with our multi-dimensional safety scoring. Detect contraindications, off-target effects, and drug-drug interactions relevant to the target disease population.',
      useCases: ['Contraindication screening', 'Off-target effect prediction', 'Drug-drug interaction checking', 'Population-specific safety assessment'],
      highlights: ['Green/Yellow/Red safety tiers', 'Contraindication alerts', 'Off-target prediction', 'Population-specific warnings'],
    },
    'evidence-reports': {
      title: 'Evidence Package & Reports',
      subtitle: 'Generate regulatory-grade evidence packages',
      icon: <FileText className="w-8 h-8" />,
      description: 'Assemble comprehensive evidence packages for any drug-disease pair. Generate regulatory-grade reports with full mechanistic pathways, clinical evidence summaries, safety assessments, and IP status — ready for internal review or regulatory submission.',
      useCases: ['Regulatory submission support', 'Internal review packages', 'Grant proposal evidence', 'Investor due diligence'],
      highlights: ['Full mechanistic pathway documentation', 'Clinical evidence synthesis', 'IP and patent landscape', 'GxP validated mode available'],
    },
    'api-developer': {
      title: 'API & Developer Tools',
      subtitle: 'Integrate DrugOS into your workflow with our RESTful API',
      icon: <Code className="w-8 h-8" />,
      description: 'Access DrugOS programmatically with our RESTful API. Search diseases, retrieve candidates, generate reports, and set up webhooks — all from your own applications. SDKs available for Python, R, and JavaScript.',
      useCases: ['Pipeline automation', 'Batch disease querying', 'Custom dashboard integration', 'ML model augmentation'],
      highlights: ['50K+ API calls/day (Professional)', 'Webhooks for async events', 'Python, R, JS SDKs', 'Interactive API playground'],
    },
  }

  const feature = featureData[slug]
  if (!feature) return <div className="p-8 text-center"><h1 className="text-2xl font-bold">Feature not found</h1></div>

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      {/* Hero */}
      <div className="max-w-3xl mb-16">
        <div className="w-16 h-16 rounded-2xl bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center mb-6">{feature.icon}</div>
        <h1 className="text-4xl font-bold text-foreground">{feature.title}</h1>
        <p className="text-xl text-muted-foreground mt-3">{feature.subtitle}</p>
        <p className="text-lg text-muted-foreground mt-6 leading-relaxed">{feature.description}</p>
        <div className="flex items-center gap-4 mt-8">
          <Button size="lg" className="bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'register' })}>
            Get Started <ArrowRight className="w-4 h-4 ml-1" />
          </Button>
          <Button size="lg" variant="outline" onClick={() => navigate({ page: 'contact' })}>Talk to Sales</Button>
        </div>
      </div>

      {/* Screenshot Placeholder */}
      <Card className="mb-16 overflow-hidden">
        <div className="h-64 sm:h-80 bg-gradient-to-br from-[#5B4FCF]/5 to-[#5B4FCF]/10 flex items-center justify-center">
          <div className="text-center">
            <div className="w-16 h-16 text-[#5B4FCF]/30 mx-auto mb-4 flex items-center justify-center">
              {feature?.icon}
            </div>
            <p className="text-muted-foreground">Interactive Demo Preview</p>
          </div>
        </div>
      </Card>

      {/* Use Cases & Highlights */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <Card>
          <CardHeader>
            <CardTitle>Use Cases</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {feature.useCases.map((uc, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Target className="w-4 h-4 text-[#5B4FCF] shrink-0 mt-1" />
                  <span className="text-foreground">{uc}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Key Highlights</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {feature.highlights.map((h, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Check className="w-4 h-4 text-[#1D9E75] shrink-0 mt-1" />
                  <span className="text-foreground">{h}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      {/* Bottom CTA */}
      <div className="mt-16 bg-gradient-to-br from-[#5B4FCF] to-[#7B6FEF] rounded-2xl p-8 sm:p-12 text-center text-white">
        <h2 className="text-2xl sm:text-3xl font-bold">Ready to try {feature.title}?</h2>
        <p className="text-purple-200 mt-3 text-lg">Start free today — no credit card required.</p>
        <div className="flex items-center justify-center gap-4 mt-6">
          <Button size="lg" className="bg-white text-[#5B4FCF] hover:bg-slate-50" onClick={() => navigate({ page: 'register' })}>Start Free</Button>
          <Button size="lg" variant="outline" className="border-white/30 text-white hover:bg-white/10" onClick={() => navigate({ page: 'pricing' })}>View Pricing</Button>
        </div>
      </div>
    </div>
  )
}
