'use client';

import { useState, useEffect, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useDrugOSNav } from '../nav-context';
import {
  useRlCandidates, LoadingSpinner,
} from '../use-api-data';
import { SafetyBadge } from '../safety-badge';
import type { DrugCandidate } from '@/lib/types';
import { PageHeader, FadeIn } from './_core-shared';

export function DrugComparisonScreen() {
  const { navigate } = useDrugOSNav();
  // FE-050 ROOT FIX (v118): previously used `drugCandidates.find(c => c.id === id)`
  // and `drugCandidates.map(c => c.drugName)` directly on the empty-defaults
  // array. The selected drug list was always empty and the comparison table
  // never rendered. Now we fetch real RL candidates via /api/rl for the list.
  const { data: rlData, loading: rlLoading } = useRlCandidates({ limit: 50 });
  const rlCandidates: DrugCandidate[] = useMemo(() =>
    (rlData?.candidates || []).map((rc: any, i: number) => ({
      id: rc.id || `rl-${i}`,
      drugName: rc.drug as string,
      compositeScore: Math.round((rc.overallScore || rc.reward || 0) * 100),
      kgScore: Math.round((rc.gnnScore || 0) * 100),
      molSimScore: null as number | null,
      safetyScore: Math.round((rc.safetyScore || 0) * 100),
      clinicalScore: 0,
      safetyTier: 'unknown' as const,
      mechanism: '',
      clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel',
      diseaseId: '',
      diseaseName: rc.disease as string,
      brandNames: [],
      genericName: rc.drug as string,
      ipStatus: null,
      targets: null,
      pathways: null,
    })),
    [rlData]
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  useEffect(() => {
    // Pre-select the first two candidates once RL data arrives so the
    // comparison table isn't empty on first load.
    if (selectedIds.length === 0 && rlCandidates.length >= 2) {
      setSelectedIds([rlCandidates[0].id, rlCandidates[1].id]);
    }
  }, [rlCandidates, selectedIds]);
  const compared = selectedIds.map(id => rlCandidates.find(c => c.id === id)).filter(Boolean) as DrugCandidate[];
  const uniqueDrugNames = [...new Set(rlCandidates.map(c => c.drugName))];

  const toggleDrug = (id: string) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : prev.length < 4 ? [...prev, id] : prev);
  };

  return (
    <FadeIn>
      <PageHeader title="Drug Comparison" description="Compare up to 4 drug candidates side-by-side" />
      <Card className="mb-6">
        <CardContent className="p-4">
          <p className="text-sm font-medium mb-2">Select drugs to compare ({selectedIds.length}/4):</p>
          {rlLoading ? <LoadingSpinner label="Loading RL candidates..." /> :
           rlCandidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">No drug candidates loaded. The RL ranker returned no candidates. Deploy the RL service to populate this screen.</p>
          ) : (
            // FE-055 ROOT FIX (TM13): removed the arbitrary `slice(0, 13)`
            // magic number. All candidates are shown in a scrollable
            // container so none are silently hidden; the compare limit
            // (4) is enforced by toggleDrug() above.
            <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto">
              {rlCandidates.map(c => (
                <Badge key={c.id} variant={selectedIds.includes(c.id) ? 'default' : 'outline'} className="cursor-pointer" onClick={() => toggleDrug(c.id)}>{c.drugName}</Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
      {compared.length > 1 && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <Table>
              <TableHeader><TableRow className="bg-muted/50"><TableHead>Metric</TableHead>{compared.map(c => <TableHead key={c.id} className="text-center">{c.drugName}</TableHead>)}</TableRow></TableHeader>
              <TableBody>
                {[
                  { label: 'Composite Score', key: 'compositeScore' },
                  { label: 'KG Score', key: 'kgScore' },
                  { label: 'Mol Similarity', key: 'molSimScore' },
                  { label: 'Safety Score', key: 'safetyScore' },
                  { label: 'Clinical Score', key: 'clinicalScore' },
                ].map(row => (
                  <TableRow key={row.key}>
                    <TableCell className="font-medium text-sm">{row.label}</TableCell>
                    {compared.map(c => {
                      const val = (c as unknown as Record<string, unknown>)[row.key] as number;
                      const max = Math.max(...compared.map(x => (x as unknown as Record<string, unknown>)[row.key] as number));
                      return (
                        <TableCell key={c.id} className="text-center">
                          <span className={`font-bold ${val === max ? 'text-emerald-600' : ''}`}>{val}</span>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell className="font-medium text-sm">Safety Tier</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center"><SafetyBadge tier={c.safetyTier} /></TableCell>)}
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-sm">Phase</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center"><Badge variant="outline" className="text-xs">{c.clinicalPhase}</Badge></TableCell>)}
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-sm">IP Status</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center text-xs">{c.ipStatus ?? 'N/A'}</TableCell>)}
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </FadeIn>
  );
}
