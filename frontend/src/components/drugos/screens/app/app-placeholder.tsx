'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2993-3013). Placeholder page rendered when an app
// section has no real screen yet. Preserved VERBATIM — only the import
// block at the top is new.

import { LayoutDashboard } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { sidebarNavGroups } from '../../app-shell'
import { SectionHeading } from '../_app-layout'

export function AppPlaceholderSection({ section }: { section: string }) {
  const label = sidebarNavGroups.flatMap(g => g.items).find(i => i.id === section)?.label || section
  const Icon = sidebarNavGroups.flatMap(g => g.items).find(i => i.id === section)?.icon || LayoutDashboard

  return (
    <div>
      <SectionHeading title={label} subtitle={`This is the ${label} section of DrugOS`} />
      <Card>
        <CardContent className="pt-6 text-center py-16">
          <div className="w-16 h-16 rounded-2xl bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center mx-auto mb-4">
            <Icon className="w-8 h-8" />
          </div>
          <h3 className="text-lg font-semibold text-foreground">{label}</h3>
          <p className="text-sm text-muted-foreground mt-2 max-w-md mx-auto">
            This section is under development. Check back soon for full {label.toLowerCase()} functionality.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
