'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1976-2012). Auth "select organization" page.
// Preserved VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function OrgSelectionPage() {
  const { navigate } = useRouter()
  const [selected, setSelected] = useState<string | null>(null)
  const orgs = [
    { name: 'DrugOS Corp', plan: 'Professional', members: '18 members' },
    { name: 'University Lab', plan: 'Academic', members: '6 members' },
    { name: 'Personal', plan: 'Free', members: '1 member' },
  ]

  return (
    <AuthLayout title="Select Organization" subtitle="Choose which organization to access">
      <Card>
        <CardContent className="pt-6 space-y-3">
          {orgs.map(org => (
            <button
              key={org.name}
              onClick={() => setSelected(org.name)}
              className={cn(
                'w-full text-left px-4 py-3 rounded-lg border transition-colors flex items-center justify-between',
                selected === org.name ? 'border-[#5B4FCF] bg-[#5B4FCF]/5' : 'border-border hover:bg-accent'
              )}
            >
              <div>
                <span className="font-medium text-foreground">{org.name}</span>
                <p className="text-xs text-muted-foreground">{org.members}</p>
              </div>
              <Badge variant="secondary">{org.plan}</Badge>
            </button>
          ))}
          <Button className="w-full mt-4 bg-[#5B4FCF] hover:bg-[#4B3FBF]" disabled={!selected} onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
            Continue
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
