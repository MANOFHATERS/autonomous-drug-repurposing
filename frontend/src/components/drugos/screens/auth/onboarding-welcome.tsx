'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2014-2053). Auth onboarding welcome page.
// Preserved VERBATIM — only the import block at the top is new.

import { Building, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useRouter } from '../../next-router-provider'
import { useSession } from '../../session-provider'
import { roleLabel } from '@/lib/rbac'
import { AuthLayout } from './_auth-layout'

export function OnboardingWelcomePage() {
  const { navigate } = useRouter()
  const { user } = useSession()
  // Skip the role step (the user already picked their role during registration).
  // We show a 2-step plan: workspace setup + invite teammates.
  const steps = [
    { icon: <Building className="w-5 h-5" />, title: 'Set up your workspace', desc: 'Configure your research environment' },
    { icon: <Users className="w-5 h-5" />, title: 'Invite team members', desc: 'Collaborate with your research team (optional)' },
  ]

  return (
    <AuthLayout title={`Welcome to DrugOS, ${user?.name || 'researcher'}!`} subtitle="Let's get you set up in a few steps">
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div className="rounded-md bg-[#5B4FCF]/5 border border-[#5B4FCF]/20 text-sm px-3 py-2">
            You registered as <strong className="text-[#5B4FCF]">{roleLabel(user?.role)}</strong>.
            Your role determines which sections of the app you can access.
          </div>
          {steps.map((step, i) => (
            <div key={step.title} className="flex items-center gap-4 p-4 bg-accent rounded-xl">
              <div className="w-10 h-10 rounded-full bg-[#5B4FCF] text-white font-bold text-sm flex items-center justify-center shrink-0">
                {i + 1}
              </div>
              <div>
                <p className="font-medium text-foreground">{step.title}</p>
                <p className="text-sm text-muted-foreground">{step.desc}</p>
              </div>
            </div>
          ))}
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'onboarding-workspace' })}>
            Get Started
          </Button>
          <Button variant="ghost" className="w-full text-muted-foreground" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
            Skip onboarding — go straight to dashboard
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
