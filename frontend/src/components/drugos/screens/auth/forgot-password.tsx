'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1829-1858). Auth "forgot password" page. Preserved
// VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AuthLayout } from './_auth-layout'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)

  return (
    <AuthLayout title="Reset your password" subtitle="We'll send you a reset link">
      <Card>
        <CardContent className="pt-6 space-y-4">
          {!sent ? (
            <>
              <div>
                <Label>Email Address</Label>
                <Input type="email" placeholder="you@company.com" value={email} onChange={e => setEmail(e.target.value)} />
              </div>
              <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => setSent(true)}>Send Reset Link</Button>
            </>
          ) : (
            <div className="text-center py-4">
              <div className="w-14 h-14 rounded-full bg-[#1D9E75]/10 text-[#1D9E75] flex items-center justify-center mx-auto mb-4">
                <Mail className="w-7 h-7" />
              </div>
              <p className="font-semibold text-foreground">Check your email</p>
              <p className="text-sm text-muted-foreground mt-1">We sent a reset link to {email || 'your email'}</p>
            </div>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
