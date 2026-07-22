'use client';

import { useState, useEffect, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  useRlCandidates, LoadingSpinner, EmptyState,
} from '../use-api-data';
import { parsePrevalence, type OrphanEligibility } from '@/lib/orphan-drug';
import { diseases } from '@/lib/empty-defaults';
import { PageHeader, FadeIn } from './_core-shared';

export function RegulatoryPathwayScreen() {
  // FE-050 ROOT FIX (v118): previously used `drugCandidates.find()` and
  // `drugCandidates.map()` directly on the empty-defaults array. The drug
  // dropdown was always empty and the screen always showed "No drug selected".
  // Now we fetch real RL candidates via /api/rl and derive the clinical phase
  // and IP status from the selected candidate.
  //
  // FE-051 ROOT FIX (Teammate 13, MEDIUM): we ALSO keep the disease name
  // from each RL candidate (previously discarded) so the Orphan Drug Status
  // card below can look up the disease's prevalence and call parsePrevalence
  // to compute FDA orphan-drug eligibility. When no prevalence data is
  // available (the current state — no /api/diseases/[id] endpoint returns
  // prevalence), parsePrevalence returns { eligible: null } and the UI
  // shows an honest "Prevalence data not available" message — it never
  // guesses. The moment a prevalence source is wired, this screen lights
  // up automatically with real eligibility assessments.
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  const { data: rlData, loading: rlLoading } = useRlCandidates({ limit: 50 });
  const rlCandidates = useMemo(() => (rlData?.candidates || []).map((rc: any, i: number) => ({
    id: rc.id || `rl-${i}`,
    drugName: rc.drug as string,
    diseaseName: (rc.disease as string | undefined) || '',
    clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel',
    ipStatus: null as string | null,
  })), [rlData]);
  useEffect(() => {
    if (!selectedDrug && rlCandidates.length > 0) setSelectedDrug(rlCandidates[0].drugName);
  }, [rlCandidates, selectedDrug]);
  const candidate = rlCandidates.find(c => c.drugName === selectedDrug) || null;

  // FE-051: compute FDA orphan-drug eligibility for the selected candidate's
  // disease. `diseases` is currently empty (no prevalence source wired), so
  // `diseaseForCandidate` is undefined and parsePrevalence(undefined) returns
  // { eligible: null, note: 'Prevalence data not available.' }. When a real
  // prevalence source lands, this same code path produces a real assessment.
  const diseaseForCandidate = candidate?.diseaseName
    ? diseases.find(d => d.name.toLowerCase() === candidate.diseaseName.toLowerCase())
    : undefined;
  const orphanEligibility: OrphanEligibility = useMemo(
    () => parsePrevalence(diseaseForCandidate?.prevalence),
    [diseaseForCandidate?.prevalence],
  );

  if (rlLoading) return <FadeIn><PageHeader title="Regulatory Pathway Assessment" description="Assess regulatory requirements for drug repurposing" /><LoadingSpinner label="Loading RL candidates..." /></FadeIn>;
  if (!candidate) return <FadeIn><PageHeader title="Regulatory Pathway Assessment" description="Assess regulatory requirements for drug repurposing" /><EmptyState title="No candidates available" description="The Phase 4 RL ranker returned no candidates. Deploy the RL service to populate this screen." /></FadeIn>;

  return (
    <FadeIn>
      <PageHeader title="Regulatory Pathway Assessment" description="Assess regulatory requirements for drug repurposing" />
      <div className="mb-4">
        <Select value={selectedDrug} onValueChange={setSelectedDrug} disabled={rlCandidates.length === 0}>
          <SelectTrigger className="w-64"><SelectValue placeholder={rlCandidates.length === 0 ? 'No drugs loaded' : 'Select a drug'} /></SelectTrigger>
          <SelectContent>{[...new Set(rlCandidates.map(c => c.drugName))].map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Regulatory Steps</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {[
              { step: 'Pre-IND Meeting', status: 'required', desc: 'Request Type B meeting with FDA' },
              { step: 'IND Application', status: 'required', desc: 'Submit 505(b)(2) application' },
              { step: 'Phase II Trial', status: candidate.clinicalPhase === 'Phase II' || candidate.clinicalPhase === 'Phase III' ? 'complete' : 'pending', desc: 'Confirmatory efficacy study' },
              { step: 'Phase III Trial', status: candidate.clinicalPhase === 'Phase III' ? 'complete' : 'pending', desc: 'Pivotal registration trial' },
              { step: 'NDA Submission', status: 'pending', desc: '505(b)(2) NDA filing' },
              { step: 'FDA Review', status: 'pending', desc: 'Standard 10-12 month review' },
            ].map((s, i) => (
              <div key={i} className="flex items-start gap-3 p-3 border rounded-lg">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${s.status === 'complete' ? 'bg-emerald-100 text-emerald-700' : s.status === 'required' ? 'bg-primary/10 text-primary' : 'bg-slate-100 text-slate-400'}`}>
                  {s.status === 'complete' ? '✓' : i + 1}
                </div>
                <div><span className="font-medium text-sm">{s.step}</span><p className="text-xs text-muted-foreground">{s.desc}</p></div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Regulatory Considerations</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="p-3 bg-primary/5 border border-primary/20 rounded-lg">
              <h4 className="font-medium text-sm mb-1">505(b)(2) Pathway</h4>
              <p className="text-xs text-muted-foreground">This drug may qualify for the 505(b)(2) abbreviated NDA pathway since it is already FDA-approved for another indication.</p>
            </div>
            <div className={`p-3 border rounded-lg ${orphanEligibility.eligible === true ? 'bg-emerald-50 border-emerald-200' : orphanEligibility.eligible === false ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-200'}`}>
              <h4 className="font-medium text-sm mb-1">Orphan Drug Status (prevalence-based)</h4>
              <p className="text-xs text-muted-foreground">
                {candidate.diseaseName
                  ? <>Disease: <span className="font-medium text-foreground">{candidate.diseaseName}</span>. </>
                  : 'No disease associated with this candidate. '}
                {orphanEligibility.note}
              </p>
              {orphanEligibility.eligible === true && (
                <p className="text-xs text-emerald-700 mt-1 font-medium">May qualify for FDA orphan-drug designation (estimated &lt; 200,000 US cases — 21 U.S.C. §360ee).</p>
              )}
              {orphanEligibility.eligible === false && (
                <p className="text-xs text-amber-700 mt-1 font-medium">Prevalence exceeds FDA orphan threshold — does not qualify on prevalence grounds alone.</p>
              )}
              {orphanEligibility.eligible === null && (
                <p className="text-xs text-muted-foreground mt-1">
                  Prevalence data not yet wired. Verify orphan status at{' '}
                  <a href="https://www.accessdata.fda.gov/scripts/opdlisting/oopd/" target="_blank" rel="noopener noreferrer" className="underline">FDA Orphan Designations</a>{' '}
                  before relying on exclusivity incentives.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}
