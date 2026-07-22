'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1411-1494). Public "Case Studies" page. Preserved
// VERBATIM — only the import block at the top is new.

import { Check } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

export function CaseStudiesPage() {
  const studies = [
    {
      type: 'Academic Research',
      org: 'University Neuroscience Lab',
      disease: "Huntington's Disease",
      outcomes: ['Identified 3 novel candidates in 2 weeks', 'Validated Memantine + Riluzole combination', 'Published in Nature Communications'],
      metrics: { time: '2 weeks', candidates: '10', topScore: '87' },
      quote: '"DrugOS compressed 6 months of literature review into 2 weeks of computational analysis."',
      author: '— Dr. Priya Sharma, Principal Investigator'
    },
    {
      type: 'Biotech Startup',
      org: 'NeuroGen Therapeutics',
      disease: 'ALS (Lou Gehrig\'s Disease)',
      outcomes: ['Discovered 5 repurposing candidates', 'Filed 2 provisional patents', 'Raised $12M Series A'],
      metrics: { time: '1 month', candidates: '12', topScore: '82' },
      quote: '"The evidence packages from DrugOS were instrumental in securing our Series A funding."',
      author: '— James Miller, CTO'
    },
    {
      type: 'Pharmaceutical Company',
      org: 'Top-10 Pharma',
      disease: "Pancreatic Cancer",
      outcomes: ['Prioritized 3 lead candidates', 'Advanced 1 to Phase II', 'Reduced discovery cost by 85%'],
      metrics: { time: '3 months', candidates: '8', topScore: '79' },
      quote: '"DrugOS transformed our early-stage pipeline strategy with data-driven insights."',
      author: '— VP of Drug Discovery'
    },
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-foreground">Case Studies</h1>
        <p className="text-lg text-muted-foreground mt-3 max-w-2xl mx-auto">
          See how researchers and companies are using DrugOS to accelerate drug repurposing.
        </p>
      </div>

      <div className="space-y-8">
        {studies.map(study => (
          <Card key={study.org} className="overflow-hidden hover:shadow-lg transition-shadow">
            <CardContent className="pt-6">
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <div className="lg:col-span-2">
                  <div className="flex items-center gap-2 mb-3">
                    <Badge className="bg-[#5B4FCF] text-white">{study.type}</Badge>
                    <span className="text-sm text-muted-foreground">{study.org}</span>
                  </div>
                  <h3 className="text-xl font-bold text-foreground mb-1">{study.disease}</h3>
                  <ul className="space-y-2 mt-4">
                    {study.outcomes.map((o, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <Check className="w-4 h-4 text-[#1D9E75] shrink-0 mt-0.5" />
                        <span className="text-foreground">{o}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-6 p-4 bg-accent rounded-xl">
                    <p className="text-sm italic text-muted-foreground">{study.quote}</p>
                    <p className="text-sm font-medium text-foreground mt-2">{study.author}</p>
                  </div>
                </div>
                <div className="space-y-4">
                  {[
                    { label: 'Time to Results', value: study.metrics.time },
                    { label: 'Candidates Found', value: study.metrics.candidates },
                    { label: 'Top Score', value: study.metrics.topScore },
                  ].map(m => (
                    <div key={m.label} className="p-4 rounded-xl bg-accent text-center">
                      <p className="text-2xl font-bold text-[#5B4FCF]">{m.value}</p>
                      <p className="text-sm text-muted-foreground">{m.label}</p>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
