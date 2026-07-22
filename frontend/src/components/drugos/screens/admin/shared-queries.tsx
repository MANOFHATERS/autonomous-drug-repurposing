'use client';

import { useApiList, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, Copy } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 5. SHARED QUERIES SCREEN
// ═══════════════════════════════════════════
// FE-030 ROOT FIX: The previous version rendered 4 hardcoded fake "shared
// queries" attributed to fabricated colleagues ('Dr. Sarah Chen', 'James
// Wilson', 'Dr. Priya Patel', 'Dr. Lisa Kim'). A researcher believed these
// were real colleagues. Root fix: call the REAL /api/projects endpoint.
// Projects ARE the shared queries. We render the real list, or an honest
// empty state. We NEVER fabricate colleagues.
export function SharedQueriesScreen() {
  const { data, loading, error, refetch } = useApiList(() => api.listProjects(), []);
  const projects = data?.items ?? [];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Shared Queries" desc="Projects shared in your organization" actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading projects..." />}
      {!loading && !error && projects.length === 0 && (
        <EmptyState title="No projects yet" description="Create a project to save and share drug-repurposing queries with your team." />
      )}
      {!loading && !error && projects.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Project Name</TableHead><TableHead>Visibility</TableHead><TableHead>Created</TableHead><TableHead>Hypotheses</TableHead><TableHead>Comments</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{projects.map(p => { const created = new Date(p.createdAt); const createdLabel = isNaN(created.getTime()) ? '—' : created.toLocaleDateString(); return (<TableRow key={p.id}><TableCell className="font-medium">{p.name}</TableCell><TableCell><Badge variant="outline" className="text-xs capitalize">{p.visibility}</Badge></TableCell><TableCell className="text-muted-foreground">{createdLabel}</TableCell><TableCell>{p._count?.hypotheses ?? 0}</TableCell>
          <TableCell>{p._count?.comments ?? 0}</TableCell>
          <TableCell><Button variant="outline" size="sm"><Copy className="h-3 w-3 mr-1" />Copy to My Queries</Button></TableCell></TableRow>); })}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  );
}
