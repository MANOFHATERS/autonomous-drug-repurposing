'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2806-2916). Authenticated disease search page — uses
// useDiseaseSearch (real /api/diseases/search endpoint). Preserved
// VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Search, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useRouter } from '../../next-router-provider'
import {
  useDiseaseSearch,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
} from '@/components/drugos/use-api-data'
import { useRecentQueries } from '@/components/drugos/use-account-data'
import { SectionHeading } from '../_app-layout'

export function AppSearchPage() {
  const { navigate } = useRouter()
  const [query, setQuery] = useState('')
  // FE-014: Real disease search via useDiseaseSearch (hits /api/diseases/search).
  const { data: searchResult, loading, error } = useDiseaseSearch(query)
  const results = searchResult?.items ?? []
  const { queries: recentQueriesList, addRecentQuery } = useRecentQueries()

  const handleSearch = () => {
    if (query.trim().length >= 2) {
      addRecentQuery(query.trim(), 'disease')
    }
  }

  const handleSelect = (d: { descriptorUi: string; name: string }) => {
    addRecentQuery(d.name, 'disease')
    navigate({ page: 'app', section: 'search', sub: 'results', id: d.name })
  }

  return (
    <div>
      <SectionHeading title="Disease Search" subtitle="Search for a disease to find drug repurposing candidates" />
      <div className="max-w-3xl mx-auto text-center py-8">
        <div className="relative mb-8">
          <Search className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Search for a disease, condition, or ICD code..."
            className="w-full pl-12 pr-32 py-4 text-lg border border-border rounded-2xl focus:outline-none focus:ring-2 focus:ring-[#5B4FCF]/20 focus:border-[#5B4FCF] shadow-lg shadow-slate-200/50 bg-white"
          />
          <Button className="absolute right-2 top-2 bottom-2 px-6 bg-[#5B4FCF] hover:bg-[#4B3FBF] rounded-xl" onClick={handleSearch}>Search</Button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-left">
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-medium text-muted-foreground mb-3">Recent Queries</h4>
              {recentQueriesList.length === 0 ? (
                <p className="text-xs text-muted-foreground">Your recent searches will appear here.</p>
              ) : (
                recentQueriesList.slice(0, 3).map(q => (
                  <button key={q.id} onClick={() => { setQuery(q.q); handleSearch() }} className="block text-sm py-1.5 text-foreground hover:text-[#5B4FCF] cursor-pointer">{q.q}</button>
                ))
              )}
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-medium text-muted-foreground mb-3">Trending</h4>
              <p className="text-xs text-muted-foreground">Trending diseases will appear here once search analytics are enabled.</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-medium text-muted-foreground mb-3">Quick Start</h4>
              <p className="text-xs text-muted-foreground">Start typing a disease name above — suggestions will appear as you type.</p>
            </CardContent>
          </Card>
        </div>

        {/* Search Results */}
        {query.trim().length >= 2 && (
          <div className="mt-8 text-left">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Results {searchResult ? `(${results.length})` : ''}</CardTitle>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <LoadingSpinner label="Searching diseases…" />
                ) : error ? (
                  <ErrorDisplay error={error} />
                ) : results.length === 0 ? (
                  <EmptyState
                    title="No diseases found"
                    description={`No MeSH descriptors matched "${query}". Try a different spelling or a broader term.`}
                  />
                ) : (
                  <div className="space-y-3">
                    {results.map(d => (
                      <button
                        key={d.descriptorUi}
                        onClick={() => handleSelect(d)}
                        className="w-full flex items-center justify-between p-4 rounded-xl border border-border hover:bg-accent transition-colors text-left"
                      >
                        <div>
                          <p className="font-medium text-foreground">{d.name}</p>
                          {d.scopeNote && (
                            <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{d.scopeNote}</p>
                          )}
                          {d.treeNumber && d.treeNumber.length > 0 && (
                            <div className="flex items-center gap-2 mt-1">
                              <Badge variant="outline" className="text-xs">{d.treeNumber[0]}</Badge>
                            </div>
                          )}
                        </div>
                        <ArrowRight className="w-4 h-4 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
