'use client';

import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type DatasetStatsResponse } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { RefreshCw } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 7. DATA SOURCES SCREEN
// ═══════════════════════════════════════════
/**
 * FE-003 ROOT FIX (Team Member 15, v108): The previous DataSourcesScreen
 * rendered 8 hardcoded fake data sources ("DrugBank 13,481 drugs synced
 * 2 hours ago", "ChEMBL 2.1M compounds", "UniProt 570K proteins",
 * "PubMed 36M articles", etc.). The "Sync" button called `handleSync(name)`
 * which was just `setTimeout(() => setSyncing(null), 2000)` — a fake
 * 2-second spinner with NO backend call. The real `/api/dataset`
 * endpoint exists and returns real source stats, but this screen
 * NEVER called it.
 *
 * ROOT FIX: Wire the screen to `api.getDatasetStats()` (which calls
 * GET /api/dataset). Render the real `sources[]` array with real
 * `loaded` / `rowsLoaded` / `sha256` fields. Remove the fake
 * `handleSync` — the Sync button is removed entirely because there
 * is no `/api/dataset/refresh` endpoint yet. Adding one requires
 * implementing a backend route that triggers Phase 1 re-ingestion,
 * which is outside this screen's scope.
 *
 * SCIENTIFIC INTEGRITY: never render fabricated drug/compound/protein
 * counts. If getDatasetStats() returns no sources (status='no_data'),
 * render an honest EmptyState that tells the admin to run Phase 1.
 */
export function DataSourcesScreen() {
  // useApiResource fires on mount and surfaces loading / error / data.
  const { data: stats, loading, error, refetch } = useApiResource<DatasetStatsResponse>(
    () => api.getDatasetStats()
  );

  const sources = stats?.sources ?? [];
  const totalLoaded = sources.filter(s => s.loaded).length;
  const isNoData = stats?.source === 'none' || (stats && (stats as any).status === 'no_data');

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Data Sources"
          desc={loading ? 'Loading data source stats…' : `${totalLoaded} of ${sources.length} sources loaded`}
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh stats
            </Button>
          }
        />

        {/* Backend source + pipeline version metadata — real, not fabricated */}
        {stats && (
          <Card>
            <CardContent className="p-4 text-xs text-muted-foreground grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div><span className="font-medium">Backend:</span> {stats.backend || stats.source}</div>
              <div><span className="font-medium">Nodes loaded:</span> {stats.nodesLoaded?.toLocaleString() ?? 0}</div>
              <div><span className="font-medium">Edges loaded:</span> {stats.edgesLoaded?.toLocaleString() ?? 0}</div>
              <div><span className="font-medium">Generated at:</span> {stats.generatedAt ? new Date(stats.generatedAt).toLocaleString() : '—'}</div>
              {stats.pipelineVersion && <div><span className="font-medium">Pipeline:</span> {stats.pipelineVersion}</div>}
              {stats.schemaVersion && <div><span className="font-medium">Schema:</span> {stats.schemaVersion}</div>}
              {stats.bridgeVersion && <div><span className="font-medium">Bridge:</span> {stats.bridgeVersion}</div>}
            </CardContent>
          </Card>
        )}

        {loading && <LoadingSpinner label="Loading data source statistics from /api/dataset…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && isNoData && (
          <EmptyState
            title="No data ingested yet"
            description="Phase 1 of the build pipeline has not been run. Run the Phase 1 data ingestion pipeline (ChEMBL, DrugBank, UniProt, STRING, DisGeNET, OMIM, PubChem) to populate these statistics. The /api/dataset endpoint reads from the Phase 1 checkpoint file — once ingestion completes, refresh this page to see real source counts and SHA256 hashes."
          />
        )}

        {!loading && !error && !isNoData && sources.length === 0 && (
          <EmptyState
            title="No data sources registered"
            description="The dataset service returned no sources. This is unexpected — please verify the Phase 1 pipeline configuration and try refreshing."
          />
        )}

        {!loading && !error && sources.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {sources.map(s => (
              <Card key={s.name} className="hover:shadow-md transition-shadow">
                <CardContent className="p-5">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="font-semibold text-sm">{s.name}</h3>
                      <p className="text-xs text-muted-foreground">
                        {s.loaded
                          ? `${(s.rowsLoaded ?? 0).toLocaleString()} rows loaded`
                          : 'Not loaded'}
                      </p>
                    </div>
                    <Badge variant={s.loaded ? 'default' : 'secondary'}>
                      {s.loaded ? 'loaded' : 'missing'}
                    </Badge>
                  </div>
                  {s.sha256 && (
                    <div className="text-[10px] font-mono text-muted-foreground break-all">
                      sha256: {s.sha256}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Warnings and errors from the dataset service — real, surfaced honestly */}
        {stats && stats.warnings.length > 0 && (
          <Card className="border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900">
            <CardHeader className="pb-2"><CardTitle className="text-base text-amber-900 dark:text-amber-200">Warnings ({stats.warnings.length})</CardTitle></CardHeader>
            <CardContent>
              <ul className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
                {stats.warnings.map((w, i) => <li key={i} className="font-mono">• {w}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}
        {stats && stats.errors.length > 0 && (
          <Card className="border-red-200 bg-red-50 dark:bg-red-950/30 dark:border-red-900">
            <CardHeader className="pb-2"><CardTitle className="text-base text-red-900 dark:text-red-200">Errors ({stats.errors.length})</CardTitle></CardHeader>
            <CardContent>
              <ul className="space-y-1 text-xs text-red-800 dark:text-red-300">
                {stats.errors.map((e, i) => <li key={i} className="font-mono">• {e}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </FadeIn>
  );
}
