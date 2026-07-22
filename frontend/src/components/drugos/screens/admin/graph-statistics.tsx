'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type KnowledgeGraphStatsResponse } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, Database, GitBranch, Layers } from 'lucide-react';
import { FadeIn, PageHeader, StatCard, PRIMARY, GREEN, ORANGE, RED } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 8. GRAPH STATISTICS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-004 ROOT FIX (Team Member 15, v108): The previous GraphStatisticsScreen
 * rendered hardcoded node counts (Drug 13,481, Disease 7,243, Gene 19,524,
 * Pathway 580, Protein 570,321), edge counts (treats 84,200, targets 195,400,
 * interacts 2.1M, associated 62,000, expressed 340,000), and 6 months of
 * fake growth data (Jan 480K nodes → Jun 611K nodes). The real
 * `/api/knowledge-graph` endpoint (no params) returns real
 * `nodeCount`/`edgeCount`/`nodeTypeCounts`/`edgeTypeCounts` from the
 * Phase 2 registry, but this screen NEVER called it.
 *
 * ROOT FIX: Wire the screen to `api.getKnowledgeGraphStats()` (which
 * calls GET /api/knowledge-graph). Render real `nodeTypeCounts` and
 * `edgeTypeCounts`. Remove fake growth data — there is no historical
 * snapshot store in the codebase, so we cannot show a trend. We show
 * the current snapshot only.
 */
export function GraphStatisticsScreen() {
  const { data: kgStats, loading, error, refetch } = useApiResource<KnowledgeGraphStatsResponse>(
    () => api.getKnowledgeGraphStats()
  );

  // Map node-type labels to colors. The Phase 2 registry uses canonical
  // type names: Compound, Protein, Pathway, Disease, ClinicalOutcomes,
  // plus non-canonical: AdverseEvent.
  const nodeTypeColors: Record<string, string> = {
    Compound: PRIMARY,
    Drug: PRIMARY,
    Protein: '#8B5CF6',
    Pathway: ORANGE,
    Disease: RED,
    ClinicalOutcomes: GREEN,
    AdverseEvent: '#C0392B',
  };

  const nodeEntries = kgStats
    ? Object.entries(kgStats.nodeTypeCounts).map(([type, count]) => ({
        type,
        count,
        color: nodeTypeColors[type] ?? '#94A3B8',
      }))
    : [];
  const edgeEntries = kgStats
    ? Object.entries(kgStats.edgeTypeCounts).map(([type, count]) => ({ type, count }))
    : [];
  const nonCanonicalEntries = kgStats
    ? Object.entries(kgStats.nonCanonicalNodeCounts || {}).map(([type, count]) => ({ type, count }))
    : [];

  const isNoData = kgStats?.source === 'none';

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Knowledge Graph Statistics"
          desc={
            loading
              ? 'Loading knowledge graph statistics…'
              : kgStats
                ? `${kgStats.nodeCount.toLocaleString()} canonical nodes · ${kgStats.edgeCount.toLocaleString()} edges (source: ${kgStats.source})`
                : 'Knowledge graph statistics'
          }
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          }
        />

        {loading && <LoadingSpinner label="Loading knowledge graph statistics from /api/knowledge-graph…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && isNoData && (
          <EmptyState
            title="Knowledge graph not built yet"
            description="Phase 2 of the build pipeline has not been run. Run the Phase 2 KG builder to produce real graph statistics (node counts, edge counts, source breakdowns). The /api/knowledge-graph endpoint reads from the Phase 2 registry — once the builder completes, refresh this page to see real statistics."
          />
        )}

        {!loading && !error && kgStats && !isNoData && (
          <>
            {/* Stat cards — real totals */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <StatCard title="Total Canonical Nodes" value={kgStats.nodeCount.toLocaleString()} icon={Database} />
              <StatCard title="Total Edges" value={kgStats.edgeCount.toLocaleString()} icon={GitBranch} />
              <StatCard title="Sources Loaded" value={kgStats.sources.length} icon={Layers} />
            </div>

            {/* Node distribution — real per-type counts */}
            {nodeEntries.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">Node Distribution (canonical types)</CardTitle></CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
                    {nodeEntries.map(n => (
                      <Card key={n.type}>
                        <CardContent className="p-4">
                          <div className="flex items-center gap-2 mb-2">
                            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: n.color }} />
                            <span className="text-xs font-medium text-muted-foreground">{n.type}</span>
                          </div>
                          <p className="text-xl font-bold">{n.count.toLocaleString()}</p>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Edge types table — real counts */}
            {edgeEntries.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">Edge Types</CardTitle></CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader><TableRow><TableHead>Edge Type</TableHead><TableHead>Count</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {edgeEntries.map(e => (
                        <TableRow key={e.type}>
                          <TableCell className="font-medium capitalize">{e.type}</TableCell>
                          <TableCell>{e.count.toLocaleString()}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            {/* Non-canonical node types — surfaced for transparency, NOT summed into nodeCount */}
            {nonCanonicalEntries.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">Non-Canonical Node Types (excluded from total)</CardTitle></CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader><TableRow><TableHead>Type</TableHead><TableHead>Count</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {nonCanonicalEntries.map(e => (
                        <TableRow key={e.type}>
                          <TableCell className="font-medium">{e.type}</TableCell>
                          <TableCell>{e.count.toLocaleString()}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            {/* Source-level breakdown */}
            {kgStats.sources.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">Sources</CardTitle></CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Loaded</TableHead><TableHead>Rows</TableHead><TableHead>SHA256</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {kgStats.sources.map(s => (
                        <TableRow key={s.name}>
                          <TableCell className="font-medium">{s.name}</TableCell>
                          <TableCell>
                            <Badge variant={s.loaded ? 'default' : 'secondary'}>{s.loaded ? 'loaded' : 'missing'}</Badge>
                            {s.loadedReason && <p className="text-[10px] text-muted-foreground mt-0.5">{s.loadedReason}</p>}
                          </TableCell>
                          <TableCell>{(s.rows ?? 0).toLocaleString()}</TableCell>
                          <TableCell className="font-mono text-[10px] text-muted-foreground">{s.sha256 ? s.sha256.slice(0, 16) + '…' : '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            {kgStats.note && (
              <p className="text-xs text-muted-foreground italic">{kgStats.note}</p>
            )}
          </>
        )}
      </div>
    </FadeIn>
  );
}
