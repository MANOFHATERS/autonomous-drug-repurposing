'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2210-2230). Auth "account locked" page. Preserved
// VERBATIM — only the import block at the top is new.

import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useRouter } from '../../next-router-provider'
import { AuthLayout } from './_auth-layout'

export function AccountLockedPage() {
  const { navigate } = useRouter()
  return (
    <AuthLayout title="Account Locked" subtitle="Too many failed login attempts">
      <Card>
        <CardContent className="pt-6 text-center">
          <div className="w-16 h-16 rounded-full bg-[#C0392B]/10 text-[#C0392B] flex items-center justify-center mx-auto mb-4">
            <Lock className="w-8 h-8" />
          </div>
          <p className="font-semibold text-foreground text-lg">Account Locked</p>
          <p className="text-sm text-muted-foreground mt-2">
            Your account has been locked due to too many failed login attempts. Please try again after 30 minutes or contact your administrator.
          </p>
          <Button variant="outline" className="w-full mt-6" onClick={() => navigate({ page: 'forgot-password' })}>
            Reset Password
          </Button>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
