'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay } from '../../use-api-data';
import { api, type SystemStatus, type AuditLog } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, XCircle, CheckCircle2, Activity } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 1. PIPELINE SCREEN
// ═══════════════════════════════════════════
/**
 * Issue 303 (audit 301-320): Wire Pipeline screen to /api/system/status.
 *
 * The previous PipelineScreen either:
 *   (a) rendered 8 hardcoded fake drug-disease pairs with fake scores, OR
 *   (b) after a partial fix, rendered a static EmptyState that lied
 *       "/api/pipeline endpoint not implemented" — even though the
 *       system status endpoint and audit logs DO contain real pipeline
 *       activity (every hypothesis_create, hypothesis_validate,
 *       dataset_query, etc. is recorded).
 *
 * ROOT FIX: This screen now calls TWO real endpoints in parallel:
 *   - GET /api/system/status — shows real service availability (auth,
 *     rxnorm, mesh, clinicalTrials, pubmed, openfda, patentsview, kg,
 *     dataset, rl). These services ARE the pipeline — without them no
 *     repurposing candidate can be produced.
 *   - GET /api/audit-logs?limit=50 — shows real recent pipeline
 *     activity: hypothesis validations, dataset queries, evidence
 *     package builds, etc. Filtered to actions that represent actual
 *     repurposing-pipeline work (hypothesis_*, dataset_query,
 *     evidence_*, rl_*).
 *
 * No fabricated drug-disease pairs. No fabricated stage counts. No
 * fabricated scores. The screen shows what is REALLY happening in
 * the pipeline right now: which services are up, and which hypothesis
 * validations and evidence-package builds have actually occurred.
 */
export function PipelineScreen() {
  const { data: status, loading: statusLoading, error: statusError, refetch: refetchStatus } = useApiResource<SystemStatus>(
    () => api.getSystemStatus()
  );
  const { data: auditData, loading: auditLoading, error: auditError, refetch: refetchAudit } = useApiResource<{ items: AuditLog[]; total: number }>(
    () => api.listAuditLogs(50, 0)
  );

  const services = status ? Object.entries(status.services).map(([key, svc]) => ({
    key,
    name: svc.service || key,
    available: svc.available,
    reason: svc.reason,
  })) : [];

  // PipelineScreen uses anyDown (not allOperational) for the status banner —
  // we keep the explicit name for self-documenting intent.
  const anyDown = services.some(s => !s.available);

  // Filter audit logs to pipeline-relevant actions: hypothesis lifecycle,
  // dataset queries, evidence package builds, RL ranker invocations.
  const pipelineActions = (auditData?.items ?? []).filter(l => {
    const a = l.action.toLowerCase();
    return a.includes('hypothesis') ||
           a.includes('dataset') ||
           a.includes('evidence') ||
           a.includes('rl_') ||
           a.includes('predict') ||
           a.includes('validate');
  });

  const refetchAll = () => { refetchStatus(); refetchAudit(); };
  const loading = statusLoading || auditLoading;

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Repurposing Pipeline"
          desc="Real pipeline service status and recent hypothesis activity"
          actions={<Button variant="outline" size="sm" onClick={refetchAll} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {statusError && <ErrorDisplay error={statusError} onRetry={refetchStatus} />}
        {auditError && <ErrorDisplay error={auditError} onRetry={refetchAudit} />}

        {loading && <LoadingSpinner label="Loading pipeline status from /api/system/status…" />}

        {!loading && status && (
          <>
            <Card className={
              anyDown ? 'bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-900' :
              'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/30 dark:border-emerald-900'
            }>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  {anyDown ? <XCircle className="h-6 w-6 text-red-600" /> : <CheckCircle2 className="h-6 w-6 text-emerald-600" />}
                  <div>
                    <h3 className={`font-semibold ${anyDown ? 'text-red-800 dark:text-red-200' : 'text-emerald-800 dark:text-emerald-200'}`}>
                      {anyDown ? 'Some pipeline services unavailable' : 'All pipeline services operational'}
                    </h3>
                    <p className={`text-sm ${anyDown ? 'text-red-700 dark:text-red-300' : 'text-emerald-700 dark:text-emerald-300'}`}>
                      Last checked: {status.generatedAt ? new Date(status.generatedAt).toLocaleString() : 'just now'} · {services.length} services
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {services.map(s => (
                <Card key={s.key}>
                  <CardContent className="p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{s.name}</span>
                      <Badge variant={s.available ? 'default' : 'destructive'}>
                        {s.available ? 'operational' : 'unavailable'}
                      </Badge>
                    </div>
                    {s.reason && <p className="text-xs text-muted-foreground mt-1">{s.reason}</p>}
                  </CardContent>
                </Card>
              ))}
            </div>
          </>
        )}

        {!loading && !statusError && !auditError && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                Recent Pipeline Activity
                {pipelineActions.length > 0 && (
                  <Badge variant="outline" className="ml-2">{pipelineActions.length} events</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {pipelineActions.length === 0 ? (
                <div className="p-6 text-center text-sm text-muted-foreground">
                  <Activity className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="font-medium">No pipeline activity yet</p>
                  <p className="text-xs mt-1 max-w-md mx-auto">
                    Validate a hypothesis (Project → Hypothesis → Validate), run a dataset query,
                    or build an evidence package. Those events will appear here in real time.
                  </p>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Action</TableHead>
                      <TableHead>Actor</TableHead>
                      <TableHead>Resource</TableHead>
                      <TableHead>When</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pipelineActions.slice(0, 20).map(l => (
                      <TableRow key={l.id}>
                        <TableCell>
                          <Badge variant="outline" className="font-mono text-xs">{l.action}</Badge>
                        </TableCell>
                        <TableCell className="text-sm">{l.actorName}</TableCell>
                        <TableCell className="text-xs text-muted-foreground font-mono">{l.resource || '—'}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(l.createdAt).toLocaleString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </FadeIn>
  );
}
