'use client';

import { useState, useMemo } from 'react';
import { AlertCircle } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useRlCandidates, EmptyState } from '../use-api-data';
import type { DrugInteraction } from '@/lib/types';
import { PageHeader, FadeIn } from './_core-shared';

export function DrugInteractionScreen() {
  // FE-050 ROOT FIX (v118): previously used `drugCandidates.map()` and
  // `drugInteractions.filter()` directly on the empty-defaults arrays.
  // The drug dropdown was always empty and the interaction results
  // always returned []. Now we fetch real RL candidates for the dropdown.
  // There is no /api/drug-interactions endpoint yet, so the results list
  // honestly shows an EmptyState directing the user to openFDA interaction
  // API (https://api.fda.gov/drug/event.json) until a real DDI service is
  // deployed.
  const [drug1, setDrug1] = useState<string>('');
  const [drug2, setDrug2] = useState('');
  const { data: rlData } = useRlCandidates({ limit: 50 });
  const uniqueDrugNames = useMemo(
    () => [...new Set((rlData?.candidates || []).map((rc: any) => rc.drug as string).filter(Boolean))],
    [rlData]
  );
  // Real drug-drug interaction data is not yet wired. We do NOT fabricate
  // interaction entries — show an honest EmptyState pointing the user to
  // openFDA / FDA Label interactions until /api/drug-interactions exists.
  const results: DrugInteraction[] = [];

  return (
    <FadeIn>
      <PageHeader title="Drug-Drug Interaction Checker" description="Check for interactions between medications" />
      <Card className="mb-6">
        <CardContent className="p-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-sm font-medium mb-1.5 block">Drug 1</label>
              <Select value={drug1} onValueChange={setDrug1}><SelectTrigger><SelectValue placeholder="Select a drug" /></SelectTrigger>
                <SelectContent>{uniqueDrugNames.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent></Select>
            </div>
            <div><label className="text-sm font-medium mb-1.5 block">Drug 2 (or class)</label>
              <Input value={drug2} onChange={e => setDrug2(e.target.value)} placeholder="Enter medication or class..." /></div>
          </div>
        </CardContent>
      </Card>
      <div className="space-y-3">
        {drug1 && drug2 ? (
          <Card><CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle className="h-4 w-4 text-amber-600" />
              <span className="text-sm font-medium text-amber-700">DDI service not yet deployed</span>
            </div>
            <p className="text-sm text-muted-foreground">Drug-drug interaction data for <strong>{drug1}</strong> + <strong>{drug2}</strong> requires a real DDI service (e.g. DrugBank interaction API or openFDA drug/event endpoint). Until <code>/api/drug-interactions</code> is wired, verify interactions manually on the <a href={`https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:${encodeURIComponent(drug1)}+AND+patient.drug.medicinalproduct:${encodeURIComponent(drug2)}`} target="_blank" rel="noopener noreferrer" className="underline">openFDA adverse-event portal</a>.</p>
          </CardContent></Card>
        ) : (
          <EmptyState title="Select two drugs to check interactions" description="Choose Drug 1 from the dropdown and type Drug 2 in the input above. Real DDI data will appear here once /api/drug-interactions is deployed." />
        )}
      </div>
    </FadeIn>
  );
}
