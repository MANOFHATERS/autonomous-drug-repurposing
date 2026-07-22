'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2137-2182). Auth onboarding "invite teammates" page.
// Preserved VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function OnboardingInvitePage() {
  const { navigate } = useRouter()
  const [emails, setEmails] = useState([''])
  const [currentEmail, setCurrentEmail] = useState('')

  const addEmail = () => {
    if (currentEmail && currentEmail.includes('@')) {
      setEmails([...emails, currentEmail])
      setCurrentEmail('')
    }
  }

  return (
    <AuthLayout title="Invite your team" subtitle="Add team members to your workspace">
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div>
            <Label>Email Address</Label>
            <div className="flex gap-2">
              <Input placeholder="colleague@university.edu" value={currentEmail} onChange={e => setCurrentEmail(e.target.value)} onKeyDown={e => e.key === 'Enter' && addEmail()} />
              <Button variant="outline" onClick={addEmail}><Plus className="w-4 h-4" /></Button>
            </div>
          </div>
          {emails.filter(e => e).length > 0 && (
            <div className="space-y-2">
              {emails.filter(e => e).map((email, i) => (
                <div key={i} className="flex items-center justify-between px-3 py-2 bg-accent rounded-lg">
                  <span className="text-sm text-foreground">{email}</span>
                  <button onClick={() => setEmails(emails.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-foreground">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
            Send Invites
          </Button>
          <Button variant="ghost" className="w-full text-muted-foreground" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
            Skip for now
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
