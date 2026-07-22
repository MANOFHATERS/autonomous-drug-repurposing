'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2111-2135). Auth onboarding "workspace setup" page.
// Preserved VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function OnboardingWorkspacePage() {
  const { navigate } = useRouter()
  const [workspaceName, setWorkspaceName] = useState('')
  const [orgName, setOrgName] = useState('')

  return (
    <AuthLayout title="Set up your workspace" subtitle="Name your research workspace and organization">
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div>
            <Label>Workspace Name</Label>
            <Input placeholder="My Research Lab" value={workspaceName} onChange={e => setWorkspaceName(e.target.value)} />
          </div>
          <div>
            <Label>Organization Name</Label>
            <Input placeholder="University or Company" value={orgName} onChange={e => setOrgName(e.target.value)} />
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'onboarding-invite' })}>
            Create Workspace
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
