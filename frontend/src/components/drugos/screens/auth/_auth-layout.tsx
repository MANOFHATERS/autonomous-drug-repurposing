'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1628-1646). Shared layout for every auth-flow page
// (login, register, onboarding, etc.). Preserved VERBATIM.

import React from 'react'
import { useRouter } from '../../next-router-provider'
import { DrugOSLogo } from '../_app-layout'

export function AuthLayout({ children, title, subtitle }: { children: React.ReactNode; title: string; subtitle?: string }) {
  const { navigate } = useRouter()
  return (
    <div className="min-h-screen flex flex-col bg-[#F8F8FA]">
      <div className="flex-1 flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <div className="text-center mb-8">
            <button onClick={() => navigate({ page: 'landing' })} className="inline-block mb-6">
              <DrugOSLogo size="md" />
            </button>
            <h1 className="text-2xl font-bold text-foreground">{title}</h1>
            {subtitle && <p className="text-muted-foreground mt-1">{subtitle}</p>}
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}
