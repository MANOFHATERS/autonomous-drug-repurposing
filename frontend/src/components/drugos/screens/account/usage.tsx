'use client';

import { useMemo } from 'react';
import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type AuditLog, type Subscription, type Plan } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Progress } from '@/components/ui/progress';
import { RefreshCw, Code, Activity, Users, Database } from 'lucide-react';
import { FadeIn, PageHeader, StatCard } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 11. USAGE SCREEN
// ═══════════════════════════════════════════
/**
 * FE-006 ROOT FIX (Team Member 15, v108): The previous UsageScreen
 * rendered 7 days of fabricated query/API volumes (Mon 45 queries/6800
 * API → Sun 18/2800) and 4 fabricated stat cards ("Queries This Month
 * 342/1,000", "API Calls Today 4,523", "Storage Used 2.4 GB",
 * "Team Seats 8/25"). No API call. No banner. A billing admin saw
 * fabricated metering and could trigger overage charges or upgrade
 * prompts on fake data.
 *
 * ROOT FIX: There is no `/api/billing/usage` endpoint in the codebase
 * yet. Per the issue spec we render an honest EmptyState for the
 * query/API/storage usage — these numbers do not exist anywhere. The
 * one real number we CAN show is the seat count, which comes from
 * `api.getSubscription()` (real subscription data, including seats).
 */
export function UsageScreen() {
  // Issue 308 (audit 301-320): Wire to /api/audit-logs. The previous
  // UsageScreen rendered "—" placeholders for queries/calls/storage and
  // showed only seat count. Now we aggregate REAL API call counts from
  // audit logs: every API call is recorded as an audit-log row, so we
  // can derive actual usage per user, per action, and per day.
  const { data: auditData, loading: auditLoading, error: auditError, refetch } = useApiResource<{ items: AuditLog[]; total: number }>(
    () => api.listAuditLogs(500, 0)
  );
  const { data: subData } = useApiResource<{ subscription: Subscription | null; plans: Plan[] }>(
    () => api.getSubscription()
  );
  const subscription = subData?.subscription ?? null;
  const logs = auditData?.items ?? [];

  // Aggregate all time-based and grouping computations in a single useMemo
  // so deps arrays are stable and the React Compiler can memoize correctly.
  const {
    callsToday,
    callsThisMonth,
    distinctUsersToday,
    topEndpoints,
  } = useMemo(() => {
    const todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);
    const monthStart = new Date(); monthStart.setDate(1); monthStart.setHours(0, 0, 0, 0);
    const todayMs = todayStart.getTime();
    const monthMs = monthStart.getTime();

    const todayLogs = logs.filter(l => new Date(l.createdAt).getTime() >= todayMs);
    const monthLogs = logs.filter(l => new Date(l.createdAt).getTime() >= monthMs);
    const todayUsers = new Set(todayLogs.map(l => l.userId).filter(Boolean)).size;

    const endpointMap = new Map<string, number>();
    for (const l of logs) {
      const r = l.resource || '(none)';
      const prefix = r.split(':')[0] || r;
      endpointMap.set(prefix, (endpointMap.get(prefix) || 0) + 1);
    }
    const top = Array.from(endpointMap.entries())
      .map(([endpoint, count]) => ({ endpoint, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 6);

    return {
      callsToday: todayLogs.length,
      callsThisMonth: monthLogs.length,
      distinctUsersToday: todayUsers,
      topEndpoints: top,
    };
  }, [logs]);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Usage"
          desc="Real API usage derived from /api/audit-logs"
          actions={<Button variant="outline" size="sm" onClick={() => refetch()} disabled={auditLoading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${auditLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {auditLoading && <LoadingSpinner label="Loading usage data from /api/audit-logs…" />}
        {auditError && <ErrorDisplay error={auditError} onRetry={() => refetch()} />}

        {!auditLoading && !auditError && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <StatCard
                title="API Calls Today"
                value={callsToday.toLocaleString()}
                subtitle={`${distinctUsersToday} active user${distinctUsersToday === 1 ? '' : 's'} today`}
                icon={Code}
              />
              <StatCard
                title="API Calls This Month"
                value={callsThisMonth.toLocaleString()}
                subtitle={`of ${logs.length.toLocaleString()} total audit entries`}
                icon={Activity}
              />
              <StatCard
                title="Team Seats (real)"
                value={subscription ? `${subscription.seats} seat${subscription.seats === 1 ? '' : 's'}` : '—'}
                subtitle={subscription ? `Plan: ${subscription.plan}` : 'no subscription'}
                icon={Users}
              />
              <StatCard
                title="Audit Entries Total"
                value={logs.length.toLocaleString()}
                subtitle="from /api/audit-logs"
                icon={Database}
              />
            </div>

            {topEndpoints.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Top API Resources (by audit-log resource prefix)</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Resource</TableHead>
                        <TableHead>Call Count</TableHead>
                        <TableHead>Share</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {topEndpoints.map(e => (
                        <TableRow key={e.endpoint}>
                          <TableCell className="font-mono text-sm">{e.endpoint}</TableCell>
                          <TableCell>{e.count.toLocaleString()}</TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Progress value={logs.length > 0 ? (e.count / logs.length) * 100 : 0} className="h-2 w-24" />
                              <span className="text-xs text-muted-foreground">
                                {logs.length > 0 ? Math.round((e.count / logs.length) * 100) : 0}%
                              </span>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            {logs.length === 0 && (
              <EmptyState
                title="No usage data yet"
                description="Once users start making API calls (searching drugs, validating hypotheses, building evidence packages), those calls will be recorded in the audit log and aggregated here."
              />
            )}

            <p className="text-xs text-muted-foreground italic">
              All usage metrics derived from real AuditLog rows written by actual API calls.
              No fabricated query counts, no fabricated storage usage.
            </p>
          </>
        )}
      </div>
    </FadeIn>
  );
}
