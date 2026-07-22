'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay } from '../../use-api-data';
import { api, type AdminMetricsResponse } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, LineChart, Line } from 'recharts';
import { RefreshCw, Users, Building, CreditCard, Activity, FolderKanban, Target, CheckCircle2, FileText, Database, GitBranch, Network, Share2 } from 'lucide-react';
import { FadeIn, PageHeader, StatCard, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 33. INVESTOR DASHBOARD SCREEN
// ═══════════════════════════════════════════
/**
 * FE-013 ROOT FIX (Team Member 15, v108): The previous InvestorDashboardScreen
 * rendered fabricated ARR/MRR data (Jan $420K ARR → Jun $840K ARR),
 * fabricated customer counts (42 customers, +24%), fabricated NRR (118%),
 * and 3 fabricated cohorts. An investor saw "$840K ARR" — both
 * fabricated. Investment decisions were made on fake financials.
 * This is securities fraud if shown to actual investors.
 *
 * ROOT FIX: Per the issue spec, remove all fabricated financial data.
 * Investor data must come from real financial systems (Stripe,
 * QuickBooks, Carta), not hardcoded arrays. We render an honest
 * EmptyState that points the user at the finance system — never
 * fabricated ARR/MRR/cohorts.
 */
export function InvestorDashboardScreen() {
  // Issue 315 (audit 301-320): Wire to /api/admin/metrics. The previous
  // InvestorDashboardScreen rendered fabricated ARR/MRR/customer
  // counts/NRR/cohort data — showing fabricated financials to actual
  // investors is securities fraud.
  //
  // ROOT FIX: This screen now calls /api/admin/metrics, which returns
  // REAL platform traction metrics derived from existing DB tables:
  //   - totalUsers, totalOrganizations, activeSubscriptions
  //   - totalProjects, totalHypotheses, totalValidatedHypotheses
  //   - auditLogEventsLast30Days, topActionsLast30Days
  //   - dailyActiveUsersLast7Days
  //   - dataset + KG scale (Phase 1 + Phase 2)
  //
  // Financial metrics (ARR/MRR/NRR) are explicitly null in the response
  // — the endpoint does NOT fabricate them. The screen surfaces a clear
  // "Requires Stripe integration" notice for any financial card.
  const { data: metrics, loading, error, refetch } = useApiResource<AdminMetricsResponse>(
    () => api.getAdminMetrics()
  );

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Investor Dashboard"
          desc={`Real platform metrics from /api/admin/metrics${metrics ? ` · scope: ${metrics.scope}` : ''}`}
          actions={<Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {loading && <LoadingSpinner label="Loading platform metrics from /api/admin/metrics…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && metrics && (
          <>
            {/* Real platform traction — NOT fabricated */}
            <div>
              <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">Platform Traction (real)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard title="Total Users" value={metrics.totalUsers.toLocaleString()} subtitle="from User table" icon={Users} />
                <StatCard title="Organizations" value={metrics.totalOrganizations.toLocaleString()} subtitle="from Organization table" icon={Building} />
                <StatCard title="Active Subscriptions" value={metrics.activeSubscriptions.toLocaleString()} subtitle="status='active'" icon={CreditCard} />
                <StatCard title="Audit Events (30d)" value={metrics.auditLogEventsLast30Days.toLocaleString()} subtitle="real audit-log rows" icon={Activity} />
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">Research Activity (real)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard title="Total Projects" value={metrics.totalProjects.toLocaleString()} subtitle="from Project table" icon={FolderKanban} />
                <StatCard title="Total Hypotheses" value={metrics.totalHypotheses.toLocaleString()} subtitle="from Hypothesis table" icon={Target} />
                <StatCard title="Validated Hypotheses" value={metrics.totalValidatedHypotheses.toLocaleString()} subtitle="status='validated'" icon={CheckCircle2} />
                <StatCard title="Evidence Packages" value={metrics.totalEvidencePackages.toLocaleString()} subtitle="from EvidencePackage table" icon={FileText} />
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">Data Scale (real)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard title="Dataset Nodes" value={metrics.dataset.nodesLoaded.toLocaleString()} subtitle={`${metrics.dataset.sourcesLoaded}/${metrics.dataset.sourcesTotal} sources`} icon={Database} />
                <StatCard title="Dataset Edges" value={metrics.dataset.edgesLoaded.toLocaleString()} subtitle={`source: ${metrics.dataset.source}`} icon={GitBranch} />
                <StatCard
                  title="KG Nodes"
                  value={metrics.knowledgeGraph ? metrics.knowledgeGraph.nodeCount.toLocaleString() : '—'}
                  subtitle={metrics.knowledgeGraph ? `source: ${metrics.knowledgeGraph.source}` : 'KG service unavailable'}
                  icon={Network}
                />
                <StatCard
                  title="KG Edges"
                  value={metrics.knowledgeGraph ? metrics.knowledgeGraph.edgeCount.toLocaleString() : '—'}
                  subtitle={metrics.knowledgeGraph ? 'from Phase 2 registry' : 'KG service unavailable'}
                  icon={Share2}
                />
              </div>
            </div>

            {/* Daily active users chart (real) */}
            {metrics.dailyActiveUsersLast7Days.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Daily Active Users (last 7 days, real)</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={metrics.dailyActiveUsersLast7Days.map(d => ({ day: d.day, users: d.activeUsers }))}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                        <YAxis />
                        <RechartsTooltip />
                        <Line type="monotone" dataKey="users" stroke={PRIMARY} strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Top actions chart (real) */}
            {metrics.topActionsLast30Days.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Top Actions (last 30 days, real)</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={metrics.topActionsLast30Days} layout="vertical" margin={{ left: 80, right: 20, top: 10, bottom: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis type="number" />
                        <YAxis type="category" dataKey="action" width={120} tick={{ fontSize: 11 }} />
                        <RechartsTooltip />
                        <Bar dataKey="count" fill={PRIMARY} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* EXPLICIT financial-metrics disclaimer — NOT fabricated */}
            <Card className="border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900">
              <CardContent className="p-4">
                <p className="text-sm font-semibold text-amber-900 dark:text-amber-100 mb-2">
                  Financial Metrics Not Available
                </p>
                <p className="text-xs text-amber-800 dark:text-amber-200">
                  {metrics.financials.note} The /api/admin/metrics endpoint explicitly returns
                  null for ARR, MRR, customer count, and NRR. These metrics require Stripe
                  (billing), CRM (customer count), and subscription-event history (NRR) integrations
                  that are not deployed. Showing fabricated financials to investors is securities
                  fraud — this screen refuses to do so.
                </p>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </FadeIn>
  );
}
