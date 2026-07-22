'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2685-2804). Authenticated dashboard page — shows
// real usage metrics (via useUsageMetrics) and recent queries (via
// useRecentQueries from localStorage). Preserved VERBATIM — only the
// import block at the top is new.

import { Search, Code, Download, Star } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { useRouter } from '../../next-router-provider'
import { useSession } from '../../session-provider'
import {
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
} from '@/components/drugos/use-api-data'
import {
  useUsageMetrics,
  useRecentQueries,
} from '@/components/drugos/use-account-data'
import { SectionHeading } from '../_app-layout'

export function AppDashboard() {
  const { navigate } = useRouter()
  const { user } = useSession()

  // FE-003: Real usage metrics from useUsageMetrics(). queries/apiCalls/reports
  // are null until a usage-tracking endpoint exists; we NEVER fabricate numbers.
  const { data: usage, loading: usageLoading, error: usageError } = useUsageMetrics()
  const { queries: recentQueriesList } = useRecentQueries()

  const stats = [
    { title: 'Queries', icon: <Search className="w-5 h-5" />, value: usage?.queries?.used != null && usage?.queries?.limit != null ? `${usage.queries.used} / ${usage.queries.limit}` : null, subtitle: usage?.queries?.used == null ? 'Usage tracking not yet available' : undefined },
    { title: 'API Calls', icon: <Code className="w-5 h-5" />, value: usage?.apiCalls?.used != null && usage?.apiCalls?.limit != null ? `${usage.apiCalls.used.toLocaleString()} / ${usage.apiCalls.limit.toLocaleString()}` : null, subtitle: usage?.apiCalls?.used == null ? 'Usage tracking not yet available' : undefined },
    { title: 'Reports', icon: <Download className="w-5 h-5" />, value: usage?.reports?.used != null && usage?.reports?.limit != null ? `${usage.reports.used} / ${usage.reports.limit}` : null, subtitle: usage?.reports?.used == null ? 'Usage tracking not yet available' : undefined },
    { title: 'Projects', icon: <Star className="w-5 h-5" />, value: usage?.projects?.used != null ? String(usage.projects.used) : null, subtitle: usage?.plan ? `Plan: ${usage.plan}` : undefined },
  ]

  return (
    <div>
      <SectionHeading
        title="Dashboard"
        subtitle={`Welcome back, ${user?.name || user?.email || 'Researcher'}`}
        action={<Button className="bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'search' })}><Search className="w-4 h-4 mr-1" /> New Search</Button>}
      />

      {/* Stats — real values where available, honest "—" otherwise */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map(stat => (
          <Card key={stat.title} className="hover:shadow-md transition-shadow">
            <CardContent className="pt-6">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">{stat.title}</p>
                  <p className="text-2xl font-bold text-foreground mt-1">{stat.value ?? '—'}</p>
                  {stat.subtitle && <p className="text-xs text-muted-foreground mt-1">{stat.subtitle}</p>}
                </div>
                <div className="w-10 h-10 rounded-lg bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center">{stat.icon}</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Queries — real, from localStorage via useRecentQueries */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Recent Queries</CardTitle>
          </CardHeader>
          <CardContent>
            {recentQueriesList.length === 0 ? (
              <EmptyState
                title="No recent queries yet"
                description="Your search history will appear here once you run your first disease search."
                action={<Button size="sm" className="bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => navigate({ page: 'app', section: 'search' })}>Start a search</Button>}
              />
            ) : (
              <div className="space-y-3">
                {recentQueriesList.slice(0, 8).map(q => (
                  <button
                    key={q.id}
                    onClick={() => navigate({ page: 'app', section: 'search', sub: 'results', id: q.q })}
                    className="flex items-center justify-between py-2 border-b border-border last:border-0 w-full text-left hover:bg-accent/50 -mx-2 px-2 rounded-md transition-colors"
                  >
                    <div>
                      <p className="font-medium text-foreground text-sm">{q.q}</p>
                      <p className="text-xs text-muted-foreground">{new Date(q.timestamp).toLocaleString()}</p>
                    </div>
                    <Badge variant="outline" className="text-xs capitalize">{q.type}</Badge>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Usage — real values where available; honest empty state otherwise */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Usage This Period</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {usageError ? (
              <ErrorDisplay error={usageError} />
            ) : usageLoading ? (
              <LoadingSpinner label="Loading usage…" />
            ) : !usage ? (
              <EmptyState title="Usage data unavailable" description="Usage tracking is not yet configured for your organization." />
            ) : (
              <>
                {[
                  { label: 'API Keys (active)', value: usage.apiKeys?.used, max: usage.apiKeys?.limit },
                  { label: 'Projects', value: usage.projects?.used, max: usage.projects?.limit },
                  { label: 'Seats', value: usage.seats?.used, max: usage.seats?.limit },
                ].map(item => (
                  <div key={item.label}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-muted-foreground">{item.label}</span>
                      <span className="text-foreground font-medium">
                        {item.value != null ? item.value.toLocaleString() : '—'}
                        {item.max != null ? ` / ${item.max.toLocaleString()}` : ''}
                      </span>
                    </div>
                    {item.max != null && item.max > 0 && item.value != null && (
                      <Progress value={Math.min(100, (item.value / item.max) * 100)} className="h-2" />
                    )}
                  </div>
                ))}
                {usage.queries == null && usage.apiCalls == null && usage.reports == null && (
                  <p className="text-xs text-muted-foreground italic">
                    Queries, API-call, and report quotas require a usage-tracking endpoint that is not yet deployed.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
