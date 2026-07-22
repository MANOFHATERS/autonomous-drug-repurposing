'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1924-1942). Auth "email verified" success page.
// Preserved VERBATIM — only the import block at the top is new.

import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function EmailVerificationPage() {
  const { navigate } = useRouter()
  return (
    <AuthLayout title="Email Verified" subtitle="Your email has been successfully verified">
      <Card>
        <CardContent className="pt-6 text-center">
          <div className="w-16 h-16 rounded-full bg-[#1D9E75]/10 text-[#1D9E75] flex items-center justify-center mx-auto mb-4">
            <Check className="w-8 h-8" />
          </div>
          <p className="font-semibold text-foreground text-lg">Email Verified Successfully</p>
          <p className="text-sm text-muted-foreground mt-2">Your account is now active. You can start using DrugOS.</p>
          <Button className="w-full mt-6 bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
            Continue to DrugOS
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
