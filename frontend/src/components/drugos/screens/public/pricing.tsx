'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 734-908). Public pricing page with cost calculator and
// FAQ. Preserved VERBATIM — only the import block at the top is new.

import { useState, useEffect, useMemo } from 'react'
import { BarChart3, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { useRouter } from '../../next-router-provider'

export function PricingPage() {
  const { navigate } = useRouter()
  const [calcQueries, setCalcQueries] = useState([500])
  const [calcApiCalls, setCalcApiCalls] = useState([25000])
  const [calcSeats, setCalcSeats] = useState([10])
  const [faqOpen, setFaqOpen] = useState<string | null>(null)
  const [realPlans, setRealPlans] = useState<Array<{ id: string; name: string; priceCents: number; seats: number; features: string[] }> | null>(null)

  // Fetch real plans from /api/billing/plans so the public pricing page
  // matches what the backend actually offers. Falls back to a curated list
  // if the API is unreachable.
  useEffect(() => {
    let mounted = true;
    fetch('/api/billing/plans')
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then((r: { plans: typeof realPlans }) => {
        if (mounted && r.plans) setRealPlans(r.plans);
      })
      .catch(() => { /* fall back to curated list below */ });
    return () => { mounted = false };
  }, [])

  const planCards = useMemo(() => {
    if (realPlans && realPlans.length > 0) {
      return realPlans.map(p => ({
        id: p.id,
        name: p.name,
        price: p.priceCents === 0 ? '$0' : `$${(p.priceCents / 100).toLocaleString()}`,
        period: p.priceCents === 0 ? 'forever' : '/month',
        users: p.seats === 100 ? 'Unlimited' : `Up to ${p.seats}`,
        features: p.features,
      }))
    }
    // Curated fallback that mirrors the backend PLANS list.
    return [
      { id: 'free', name: 'Free', price: '$0', period: 'forever', users: '1 seat', features: ['10 evidence packages / month', 'PubMed literature search', 'ClinicalTrials.gov search', 'Community support'] },
      { id: 'researcher', name: 'Researcher', price: '$49', period: '/month', users: '1 seat', features: ['Unlimited evidence packages', 'FDA adverse event data', 'USPTO patent search', 'Email support', 'API access (1,000 req/day)'] },
      { id: 'team', name: 'Team', price: '$299', period: '/month', users: 'Up to 10 seats', features: ['Everything in Researcher', 'Collaboration workspaces', 'Audit logs & SSO', 'Priority support', 'API access (50,000 req/day)'] },
      { id: 'enterprise', name: 'Enterprise', price: 'Custom', period: '', users: 'Unlimited', features: ['Everything in Team', 'Dedicated CSM', 'Custom data residency', 'On-prem deployment option', 'Unlimited API'] },
    ]
  }, [realPlans])

  const faqs = [
    { q: 'Can I switch plans at any time?', a: 'Yes, you can upgrade or downgrade your plan at any time. Changes take effect at the start of your next billing cycle.' },
    { q: 'What happens when I exceed my query limit?', a: 'You will receive a warning at 80% usage. After exceeding, additional queries are billed at a per-query overage rate.' },
    { q: 'Is the Free plan really free?', a: 'Yes, the Free plan is completely free for individual researchers. No credit card required.' },
    { q: 'What is the Discovery Deal?', a: 'The Discovery Deal is a licensing arrangement where pharmaceutical companies acquire exclusive rights to a validated drug repurposing candidate identified by DrugOS, including full evidence packages and regulatory support.' },
    { q: 'Do you offer HIPAA compliance?', a: 'Yes, our Team and Enterprise plans include HIPAA-compliant infrastructure with Business Associate Agreements (BAA) available.' },
    { q: 'Can I try before I buy?', a: 'Absolutely. Start with our Free plan or request a 14-day trial of any paid plan with full feature access.' },
  ]

  const estimatedCost = useMemo(() => {
    const q = calcQueries[0]
    const api = calcApiCalls[0]
    const seats = calcSeats[0]
    if (q <= 10 && api <= 1000 && seats <= 1) return 0
    if (q <= 100 && api <= 1000 && seats <= 5) return 49
    if (q <= 1000 && api <= 50000 && seats <= 25) return 299
    return 299 + Math.max(0, (seats - 10) * 30) + Math.max(0, (api - 50000) * 0.001)
  }, [calcQueries, calcApiCalls, calcSeats])

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-foreground">Simple, transparent pricing</h1>
        <p className="text-lg text-muted-foreground mt-3 max-w-2xl mx-auto">
          Start free for academic research. Scale as your discoveries grow. Enterprise-grade security included.
        </p>
      </div>

      {/* Plan Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-16">
        {planCards.map(plan => (
          <Card
            key={plan.id}
            className={cn(
              'relative hover:shadow-lg transition-shadow',
              plan.id === 'team' && 'border-[#5B4FCF] ring-2 ring-[#5B4FCF]/20'
            )}
          >
            {plan.id === 'team' && (
              <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                <Badge className="bg-[#5B4FCF] text-white px-3">Most Popular</Badge>
              </div>
            )}
            <CardHeader className="pb-2">
              <CardTitle className="text-lg">{plan.name}</CardTitle>
              <div className="mt-2">
                <span className="text-3xl font-bold text-foreground">{plan.price}</span>
                <span className="text-muted-foreground text-sm">{plan.period}</span>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">{plan.users}</p>
              <ul className="space-y-2">
                {plan.features.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <Check className="w-4 h-4 text-[#1D9E75] shrink-0 mt-0.5" />
                    <span className="text-muted-foreground">{f}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
            <CardFooter>
              <Button
                className={cn(
                  'w-full',
                  plan.id === 'team' ? 'bg-[#5B4FCF] hover:bg-[#4B3FBF]' : '',
                  plan.id === 'free' && 'bg-[#1D9E75] hover:bg-[#168F68]'
                )}
                variant={plan.id === 'team' || plan.id === 'free' ? 'default' : 'outline'}
                onClick={() => navigate({ page: plan.id === 'enterprise' ? 'contact' : 'register' })}
              >
                {plan.id === 'enterprise' ? 'Contact Us' : plan.price === '$0' ? 'Start Free' : 'Get Started'}
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>

      {/* Cost Calculator */}
      <Card className="mb-16">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-[#5B4FCF]" /> Cost Calculator
          </CardTitle>
          <CardDescription>Estimate your monthly cost based on your expected usage</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <div className="flex justify-between mb-2">
              <Label>Monthly Queries</Label>
              <span className="text-sm font-medium text-foreground">{calcQueries[0]}</span>
            </div>
            <Slider value={calcQueries} onValueChange={setCalcQueries} min={0} max={5000} step={50} />
          </div>
          <div>
            <div className="flex justify-between mb-2">
              <Label>API Calls / Day</Label>
              <span className="text-sm font-medium text-foreground">{calcApiCalls[0].toLocaleString()}</span>
            </div>
            <Slider value={calcApiCalls} onValueChange={setCalcApiCalls} min={0} max={100000} step={1000} />
          </div>
          <div>
            <div className="flex justify-between mb-2">
              <Label>Team Seats</Label>
              <span className="text-sm font-medium text-foreground">{calcSeats[0]}</span>
            </div>
            <Slider value={calcSeats} onValueChange={setCalcSeats} min={1} max={100} step={1} />
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <span className="text-lg font-medium">Estimated Monthly Cost</span>
            <span className="text-3xl font-bold text-[#5B4FCF]">
              {estimatedCost === 0 ? 'Free' : `$${estimatedCost.toLocaleString()}/mo`}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* FAQ */}
      <div className="max-w-3xl mx-auto">
        <h2 className="text-2xl font-bold text-foreground text-center mb-8">Frequently Asked Questions</h2>
        <Accordion type="single" collapsible className="space-y-3">
          {faqs.map((faq, i) => (
            <AccordionItem key={i} value={`faq-${i}`} className="bg-white rounded-lg border px-4">
              <AccordionTrigger className="text-left font-medium">{faq.q}</AccordionTrigger>
              <AccordionContent className="text-muted-foreground">{faq.a}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </div>
  )
}
