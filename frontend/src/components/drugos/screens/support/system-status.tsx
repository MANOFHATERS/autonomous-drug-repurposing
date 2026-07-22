'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type SystemStatus } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, XCircle, AlertTriangle, CheckCircle2, AlertCircle } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 32. SYSTEM STATUS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-014 ROOT FIX (Team Member 15, v108): The previous SystemStatusScreen
 * rendered 3 fabricated incidents ("Jun 10 Report generation delays 2h 15m",
 * etc.) and a fabricated "All Systems Operational" banner despite no real
 * health check. The real /api/system/status endpoint exists and returns
 * real service availability (auth, rxnorm, mesh, clinicalTrials, pubmed,
 * openfda, patentsview, kg, dataset, rl), but this screen NEVER called it.
 *
 * ROOT FIX: Wire the screen to `api.getSystemStatus()` (real call to
 * GET /api/system/status). Render real service states. Remove the
 * fabricated incidents list — there is no incident-tracking system
 * in the codebase.
 */
export function SystemStatusScreen() {
  const { data: status, loading, error, refetch } = useApiResource<SystemStatus>(
    () => api.getSystemStatus()
  );

  const services = status ? Object.entries(status.services).map(([key, svc]) => ({
    key,
    name: svc.service || key,
    available: svc.available,
    degraded: (svc as any).degraded,
    reason: svc.reason,
  })) : [];

  const allOperational = services.length > 0 && services.every(s => s.available && !s.degraded);
  const anyDegraded = services.some(s => s.degraded);
  const anyDown = services.some(s => !s.available);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="System Status"
          desc="Real-time platform health (from /api/system/status)"
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          }
        />

        {loading && <LoadingSpinner label="Loading system status…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && status && (
          <>
            {/* Real overall status banner — derived from actual service states */}
            <Card className={
              anyDown ? 'bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-900' :
              anyDegraded ? 'bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-900' :
              'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/30 dark:border-emerald-900'
            }>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  {anyDown ? (
                    <XCircle className="h-6 w-6 text-red-600" />
                  ) : anyDegraded ? (
                    <AlertTriangle className="h-6 w-6 text-amber-600" />
                  ) : (
                    <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                  )}
                  <div>
                    <h3 className={`font-semibold ${
                      anyDown ? 'text-red-800 dark:text-red-200' :
                      anyDegraded ? 'text-amber-800 dark:text-amber-200' :
                      'text-emerald-800 dark:text-emerald-200'
                    }`}>
                      {anyDown ? 'Some services unavailable' : anyDegraded ? 'Some services degraded' : 'All systems operational'}
                    </h3>
                    <p className={`text-sm ${
                      anyDown ? 'text-red-700 dark:text-red-300' :
                      anyDegraded ? 'text-amber-700 dark:text-amber-300' :
                      'text-emerald-700 dark:text-emerald-300'
                    }`}>
                      Last checked: {status.generatedAt ? new Date(status.generatedAt).toLocaleString() : 'just now'}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Real per-service status table */}
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-base">Service Status ({services.length} services)</CardTitle></CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Service</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Details</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {services.map(s => (
                      <TableRow key={s.key}>
                        <TableCell className="font-medium">{s.name}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span className={`w-2.5 h-2.5 rounded-full ${
                              s.available && !s.degraded ? 'bg-emerald-500' :
                              s.degraded ? 'bg-amber-500' :
                              'bg-red-500'
                            }`} />
                            <Badge variant={s.available && !s.degraded ? 'default' : s.degraded ? 'secondary' : 'destructive'}>
                              {s.available && !s.degraded ? 'operational' : s.degraded ? 'degraded' : 'unavailable'}
                            </Badge>
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{s.reason || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </>
        )}

        {!loading && !error && !status && (
          <EmptyState
            title="System status unavailable"
            description="The /api/system/status endpoint did not return data. This may be due to insufficient permissions (admin role required) or a server error."
          />
        )}

        {/* FE-014: Removed the fabricated "Recent Incidents" section.
            There is no incident-tracking system in the codebase, so any
            incidents shown would be fabricated. When an incident-tracking
            backend is added, this section can be wired to it. */}
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground">
            <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm font-medium">Incident history not tracked</p>
            <p className="text-xs mt-1 max-w-md mx-auto">
              There is no incident-tracking system in the codebase. When one is added
              (e.g. a StatusPage integration or in-DB incident log), this section will
              show real incident history. No fabricated incidents are rendered.
            </p>
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}
