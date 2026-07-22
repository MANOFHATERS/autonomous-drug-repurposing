'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2184-2208). Auth "admin approval pending" page.
// Preserved VERBATIM — only the import block at the top is new.

import { AlertTriangle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { AuthLayout } from './_auth-layout'

export function AdminApprovalPage() {
  return (
    <AuthLayout title="Approval Pending" subtitle="Your account requires admin approval">
      <Card>
        <CardContent className="pt-6 text-center">
          <div className="w-16 h-16 rounded-full bg-[#D4853A]/10 text-[#D4853A] flex items-center justify-center mx-auto mb-4">
            <AlertTriangle className="w-8 h-8" />
          </div>
          <p className="font-semibold text-foreground text-lg">Awaiting Admin Approval</p>
          <p className="text-sm text-muted-foreground mt-2">
            Your organization requires admin approval for new accounts. You&apos;ll receive an email once your account is approved.
          </p>
          <div className="mt-6 p-4 bg-accent rounded-xl text-left">
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Typical wait time:</span> 1-2 business days
            </p>
            <p className="text-sm text-muted-foreground mt-1">
              <span className="font-medium text-foreground">Contact:</span> admin@yourorg.com
            </p>
          </div>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
