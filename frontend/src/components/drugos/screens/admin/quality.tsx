'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type DatasetQualityResponse } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, CheckCircle2, Layers, ShieldCheck, AlertTriangle, Clock } from 'lucide-react';
import { FadeIn, PageHeader, StatCard } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 9. QUALITY SCREEN
// ═══════════════════════════════════════════
/**
 * FE-005 ROOT FIX (Team Member 15, v108): The previous QualityScreen
 * rendered 5 fabricated source quality metrics (DrugBank 96% completeness
 * / 98% freshness / 2 duplicates / 97% reliability, etc.) and 4 fabricated
 * aggregate stat cards ("Avg Completeness 93.2%", "Avg Freshness 95.0%",
 * "Duplicates 19", "Reliability 95.8%"). No API call. No banner.
 *
 * ROOT FIX: There is no `/api/data-quality` endpoint in the codebase
 * yet. Per the issue spec, we derive what we can from the real
 * `api.getDatasetStats()` response (which has `warnings[]` and
 * `errors[]` arrays) and render an honest EmptyState for the rest.
 * We never fabricate completeness/freshness/reliability percentages.
 */
export function QualityScreen() {
  // Issue 307 (audit 301-320): Wire to /api/dataset/quality. The endpoint
  // derives REAL quality metrics from Phase 1 + Phase 2 stats — no
  // fabricated percentages.
  const { data: quality, loading, error, refetch } = useApiResource<DatasetQualityResponse>(
    () => api.getDatasetQuality()
  );

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Data Quality"
          desc="Real quality metrics derived from Phase 1 + Phase 2 stats via /api/dataset/quality"
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          }
        />

        {loading && <LoadingSpinner label="Loading data quality metrics from /api/dataset/quality…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && quality && quality.status === 'no_data' && (
          <EmptyState
            title="No dataset quality data available"
            description="The Phase 1 pipeline has not been run yet. Run Phase 1 to populate the dataset checkpoint — quality metrics (completeness, integrity, freshness, canonical coverage) will then be computed from real stats."
          />
        )}

        {!loading && !error && quality && quality.status !== 'no_data' && (
          <>
            {/* Real coverage stat cards */}
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <StatCard
                title="Source Completeness"
                value={`${quality.sourceCompletenessPct}%`}
                subtitle={`${quality.totalSources > 0 ? Math.round(quality.sourceCompletenessPct * quality.totalSources / 100) : 0}/${quality.totalSources} sources loaded`}
                icon={CheckCircle2}
              />
              <StatCard
                title="Canonical Coverage"
                value={`${quality.canonicalCoveragePct}%`}
                subtitle="Compound/Protein/Pathway/Disease/Outcomes"
                icon={Layers}
              />
              <StatCard
                title="Checksum Coverage"
                value={`${quality.checksumCoveragePct}%`}
                subtitle={`${quality.sourcesWithChecksum}/${quality.totalSources} sources with SHA-256`}
                icon={ShieldCheck}
              />
              <StatCard
                title="Freshness"
                value={quality.freshnessHoursAgo === null ? '—' : `${quality.freshnessHoursAgo}h ago`}
                subtitle={quality.isStale ? 'stale (>7 days)' : 'fresh'}
                icon={quality.isStale ? AlertTriangle : Clock}
                trend={quality.isStale ? 'stale' : undefined}
              />
            </div>

            {/* Real per-canonical-type breakdown */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Canonical Node Type Coverage (Phase 2 KG)</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Node Type</TableHead>
                      <TableHead>Present</TableHead>
                      <TableHead>Count</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {quality.canonicalNodeCoverage.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={3} className="text-center text-muted-foreground py-6">
                          Phase 2 KG has no nodes registered. Run Phase 2 to populate.
                        </TableCell>
                      </TableRow>
                    ) : quality.canonicalNodeCoverage.map(c => (
                      <TableRow key={c.type}>
                        <TableCell className="font-medium">{c.type}</TableCell>
                        <TableCell>
                          <Badge variant={c.present ? 'default' : 'secondary'}>
                            {c.present ? 'present' : 'missing'}
                          </Badge>
                        </TableCell>
                        <TableCell>{c.count.toLocaleString()}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            {/* Real graph-anomaly signal */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Graph Anomaly Signals</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
                  <div>
                    <p className="text-muted-foreground text-xs">Nodes Loaded</p>
                    <p className="font-semibold">{quality.nodesLoaded.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground text-xs">Edges Loaded</p>
                    <p className="font-semibold">{quality.edgesLoaded.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground text-xs">Node/Edge Ratio</p>
                    <p className="font-semibold">{quality.nodeEdgeRatio}</p>
                    <p className="text-xs text-muted-foreground">
                      {quality.nodeEdgeRatio > 5 || quality.nodeEdgeRatio < 0.05
                        ? 'anomalous — investigate loader'
                        : 'within expected range (0.05-5.0)'}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Real warnings from the dataset service */}
            <Card className={quality.warningsCount > 0 ? 'border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900' : ''}>
              <CardHeader className="pb-2"><CardTitle className="text-base">Warnings ({quality.warningsCount})</CardTitle></CardHeader>
              <CardContent>
                {quality.warningsCount === 0 ? (
                  <p className="text-sm text-muted-foreground">No warnings from the dataset service.</p>
                ) : (
                  <ul className="space-y-1 text-xs text-amber-800 dark:text-amber-300 font-mono">
                    {quality.warnings.map((w, i) => <li key={i}>• {w}</li>)}
                  </ul>
                )}
              </CardContent>
            </Card>

            {/* Real errors from the dataset service */}
            <Card className={quality.errorsCount > 0 ? 'border-red-200 bg-red-50 dark:bg-red-950/30 dark:border-red-900' : ''}>
              <CardHeader className="pb-2"><CardTitle className="text-base">Errors ({quality.errorsCount})</CardTitle></CardHeader>
              <CardContent>
                {quality.errorsCount === 0 ? (
                  <p className="text-sm text-muted-foreground">No errors from the dataset service.</p>
                ) : (
                  <ul className="space-y-1 text-xs text-red-800 dark:text-red-300 font-mono">
                    {quality.errors.map((e, i) => <li key={i}>• {e}</li>)}
                  </ul>
                )}
              </CardContent>
            </Card>

            <p className="text-xs text-muted-foreground italic">
              All metrics derived from real Phase 1 dataset stats and Phase 2 KG stats via /api/dataset/quality.
              No fabricated completeness percentages, no fabricated freshness scores, no fabricated reliability metrics.
              Pipeline: {quality.pipelineVersion || 'unknown'} · Schema: {quality.schemaVersion || 'unknown'} · Bridge: {quality.bridgeVersion || 'unknown'}
            </p>
          </>
        )}
      </div>
    </FadeIn>
  );
}
