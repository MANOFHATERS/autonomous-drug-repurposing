'use client';

import { useState, useEffect, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  useRlCandidates, useDrugMechanisms,
  LoadingSpinner, ErrorDisplay, EmptyState,
} from '../use-api-data';
import { PathwayDiagram } from './charts';
import type { DrugCandidate, Disease } from '@/lib/types';
import { PageHeader, FadeIn } from './_core-shared';

export function MechanismOfActionScreen() {
  // FE-050 ROOT FIX (v118): previously used `drugCandidates.find()` and
  // `drugCandidates.map()` directly on the empty-defaults array — the
  // drug dropdown was always empty and the screen always showed "No drug
  // selected". Now we fetch real RL candidates via /api/rl for the dropdown
  // and real mechanism-of-action data via /api/drugs/mechanism (backed by
  // ChEMBL/DrugBank) for the displayed mechanism, target proteins, and
  // pathways. The `useDrugMechanisms` hook takes a list of drug names and
  // returns a Map keyed by lowercase drug name.
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  const { data: rlData, loading: rlLoading } = useRlCandidates({ limit: 50 });
  const rlDrugNames = useMemo(
    () => [...new Set((rlData?.candidates || []).map((rc: any) => rc.drug as string).filter(Boolean))],
    [rlData]
  );
  useEffect(() => {
    if (!selectedDrug && rlDrugNames.length > 0) setSelectedDrug(rlDrugNames[0]);
  }, [rlDrugNames, selectedDrug]);
  const { data: mechMap, loading: mechLoading, error: mechError } = useDrugMechanisms(selectedDrug ? [selectedDrug] : []);
  const mech = mechMap?.get(selectedDrug.toLowerCase()) || null;
  // FE-050 ROOT FIX (v118): mechanism, targets, pathways come from the REAL
  // ChEMBL/DrugBank data (useDrugMechanisms). Previously these came from
  // the empty `drugCandidates` array so the screen always showed 'N/A'.
  // The DrugMechanismResult type uses `proteinTargets` (not `targets`).
  const mechanism: string = mech?.mechanism || (selectedDrug ? 'Mechanism not yet fetched from ChEMBL/DrugBank.' : '');
  const targets: string[] = mech?.proteinTargets || [];
  const pathways: string[] = mech?.pathways || [];
  const diseaseName = rlData?.candidates?.find((rc: any) => rc.drug === selectedDrug)?.disease || null;
  if (rlLoading) return <FadeIn><PageHeader title="Mechanism of Action" description="Detailed MoA view for drug candidates" /><LoadingSpinner label="Loading RL candidates..." /></FadeIn>;
  if (!selectedDrug) return <FadeIn><PageHeader title="Mechanism of Action" description="Detailed MoA view for drug candidates" /><EmptyState title="No candidates available" description="The Phase 4 RL ranker returned no candidates. Deploy the RL service to populate this screen." /></FadeIn>;

  return (
    <FadeIn>
      <PageHeader title="Mechanism of Action" description="Detailed MoA view for drug candidates (real ChEMBL/DrugBank)" />
      <div className="mb-4">
        <Select value={selectedDrug} onValueChange={setSelectedDrug} disabled={rlDrugNames.length === 0}>
          <SelectTrigger className="w-64"><SelectValue placeholder={rlDrugNames.length === 0 ? 'No drugs loaded' : 'Select a drug'} /></SelectTrigger>
          <SelectContent>{rlDrugNames.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {mechLoading && <LoadingSpinner label="Fetching mechanism from ChEMBL/DrugBank..." />}
      {mechError && <ErrorDisplay error={mechError} />}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">{selectedDrug} Mechanism</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm">{mechanism}</p>
            <div><span className="text-xs font-semibold text-muted-foreground">Target Proteins</span>
              <div className="flex flex-wrap gap-2 mt-1">{targets.length === 0 ? <span className="text-xs text-muted-foreground">N/A</span> : targets.map(t => <Badge key={t} variant="secondary" className="font-mono">{t}</Badge>)}</div></div>
            <div><span className="text-xs font-semibold text-muted-foreground">Pathways</span>
              <div className="flex flex-wrap gap-2 mt-1">{pathways.length === 0 ? <span className="text-xs text-muted-foreground">N/A</span> : pathways.map(p => <Badge key={p} variant="outline">{p}</Badge>)}</div></div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Pathway Diagram</CardTitle></CardHeader>
          <CardContent><PathwayDiagram candidate={{ id: '', drugName: selectedDrug, brandNames: [], genericName: selectedDrug, compositeScore: 0, kgScore: 0, molSimScore: null, safetyScore: 0, clinicalScore: 0, safetyTier: 'unknown', mechanism, clinicalPhase: 'N/A', ipStatus: null, diseaseId: '', diseaseName: diseaseName || 'Unknown', targets: targets.length > 0 ? targets : null, pathways: pathways.length > 0 ? pathways : null } as DrugCandidate} disease={{ id: '', name: diseaseName || 'Unknown', icdCode: '—', meshTerm: '', description: '', therapeuticArea: 'N/A', prevalence: 'N/A', geneticBasis: false } as Disease} /></CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}
