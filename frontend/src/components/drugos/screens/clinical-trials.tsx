'use client';

import { useState, useMemo } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  useClinicalTrialsSearch,
  LoadingSpinner, ErrorDisplay,
} from '../use-api-data';
import type { ClinicalTrial } from '@/lib/types';
import { FadeIn, PageHeader } from './_core-shared';

export function ClinicalTrialsScreen() {
  const [search, setSearch] = useState('');
  const [phaseFilter, setPhaseFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedTrial, setSelectedTrial] = useState<ClinicalTrial | null>(null);

  // FE-001 ROOT FIX: Real ClinicalTrials.gov v2 API integration. The previous
  // code rendered a local `clinicalTrials` mock array of 5 hardcoded entries.
  // Now we query the real CT.gov database (15,000+ trials) via the API.
  // The search input is debounced by the hook (300ms).
  const { data: trialsData, loading: trialsLoading, error: trialsError } = useClinicalTrialsSearch({
    condition: search.trim() || undefined,
    limit: 50,
  });

  // Map the real API response to the UI's ClinicalTrial shape.
  const realTrials: ClinicalTrial[] = useMemo(() => {
    if (!trialsData?.items) return [];
    return trialsData.items.map((t: any) => ({
      id: t.nctId,
      nctId: t.nctId,
      title: t.title,
      phase: t.phase || 'N/A',
      status: t.status,
      enrollment: t.enrollment,
      startDate: t.startDate,
      completionDate: t.completionDate,
      drugName: (t.interventions || []).join(', '),
      disease: (t.conditions || []).join(', '),
      outcome: t.briefSummary || '',
    }));
  }, [trialsData]);

  const filtered = useMemo(() => {
    return realTrials.filter(t => {
      const matchPhase = phaseFilter === 'all' || t.phase === phaseFilter;
      const matchStatus = statusFilter === 'all' || t.status === statusFilter;
      return matchPhase && matchStatus;
    });
  }, [realTrials, phaseFilter, statusFilter]);

  const phases = [...new Set(realTrials.map(t => t.phase))];
  const statuses = [...new Set(realTrials.map(t => t.status))];

  return (
    <FadeIn>
      <PageHeader title="Clinical Trial Search" description="Search ClinicalTrials.gov data for drug repurposing trials (real API)" />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search by disease (e.g., Huntington's)..." className="pl-9" />
        </div>
        <Select value={phaseFilter} onValueChange={setPhaseFilter}>
          <SelectTrigger className="w-36"><SelectValue placeholder="Phase" /></SelectTrigger>
          <SelectContent><SelectItem value="all">All Phases</SelectItem>{phases.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent><SelectItem value="all">All Status</SelectItem>{statuses.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="p-0">
          {trialsLoading && <LoadingSpinner label="Searching ClinicalTrials.gov..." />}
          {trialsError && <ErrorDisplay error={trialsError} />}
          {!trialsLoading && !trialsError && (
            <Table>
              <TableHeader><TableRow className="bg-muted/50"><TableHead>NCT ID</TableHead><TableHead>Title</TableHead><TableHead>Phase</TableHead><TableHead>Status</TableHead><TableHead>Enrollment</TableHead><TableHead>Dates</TableHead></TableRow></TableHeader>
              <TableBody>
                {filtered.map(t => (
                  <TableRow key={t.id} className="cursor-pointer hover:bg-muted/30" onClick={() => setSelectedTrial(selectedTrial?.id === t.id ? null : t)}>
                    <TableCell><span className="font-mono text-xs text-primary">{t.nctId}</span></TableCell>
                    <TableCell className="max-w-[300px]"><span className="text-sm line-clamp-2">{t.title}</span></TableCell>
                    <TableCell><Badge variant="secondary" className="text-xs">{t.phase}</Badge></TableCell>
                    <TableCell><Badge className="text-xs">{t.status}</Badge></TableCell>
                    <TableCell className="text-sm">{t.enrollment ?? '—'}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{t.startDate || '—'} → {t.completionDate || '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!trialsLoading && !trialsError && filtered.length === 0 && !search && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>Enter a disease name to search ClinicalTrials.gov</p>
            </div>
          )}
        </CardContent>
      </Card>

      {selectedTrial && (
        <Card className="mt-4">
          <CardHeader className="pb-3"><CardTitle className="text-base">{selectedTrial.title}</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div><span className="text-muted-foreground">NCT ID:</span> <span className="font-mono">{selectedTrial.nctId}</span></div>
              <div><span className="text-muted-foreground">Phase:</span> <Badge variant="secondary">{selectedTrial.phase}</Badge></div>
              <div><span className="text-muted-foreground">Status:</span> <Badge>{selectedTrial.status}</Badge></div>
              <div><span className="text-muted-foreground">Enrollment:</span> {selectedTrial.enrollment ?? '—'}</div>
            </div>
            <div><span className="text-muted-foreground">Drug:</span> {selectedTrial.drugName} · <span className="text-muted-foreground">Disease:</span> {selectedTrial.disease}</div>
            {selectedTrial.outcome && <div><span className="text-muted-foreground">Summary:</span> {selectedTrial.outcome.slice(0, 300)}...</div>}
          </CardContent>
        </Card>
      )}
    </FadeIn>
  );
}
