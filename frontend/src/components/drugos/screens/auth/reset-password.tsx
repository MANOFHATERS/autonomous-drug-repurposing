'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1860-1881). Auth "reset password" page. Preserved
// VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AuthLayout } from './_auth-layout'

export function ResetPasswordPage() {
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  return (
    <AuthLayout title="Set new password" subtitle="Choose a strong password for your account">
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div>
            <Label>New Password</Label>
            <Input type="password" placeholder="Min 12 characters" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <div>
            <Label>Confirm Password</Label>
            <Input type="password" placeholder="Repeat password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} />
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]">Reset Password</Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
