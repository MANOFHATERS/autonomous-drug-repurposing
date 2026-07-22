'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 3088-3126). Top-level dispatcher for authenticated
// app sections — enforces RBAC and delegates to AppDashboard or
// CoreScreenBridge. Preserved VERBATIM — only the import block at the
// top is new.

import { useEffect } from 'react'
import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useSession } from '../../session-provider'
import { useRouter } from '../../next-router-provider'
import { canAccessSection, roleLabel } from '@/lib/rbac'
import { AppDashboard } from './app-dashboard'
import { CoreScreenBridge } from './core-screen-bridge'

export function AppSectionRenderer({ section, sub, id }: { section: string; sub?: string; id?: string }) {
  // RBAC: redirect to dashboard if the current user's role can't access
  // this section. We use a deferred navigation effect so React doesn't
  // warn about rendering during render.
  const { user } = useSession()
  const { navigate } = useRouter()

  // Alias 'settings' → 'preferences' so the user dropdown's "Settings" item
  // lands on a real page instead of triggering an access-denied redirect.
  const effectiveSection = section === 'settings' ? 'preferences' : section

  useEffect(() => {
    if (user && !canAccessSection(user.role, effectiveSection) && effectiveSection !== 'dashboard') {
      navigate({ page: 'app', section: 'dashboard' })
    }
  }, [user, effectiveSection, navigate])

  if (user && !canAccessSection(user.role, effectiveSection) && effectiveSection !== 'dashboard') {
    return (
      <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
        <div className="w-16 h-16 rounded-full bg-[#C0392B]/10 text-[#C0392B] flex items-center justify-center mx-auto mb-4">
          <Lock className="w-8 h-8" />
        </div>
        <h2 className="text-xl font-bold text-foreground">Access denied</h2>
        <p className="text-sm text-muted-foreground mt-2 max-w-md">
          Your role ({roleLabel(user.role)}) does not have permission to view this section.
          Contact your administrator if you believe this is an error.
        </p>
        <Button className="mt-6 bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
          Back to Dashboard
        </Button>
      </div>
    )
  }

  if (effectiveSection === 'dashboard') return <AppDashboard />
  // Delegate to core screens from core-screens.tsx
  return <CoreScreenBridge section={effectiveSection} sub={sub} id={id} />
}
