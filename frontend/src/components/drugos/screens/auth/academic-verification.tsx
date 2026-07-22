'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1944-1974). Auth "academic verification" page.
// Preserved VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { GraduationCap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AuthLayout } from './_auth-layout'

export function AcademicVerificationPage() {
  const [email, setEmail] = useState('')
  const [verified, setVerified] = useState(false)

  return (
    <AuthLayout title="Academic Verification" subtitle="Verify your .edu email for free access">
      <Card>
        <CardContent className="pt-6 space-y-4">
          {!verified ? (
            <>
              <div>
                <Label>University Email</Label>
                <Input type="email" placeholder="you@university.edu" value={email} onChange={e => setEmail(e.target.value)} />
                <p className="text-xs text-muted-foreground mt-1">Must be a .edu email address</p>
              </div>
              <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => setVerified(true)}>Verify Academic Status</Button>
            </>
          ) : (
            <div className="text-center py-4">
              <div className="w-14 h-14 rounded-full bg-[#1D9E75]/10 text-[#1D9E75] flex items-center justify-center mx-auto mb-4">
                <GraduationCap className="w-7 h-7" />
              </div>
              <p className="font-semibold text-foreground">Academic Status Verified</p>
              <p className="text-sm text-muted-foreground mt-1">You now have access to the Free Academic plan.</p>
            </div>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
