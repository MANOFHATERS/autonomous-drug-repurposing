'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 454-728). Public marketing landing page. Preserved
// VERBATIM — only the import block at the top is new.
//
// The two module-level constants below (`diseases`, `trendingDiseases`)
// were defined at the top of app-router.tsx (lines 92-109) but used only
// by LandingPage. Moved here as local declarations per hostile-auditor
// rule 4. The FE-065 comments that explained each placeholder have been
// preserved verbatim alongside the constants.

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, Network, Shield, FileText, Code, Zap, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useRouter } from '../../next-router-provider'
import { SectionHeading } from '../_app-layout'
import type { Disease } from '@/lib/types'

// FE-065: Empty placeholder — replace with useDiseaseSearch(query) hook.
const diseases: Disease[] = []

// FE-065: Empty placeholder — derive from api.getRankedHypotheses() for
// live trending disease data.
const trendingDiseases: Array<{
  id: string; name: string; queries: number; candidates: number
  trend: string; change?: string
}> = []

export function LandingPage() {
  const { navigate } = useRouter()
  const [searchQuery, setSearchQuery] = useState('')
  const [showAutocomplete, setShowAutocomplete] = useState(false)
  const [selectedDisease, setSelectedDisease] = useState<string | null>(null)

  const filteredDiseases = useMemo(() => {
    if (!searchQuery || searchQuery.length < 2) return []
    return diseases.filter(d =>
      d.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.therapeuticArea.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.icdCode.toLowerCase().includes(searchQuery.toLowerCase())
    ).slice(0, 6)
  }, [searchQuery])

  const handleSearch = () => {
    if (selectedDisease) {
      navigate({ page: 'app', section: 'search', sub: 'results', id: selectedDisease })
    } else if (filteredDiseases.length > 0) {
      navigate({ page: 'app', section: 'search', sub: 'results', id: filteredDiseases[0].id })
    }
  }

  const features = [
    { icon: <Search className="w-6 h-6" />, title: 'Disease Search & Candidate Ranking', desc: 'Search any disease and get AI-ranked drug repurposing candidates with composite scores.', slug: 'disease-search' },
    { icon: <Network className="w-6 h-6" />, title: 'Knowledge Graph Explorer', desc: 'Interactive biomedical knowledge graph with 500K+ nodes and 6M+ edges.', slug: 'knowledge-graph' },
    { icon: <Shield className="w-6 h-6" />, title: 'Safety & Off-Target Profiling', desc: 'Comprehensive safety assessment with contraindication detection.', slug: 'safety-profiling' },
    { icon: <FileText className="w-6 h-6" />, title: 'Evidence Package & Reports', desc: 'Generate regulatory-grade evidence packages with full mechanistic pathways.', slug: 'evidence-reports' },
    { icon: <Code className="w-6 h-6" />, title: 'API & Developer Tools', desc: 'RESTful API with 50K+ daily calls, webhooks, and SDK support.', slug: 'api-developer' },
  ]

  const steps = [
    { num: '01', title: 'Knowledge Graph', desc: '5 node types, 8 edge types, 500K+ nodes from 10+ data sources' },
    { num: '02', title: 'Graph Transformer', desc: 'Heterogeneous GNN scores every drug-disease pair' },
    { num: '03', title: 'Composite Scoring', desc: 'KG + molecular similarity + safety + clinical + IP signals' },
    { num: '04', title: 'Explainable Reports', desc: 'Full mechanistic pathways and evidence packages' },
  ]

  const logos = ['Pfizer', 'Novartis', 'Roche', 'AstraZeneca', 'Biogen', 'Merck']

  return (
    <div>
      {/* Hero Section */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#5B4FCF]/5 via-transparent to-transparent" />
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-16 sm:pt-24 pb-20">
          <div className="max-w-3xl mx-auto text-center">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
              <Badge variant="secondary" className="mb-6 px-3 py-1 text-sm bg-[#5B4FCF]/10 text-[#5B4FCF] border-[#5B4FCF]/20">
                <Zap className="w-3.5 h-3.5 mr-1" /> Now with GxP Validated Mode
              </Badge>
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-foreground leading-tight tracking-tight">
                Find new treatments<br />
                <span className="text-[#5B4FCF]">for any disease.</span> Instantly.
              </h1>
              <p className="mt-6 text-lg sm:text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
                DrugOS uses AI to systematically mine 10,000+ FDA-approved drugs against every known disease using a multi-modal biomedical knowledge graph.
              </p>
            </motion.div>

            {/* Search Bar */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
              className="mt-10 max-w-2xl mx-auto"
            >
              <div className="relative">
                <div className="flex items-center bg-white rounded-2xl shadow-xl shadow-slate-200/60 border border-border p-2">
                  <Search className="w-5 h-5 text-muted-foreground ml-3 mr-2 shrink-0" />
                  <input
                    value={searchQuery}
                    onChange={(e) => { setSearchQuery(e.target.value); setShowAutocomplete(true); setSelectedDisease(null) }}
                    onFocus={() => setShowAutocomplete(true)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="Search for a disease — e.g. Huntington's, Alzheimer's, ALS..."
                    className="flex-1 py-3 text-base bg-transparent border-none outline-none placeholder:text-muted-foreground/60"
                  />
                  <Button onClick={handleSearch} className="bg-[#5B4FCF] hover:bg-[#4B3FBF] px-6 py-3 rounded-xl text-base">
                    Search
                  </Button>
                </div>

                {/* Autocomplete Dropdown */}
                <AnimatePresence>
                  {showAutocomplete && filteredDiseases.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }}
                      className="absolute top-full mt-2 w-full bg-white rounded-xl shadow-xl border border-border overflow-hidden z-50"
                    >
                      {filteredDiseases.map(d => (
                        <button
                          key={d.id}
                          onClick={() => { setSearchQuery(d.name); setSelectedDisease(d.id); setShowAutocomplete(false) }}
                          className="w-full text-left px-5 py-3 hover:bg-accent transition-colors flex items-center justify-between"
                        >
                          <div>
                            <span className="font-medium text-foreground">{d.name}</span>
                            <span className="text-muted-foreground text-sm ml-2">{d.icdCode}</span>
                          </div>
                          <Badge variant="outline" className="text-xs">{d.therapeuticArea}</Badge>
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Quick Links */}
              <div className="flex items-center justify-center gap-3 mt-4 flex-wrap">
                <span className="text-sm text-muted-foreground">Popular:</span>
                {trendingDiseases.slice(0, 4).map(d => (
                  <button
                    key={d.name}
                    onClick={() => { setSearchQuery(d.name); setSelectedDisease(diseases.find(dd => dd.name === d.name)?.id || null) }}
                    className="text-sm text-[#5B4FCF] hover:underline"
                  >
                    {d.name}
                  </button>
                ))}
              </div>
            </motion.div>

            {/* CTA Buttons */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.3 }}
              className="mt-8 flex items-center justify-center gap-4 flex-wrap"
            >
              <Button size="lg" onClick={() => navigate({ page: 'register' })} className="bg-[#5B4FCF] hover:bg-[#4B3FBF] text-base px-8">
                Start Free <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
              <Button size="lg" variant="outline" onClick={() => navigate({ page: 'contact' })} className="text-base px-8">
                Book a Demo
              </Button>
            </motion.div>
          </div>

          {/* Stats */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.4 }}
            className="mt-16 grid grid-cols-3 gap-8 max-w-2xl mx-auto text-center"
          >
            {[
              { value: '10,000+', label: 'Drugs Analyzed' },
              { value: '7,000+', label: 'Diseases Covered' },
              { value: '$0', label: 'Cost to Start' },
            ].map(stat => (
              <div key={stat.label}>
                <div className="text-3xl sm:text-4xl font-bold text-foreground">{stat.value}</div>
                <div className="text-sm text-muted-foreground mt-1">{stat.label}</div>
              </div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* How It Works */}
      <section className="py-20 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <SectionHeading title="How It Works" subtitle="From disease query to validated candidate in minutes, not months" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {steps.map((step, i) => (
              <motion.div
                key={step.num}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1 }}
                className="text-center"
              >
                <div className="w-16 h-16 rounded-2xl bg-[#5B4FCF]/10 text-[#5B4FCF] font-bold text-2xl flex items-center justify-center mx-auto mb-4">
                  {step.num}
                </div>
                <h3 className="text-lg font-semibold text-foreground mb-2">{step.title}</h3>
                <p className="text-sm text-muted-foreground">{step.desc}</p>
                {i < steps.length - 1 && (
                  <ArrowRight className="w-5 h-5 text-[#5B4FCF]/30 hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2" />
                )}
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Feature Cards */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <SectionHeading title="Core Capabilities" subtitle="Everything you need for systematic drug repurposing" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((f, i) => (
              <motion.div
                key={f.title}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.08 }}
              >
                <Card className="h-full hover:shadow-lg transition-shadow cursor-pointer group" onClick={() => navigate({ page: 'features', slug: f.slug })}>
                  <CardHeader>
                    <div className="w-12 h-12 rounded-xl bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center mb-2 group-hover:bg-[#5B4FCF] group-hover:text-white transition-colors">
                      {f.icon}
                    </div>
                    <CardTitle className="text-lg">{f.title}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <CardDescription className="text-sm leading-relaxed">{f.desc}</CardDescription>
                  </CardContent>
                  <CardFooter>
                    <span className="text-sm text-[#5B4FCF] font-medium flex items-center gap-1 group-hover:gap-2 transition-all">
                      Learn more <ArrowRight className="w-3.5 h-3.5" />
                    </span>
                  </CardFooter>
                </Card>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Customer Logos */}
      <section className="py-16 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <p className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-8">Trusted by leading pharmaceutical companies</p>
          <div className="flex items-center justify-center gap-8 sm:gap-16 flex-wrap opacity-40">
            {logos.map(name => (
              <div key={name} className="text-2xl font-bold text-foreground/60 tracking-tight">{name}</div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Teaser */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <h2 className="text-3xl font-bold text-foreground">Start free. Scale as you discover.</h2>
          <p className="text-muted-foreground mt-3 text-lg max-w-xl mx-auto">
            From academic researchers to enterprise pharma, we have a plan for every stage.
          </p>
          <div className="flex items-center justify-center gap-4 mt-8">
            <Button size="lg" onClick={() => navigate({ page: 'pricing' })} className="bg-[#5B4FCF] hover:bg-[#4B3FBF]">
              View Pricing <ArrowRight className="w-4 h-4 ml-1" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => navigate({ page: 'contact' })}>
              Talk to Sales
            </Button>
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="py-20 bg-gradient-to-br from-[#5B4FCF] to-[#7B6FEF]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <h2 className="text-3xl sm:text-4xl font-bold text-white">Ready to find your next breakthrough?</h2>
          <p className="text-purple-200 mt-4 text-lg max-w-xl mx-auto">
            Join hundreds of researchers already using DrugOS to discover new therapeutic uses for existing drugs.
          </p>
          <div className="flex items-center justify-center gap-4 mt-8">
            <Button size="lg" onClick={() => navigate({ page: 'register' })} className="bg-white text-[#5B4FCF] hover:bg-slate-50">
              Get Started Free
            </Button>
            <Button size="lg" variant="outline" className="border-white/30 text-white hover:bg-white/10" onClick={() => navigate({ page: 'contact' })}>
              Schedule Demo
            </Button>
          </div>
        </div>
      </section>
    </div>
  )
}
