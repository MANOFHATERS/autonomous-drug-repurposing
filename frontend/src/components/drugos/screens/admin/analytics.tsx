'use client';

import { useMemo } from 'react';
import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type AuditLog } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { RefreshCw, Activity, Users, BarChart3 } from 'lucide-react';
import { FadeIn, PageHeader, StatCard, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 2. ANALYTICS SCREEN
// ═══════════════════════════════════════════
/**
 * Issue 304 (audit 301-320): Wire Analytics screen to /api/audit-logs.
 *
 * The previous AnalyticsScreen rendered 6 months of fabricated query
 * volumes, API call counts, "top diseases" with fabricated growth
 * percentages, and 4 fabricated stat cards. A pharma executive
 * reviewing platform ROI saw fabricated telemetry.
 *
 * ROOT FIX: There is no separate /api/analytics endpoint, but the
 * existing /api/audit-logs endpoint records EVERY user action (login,
 * search, hypothesis_create, dataset_query, billing_change, etc.).
 * This screen now aggregates those real audit-log rows to derive:
 *
 *   - Total events (last 30 days): COUNT(audit logs in last 30d)
 *   - Unique active users (last 30d): COUNT(DISTINCT userId)
 *   - Top actions (last 30d): GROUP BY action, COUNT, ORDER BY count
 *   - Daily event volume (last 14 days): GROUP BY DATE(createdAt)
 *
 * Every number on this screen is computed from REAL audit-log rows
 * that were written by real user actions. No fabricated metrics.
 */
export function AnalyticsScreen() {
  const { data: auditData, loading, error, refetch } = useApiResource<{ items: AuditLog[]; total: number }>(
    () => api.listAuditLogs(500, 0)
  );

  const logs = auditData?.items ?? [];

  // Aggregate real metrics from audit-log rows. Wrap all computations in
  // useMemo so the deps arrays are stable across re-renders (avoids the
  // react-compiler "Compilation Skipped: Existing memoization could not
  // be preserved" error).
  const { totalEvents30d, uniqueUsers30d, actionCounts, dailyVolume } = useMemo(() => {
    const now = Date.now();
    const thirtyDaysAgo = now - 30 * 24 * 60 * 60 * 1000;
    const fourteenDaysAgo = now - 14 * 24 * 60 * 60 * 1000;

    const recentLogs = logs.filter(l => new Date(l.createdAt).getTime() > thirtyDaysAgo);
    const totalEvents = recentLogs.length;
    const uniqueUsers = new Set(recentLogs.map(l => l.userId).filter(Boolean)).size;

    const actionMap = new Map<string, number>();
    for (const l of recentLogs) {
      actionMap.set(l.action, (actionMap.get(l.action) || 0) + 1);
    }
    const topActions = Array.from(actionMap.entries())
      .map(([action, count]) => ({ action, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);

    const dayMap = new Map<string, number>();
    for (const l of logs) {
      const t = new Date(l.createdAt).getTime();
      if (t < fourteenDaysAgo) continue;
      const day = new Date(l.createdAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      dayMap.set(day, (dayMap.get(day) || 0) + 1);
    }
    const daily = Array.from(dayMap.entries())
      .map(([day, count]) => ({ day, count }));

    return {
      totalEvents30d: totalEvents,
      uniqueUsers30d: uniqueUsers,
      actionCounts: topActions,
      dailyVolume: daily,
    };
  }, [logs]);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Analytics"
          desc="Real platform usage derived from /api/audit-logs (last 30 days)"
          actions={<Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {loading && <LoadingSpinner label="Loading audit logs…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <StatCard
                title="Events (last 30 days)"
                value={totalEvents30d.toLocaleString()}
                subtitle={`of ${logs.length.toLocaleString()} total audit entries`}
                icon={Activity}
              />
              <StatCard
                title="Active Users (last 30 days)"
                value={uniqueUsers30d}
                subtitle="distinct userIds in audit logs"
                icon={Users}
              />
              <StatCard
                title="Action Types (last 30 days)"
                value={actionCounts.length}
                subtitle="distinct action categories"
                icon={BarChart3}
              />
            </div>

            {logs.length === 0 && (
              <EmptyState
                title="No audit log data yet"
                description="Once users start logging in, searching drugs, and creating hypotheses, those actions will be recorded in the audit log and aggregated here. No fabricated metrics are shown."
              />
            )}

            {actionCounts.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Top Actions (last 30 days)</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={actionCounts} layout="vertical" margin={{ left: 80, right: 20, top: 10, bottom: 10 }}>
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

            {dailyVolume.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Daily Event Volume (last 14 days)</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={dailyVolume} margin={{ left: 0, right: 20, top: 10, bottom: 10 }}>
                        <defs>
                          <linearGradient id="colorEvents" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor={PRIMARY} stopOpacity={0.8} />
                            <stop offset="95%" stopColor={PRIMARY} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                        <YAxis />
                        <RechartsTooltip />
                        <Area type="monotone" dataKey="count" stroke={PRIMARY} fillOpacity={1} fill="url(#colorEvents)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            )}

            <p className="text-xs text-muted-foreground italic">
              All metrics derived from real AuditLog rows written by actual user actions.
              No fabricated query volumes, no fabricated growth percentages, no fabricated user counts.
            </p>
          </>
        )}
      </div>
    </FadeIn>
  );
}
