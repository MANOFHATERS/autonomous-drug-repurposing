'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1883-1922). Auth MFA challenge page. Preserved
// VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function MFAChallengePage() {
  const [otp, setOtp] = useState(['', '', '', '', '', ''])
  const { navigate } = useRouter()

  const handleOtpChange = (index: number, value: string) => {
    const newOtp = [...otp]
    newOtp[index] = value.slice(-1)
    setOtp(newOtp)
    if (value && index < 5) {
      const next = document.getElementById(`otp-${index + 1}`)
      next?.focus()
    }
  }

  return (
    <AuthLayout title="Two-Factor Authentication" subtitle="Enter the 6-digit code from your authenticator app">
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground text-center mb-4">Enter the 6-digit code from your authenticator</p>
          <div className="flex gap-2 justify-center mb-6">
            {otp.map((digit, i) => (
              <Input
                key={i}
                id={`otp-${i}`}
                className="w-12 h-14 text-center text-xl font-bold"
                maxLength={1}
                value={digit}
                onChange={e => handleOtpChange(i, e.target.value)}
              />
            ))}
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>Verify</Button>
          <p className="text-center text-sm text-muted-foreground mt-4">
            Didn&apos;t receive a code? <button className="text-[#5B4FCF] hover:underline">Resend</button>
          </p>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
