'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2918-2991). Authenticated disease search results page
// — fetches real candidates via useRlCandidates. Preserved VERBATIM —
// only the import block at the top is new.

import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  useDiseaseSearch,
  useRlCandidates,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
} from '@/components/drugos/use-api-data'
import { ScoreBar } from '@/components/drugos/score-bar'
import { SafetyBadge } from '@/components/drugos/safety-badge'
import { SectionHeading } from '../_app-layout'

export function AppSearchResultsPage({ diseaseId }: { diseaseId?: string }) {
  // FE-015: diseaseId is now a disease NAME. Fetch real candidates via useRlCandidates.
  const diseaseName = diseaseId || ''
  const { data: rlData, loading, error } = useRlCandidates({ disease: diseaseName, limit: 50 })
  const { data: diseaseSearch } = useDiseaseSearch(diseaseName)
  const diseaseMeta = diseaseSearch?.items?.[0] ?? null
  const candidates = rlData?.candidates ?? []

  if (!diseaseName) {
    return (
      <EmptyState
        title="No disease selected"
        description="Use the search box to find a disease and view its drug repurposing candidates."
      />
    )
  }

  return (
    <div>
      <SectionHeading
        title={`${diseaseMeta?.name ?? diseaseName} — Candidates`}
        subtitle={
          loading ? 'Loading candidates…'
            : error ? 'Failed to load candidates'
              : `${candidates.length} drug repurposing ${candidates.length === 1 ? 'candidate' : 'candidates'} found${rlData?.source === 'local_csv' ? ' (offline model)' : ''}`
        }
        action={<Button variant="outline" disabled><Download className="w-4 h-4 mr-1" />Export CSV</Button>}
      />

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <LoadingSpinner label="Fetching ranked candidates from the RL ranker…" />
          ) : error ? (
            <ErrorDisplay error={error} />
          ) : candidates.length === 0 ? (
            <EmptyState
              title="No candidates found"
              description={`The RL ranker returned no drug candidates for "${diseaseName}". Try a different disease name or check that the RL service is deployed.`}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {['#', 'Drug Name', 'Composite', 'Safety', 'Mechanism', 'Phase', 'IP'].map(h => (
                      <th key={h} className="text-left py-3 px-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((c, i) => {
                    const reward = typeof c.reward === 'number' ? Math.round(c.reward * 100) : (typeof c.gnnScore === 'number' ? Math.round(c.gnnScore * 100) : 0)
                    return (
                      <tr key={`${c.drug}-${i}`} className="border-b border-border/50 hover:bg-accent/50 transition-colors">
                        <td className="py-3 px-3 font-bold text-muted-foreground">{c.rank ?? i + 1}</td>
                        <td className="py-3 px-3"><span className="font-semibold text-foreground">{c.drug}</span></td>
                        <td className="py-3 px-3"><div className="w-24"><ScoreBar score={reward} size="sm" showInfoIcon={false} /></div></td>
                        <td className="py-3 px-3"><SafetyBadge tier="unknown" /></td>
                        <td className="py-3 px-3 max-w-[200px]"><span className="text-xs text-muted-foreground line-clamp-2">{c.literatureSupport != null ? `Literature support: ${c.literatureSupport.toFixed(2)}` : 'Mechanism not available'}</span></td>
                        <td className="py-3 px-3"><span className="text-xs font-medium text-foreground">N/A (RL prediction)</span></td>
                        <td className="py-3 px-3"><span className="text-xs text-muted-foreground">N/A</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
