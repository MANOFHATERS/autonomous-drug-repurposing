'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1344-1405). Public "Careers" page. Preserved VERBATIM
// — only the import block at the top is new.
//
// The module-level `careers` constant was defined at the top of
// app-router.tsx (lines 129-133) but used only by CareersPage. Moved here
// as a local declaration per hostile-auditor rule 4. The FE-065 comment
// that explained the placeholder has been preserved verbatim.

import { Heart, Globe, GraduationCap, Briefcase, MapPin, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useRouter } from '../../next-router-provider'
import { SectionHeading } from '../_app-layout'

// FE-065: Empty placeholder — integrate a CMS or ATS for job listings.
const careers: Array<{
  id: string; title: string; location: string; type: string
  department: string; postedAt: string
}> = []

export function CareersPage() {
  const { navigate } = useRouter()
  const benefits = [
    { icon: <Heart className="w-5 h-5" />, title: 'Health & Wellness', desc: 'Comprehensive medical, dental, vision' },
    { icon: <Globe className="w-5 h-5" />, title: 'Remote-First', desc: 'Work from anywhere in the world' },
    { icon: <GraduationCap className="w-5 h-5" />, title: 'Learning Budget', desc: '$5,000/year for conferences & courses' },
    { icon: <Briefcase className="w-5 h-5" />, title: 'Equity', desc: 'Stock options for all team members' },
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-foreground">Join the Team</h1>
        <p className="text-lg text-muted-foreground mt-3 max-w-2xl mx-auto">
          Help us build the future of drug repurposing. We're looking for passionate people who want to make a real impact on human health.
        </p>
      </div>

      {/* Culture */}
      <div className="bg-gradient-to-br from-[#5B4FCF] to-[#7B6FEF] rounded-2xl p-8 sm:p-12 text-white mb-16">
        <h2 className="text-2xl font-bold mb-4">Our Culture</h2>
        <p className="text-purple-200 text-lg max-w-2xl leading-relaxed">
          We move fast, think rigorously, and care deeply. Every line of code and every model inference could lead to a life-saving treatment.
          We take that responsibility seriously — and we have fun doing it.
        </p>
      </div>

      {/* Benefits */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-16">
        {benefits.map(b => (
          <Card key={b.title} className="hover:shadow-md transition-shadow">
            <CardContent className="pt-6">
              <div className="w-10 h-10 rounded-lg bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center mb-3">{b.icon}</div>
              <h3 className="font-semibold text-foreground">{b.title}</h3>
              <p className="text-sm text-muted-foreground mt-1">{b.desc}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Job Listings */}
      <SectionHeading title="Open Positions" subtitle="Find your next role" />
      <div className="space-y-4">
        {careers.map(job => (
          <Card key={job.id} className="hover:shadow-md transition-shadow">
            <CardContent className="pt-6 flex items-center justify-between flex-wrap gap-4">
              <div>
                <h3 className="font-semibold text-foreground text-lg">{job.title}</h3>
                <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
                  <span className="flex items-center gap-1"><MapPin className="w-3.5 h-3.5" />{job.location}</span>
                  <span className="flex items-center gap-1"><Clock className="w-3.5 h-3.5" />{job.type}</span>
                  <Badge variant="secondary">{job.department}</Badge>
                </div>
              </div>
              <Button className="bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'contact' })}>Apply Now</Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
