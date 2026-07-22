'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2055-2109). Auth onboarding "role selection" page.
// Preserved VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Microscope, BarChart3, Award, Briefcase, Code, Eye } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useRouter } from '../../next-router-provider'
import { useSession } from '../../session-provider'
import { roleLabel } from '@/lib/rbac'
import { AuthLayout } from './_auth-layout'

export function OnboardingRolePage() {
  // This page is kept for backwards compatibility but is no longer the primary
  // onboarding entry — the role is collected during registration. If the user
  // lands here, we show them their current role and let them proceed.
  const { navigate } = useRouter()
  const { user } = useSession()
  const [selected, setSelected] = useState<string | null>(user?.role || null)
  const roles = [
    { id: 'researcher', icon: <Microscope className="w-5 h-5" />, name: 'Researcher', desc: 'Academic or industry researcher' },
    { id: 'data-scientist', icon: <BarChart3 className="w-5 h-5" />, name: 'Data Scientist', desc: 'ML & data analysis' },
    { id: 'pi', icon: <Award className="w-5 h-5" />, name: 'PI / Lab Head', desc: 'Principal Investigator' },
    { id: 'business-dev', icon: <Briefcase className="w-5 h-5" />, name: 'Business Dev', desc: 'Partnerships & licensing' },
    { id: 'developer', icon: <Code className="w-5 h-5" />, name: 'Developer', desc: 'API integration & tools' },
    { id: 'viewer', icon: <Eye className="w-5 h-5" />, name: 'Viewer', desc: 'Read-only access' },
  ]

  return (
    <AuthLayout title="What best describes your role?" subtitle="This helps us personalize your experience">
      <Card>
        <CardContent className="pt-6">
          {user?.role && (
            <div className="rounded-md bg-[#5B4FCF]/5 border border-[#5B4FCF]/20 text-sm px-3 py-2 mb-4">
              You already selected <strong className="text-[#5B4FCF]">{roleLabel(user.role)}</strong> during registration.
              Changing your role here requires admin approval — for now, you can proceed.
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 mb-6">
            {roles.map(role => (
              <button
                key={role.id}
                onClick={() => setSelected(role.id)}
                className={cn(
                  'p-4 rounded-xl border text-left transition-colors',
                  selected === role.id ? 'border-[#5B4FCF] bg-[#5B4FCF]/5' : 'border-border hover:bg-accent'
                )}
              >
                <div className={cn(
                  'w-8 h-8 rounded-lg flex items-center justify-center mb-2',
                  selected === role.id ? 'bg-[#5B4FCF] text-white' : 'bg-accent text-muted-foreground'
                )}>
                  {role.icon}
                </div>
                <p className="font-medium text-foreground text-sm">{role.name}</p>
                <p className="text-xs text-muted-foreground">{role.desc}</p>
              </button>
            ))}
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" disabled={!selected} onClick={() => navigate({ page: 'onboarding-workspace' })}>
            Continue
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
