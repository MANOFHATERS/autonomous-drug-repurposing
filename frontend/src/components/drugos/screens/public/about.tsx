'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 914-993). Public "About" page. Preserved VERBATIM —
// only the import block at the top is new.

import { ArrowRight } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { SectionHeading } from '../_app-layout'

export function AboutPage() {
  const team = [
    { name: 'Manoj Builder', role: 'CEO & Co-Founder', desc: 'Former pharma data scientist. 15+ years in drug discovery.' },
    { name: 'Rohan Analyst', role: 'CTO & Co-Founder', desc: 'ML engineer with deep expertise in graph neural networks.' },
    { name: 'Aseem Hustler', role: 'COO & Co-Founder', desc: 'Serial operator. Built and scaled B2B SaaS companies.' },
  ]

  const milestones = [
    { year: '2024', event: 'DrugOS founded with a mission to democratize drug repurposing' },
    { year: '2025', event: 'Launched MVP with 5,000 drugs and knowledge graph v1' },
    { year: '2025', event: 'First validated prediction confirmed by wet-lab results' },
    { year: '2026', event: 'Series A funding. 10,000+ drugs, enterprise customers onboarded' },
    { year: '2026', event: 'GxP validated mode, HIPAA compliance, Discovery Deal launched' },
  ]

  const press = [
    { outlet: 'Nature Biotechnology', title: 'AI Drug Repurposing Platform Identifies Novel Candidates' },
    { outlet: 'TechCrunch', title: 'DrugOS Raises Series A to Accelerate Drug Repurposing' },
    { outlet: 'STAT News', title: 'New Platform Promises Faster Path to Rare Disease Treatments' },
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      {/* Mission */}
      <div className="text-center max-w-3xl mx-auto mb-16">
        <h1 className="text-4xl font-bold text-foreground">Building the future of drug repurposing</h1>
        <p className="text-lg text-muted-foreground mt-4 leading-relaxed">
          We believe every disease deserves a chance at a cure. DrugOS uses AI to systematically explore the universe of approved drugs,
          finding new therapeutic uses faster and cheaper than traditional drug discovery. Our mission is to democratize access to
          life-saving treatments — especially for rare and neglected diseases.
        </p>
      </div>

      {/* Team */}
      <SectionHeading title="Leadership Team" subtitle="The people behind DrugOS" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-16">
        {team.map(member => (
          <Card key={member.name} className="hover:shadow-md transition-shadow">
            <CardContent className="pt-6 text-center">
              <Avatar className="w-20 h-20 mx-auto mb-4">
                <AvatarFallback className="bg-[#5B4FCF] text-white text-2xl">{member.name.split(' ').map(n => n[0]).join('')}</AvatarFallback>
              </Avatar>
              <h3 className="text-lg font-semibold text-foreground">{member.name}</h3>
              <p className="text-sm text-[#5B4FCF] font-medium">{member.role}</p>
              <p className="text-sm text-muted-foreground mt-2">{member.desc}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Timeline */}
      <SectionHeading title="Milestones" subtitle="Our journey so far" />
      <div className="relative mb-16 pl-8 border-l-2 border-[#5B4FCF]/20 space-y-8 max-w-2xl">
        {milestones.map(m => (
          <div key={m.year + m.event} className="relative">
            <div className="absolute -left-[2.55rem] w-4 h-4 rounded-full bg-[#5B4FCF] border-4 border-[#F8F8FA]" />
            <span className="text-sm font-bold text-[#5B4FCF]">{m.year}</span>
            <p className="text-foreground mt-0.5">{m.event}</p>
          </div>
        ))}
      </div>

      {/* Press */}
      <SectionHeading title="In the News" subtitle="What they're saying about DrugOS" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {press.map(article => (
          <Card key={article.title} className="hover:shadow-md transition-shadow cursor-pointer">
            <CardContent className="pt-6">
              <Badge variant="secondary" className="mb-3">{article.outlet}</Badge>
              <h3 className="font-semibold text-foreground leading-snug">{article.title}</h3>
              <p className="text-sm text-[#5B4FCF] mt-3 flex items-center gap-1">
                Read more <ArrowRight className="w-3 h-3" />
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
