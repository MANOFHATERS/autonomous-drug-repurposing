'use client';

import { useApiList, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type Project } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, FolderKanban, Activity, Target, MessageSquare } from 'lucide-react';
import { FadeIn, PageHeader, StatCard } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 12. DEALS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-007 ROOT FIX (Team Member 15, v108): The previous DealsScreen
 * rendered 4 fabricated licensing deals ("Memantine/Huntington's/
 * NeuroPharm Inc/Term Sheet/$2.4M", "Naltrexone/MS/BioRepath Corp/
 * Due Diligence/$5.1M", etc.) and 4 fabricated stat cards
 * ("Active Deals 4", "Pipeline Value $19.5M", "Avg Deal Size $4.9M",
 * "Close Rate 68%"). No API call. No banner. A biz-dev user could
 * contact fictional licensees about fictional deals. The "$19.5M
 * pipeline value" could be reported to investors.
 *
 * ROOT FIX: There is no `/api/deals` endpoint in the codebase. Deal
 * pipeline is not a core drug-repurposing feature. Per the issue
 * spec we render an honest EmptyState — no fabricated deals, no
 * fabricated licensees, no fabricated dollar values.
 */
export function DealsScreen() {
  // Issue 309 (audit 301-320): Wire to /api/projects. Projects ARE the
  // "deals" — each project represents a research collaboration between
  // the platform and a pharma partner around specific drug-disease
  // hypotheses. We render the REAL project list (with hypothesis counts
  // and statuses), not fabricated deal data.
  const { data: projData, loading, error, refetch } = useApiList<{ items: Project[] }>(
    () => api.listProjects(),
    []
  );
  const projects = projData?.items ?? [];

  const activeProjects = projects.filter(p => p.status === 'active').length;
  const totalHypotheses = projects.reduce((sum, p) => sum + (p._count?.hypotheses || 0), 0);
  const totalComments = projects.reduce((sum, p) => sum + (p._count?.comments || 0), 0);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Discovery Deals"
          desc="Real research collaborations from /api/projects"
          actions={<Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {loading && <LoadingSpinner label="Loading projects from /api/projects…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <StatCard title="Total Projects" value={projects.length} subtitle="from /api/projects" icon={FolderKanban} />
              <StatCard title="Active Projects" value={activeProjects} subtitle="status='active'" icon={Activity} />
              <StatCard title="Hypotheses Tracked" value={totalHypotheses} subtitle="across all projects" icon={Target} />
              <StatCard title="Collaboration Comments" value={totalComments} subtitle="across all projects" icon={MessageSquare} />
            </div>

            {projects.length === 0 ? (
              <EmptyState
                title="No deals yet"
                description="There are no /api/deals endpoints, but /api/projects serves as the real research-collaboration tracking surface. Create a project to track a pharma partner engagement around specific drug-disease hypotheses."
              />
            ) : (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Active Research Collaborations ({projects.length})</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Project</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Visibility</TableHead>
                        <TableHead>Hypotheses</TableHead>
                        <TableHead>Comments</TableHead>
                        <TableHead>Updated</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {projects.map(p => (
                        <TableRow key={p.id}>
                          <TableCell>
                            <div>
                              <p className="font-medium">{p.name}</p>
                              <p className="text-xs text-muted-foreground">{p.description || 'No description'}</p>
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant={p.status === 'active' ? 'default' : 'secondary'} className="capitalize">{p.status}</Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="capitalize text-xs">{p.visibility}</Badge>
                          </TableCell>
                          <TableCell>{p._count?.hypotheses ?? 0}</TableCell>
                          <TableCell>{p._count?.comments ?? 0}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {new Date(p.updatedAt).toLocaleDateString()}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            <p className="text-xs text-muted-foreground italic">
              All deal/collaboration data derived from real /api/projects rows.
              No fabricated licensing deals, no fabricated dollar values, no fabricated partner names.
            </p>
          </>
        )}
      </div>
    </FadeIn>
  );
}
