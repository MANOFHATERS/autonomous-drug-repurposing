'use client';

import { Search, FlaskConical, Activity } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useDrugOSNav } from '../nav-context';
import {
  useDiseaseSearch, useRlCandidates, useClinicalTrialsSearch,
  LoadingSpinner, EmptyState,
} from '../use-api-data';
import { ScoreBar } from '../score-bar';
import { SafetyBadge } from '../safety-badge';
import type { DrugCandidate, Disease } from '@/lib/types';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE,
  StatCard, PageHeader, FadeIn,
} from './_core-shared';

export function DiseaseDetailScreen() {
  const { navigate, currentRoute } = useDrugOSNav();
  // FE-050 ROOT FIX (v118): the previous code used `diseases.find()` and
  // `drugCandidates.filter()` directly on the empty-defaults arrays. The
  // disease metadata, related candidates, and related trials all rendered
  // as empty/zero. Now we fetch real data: MeSH disease metadata via
  // /api/diseases/search, real RL candidates via /api/rl, and real trials
  // via /api/clinical-trials/search. The disease `id` passed in the URL is
  // treated as a disease NAME (the AppSearchPage navigates by name).
  const diseaseName = currentRoute.id || '';
  const { data: diseaseSearch } = useDiseaseSearch(diseaseName, 2);
  const diseaseMeta = diseaseSearch?.items?.[0] || null;
  const disease: Disease = diseaseMeta ? {
    id: diseaseMeta.descriptorUi,
    name: diseaseMeta.name,
    icdCode: '—',
    meshTerm: diseaseMeta.name,
    description: diseaseMeta.scopeNote || 'No MeSH scope note available.',
    therapeuticArea: diseaseMeta.treeNumber?.[0] || 'N/A',
    prevalence: 'N/A',
    geneticBasis: false,
  } : {
    id: diseaseName,
    name: diseaseName || 'Unknown Disease',
    icdCode: '—',
    meshTerm: '',
    description: diseaseName ? 'Searching MeSH for disease metadata...' : 'No disease selected. Use the search page to find a disease.',
    therapeuticArea: 'N/A',
    prevalence: 'N/A',
    geneticBasis: false,
  };
  // Real RL candidates for this disease
  const { data: rlData, loading: rlLoading } = useRlCandidates({ disease: diseaseName, limit: 50 });
  const relatedCandidates: DrugCandidate[] = (rlData?.candidates || []).map((rc: any, i: number) => ({
    id: rc.id || `rl-${i}`,
    drugName: rc.drug as string,
    compositeScore: Math.round((rc.overallScore || rc.reward || 0) * 100),
    kgScore: Math.round((rc.gnnScore || 0) * 100),
    molSimScore: null,
    safetyScore: Math.round((rc.safetyScore || 0) * 100),
    clinicalScore: 0,
    safetyTier: 'unknown' as const,
    mechanism: '',
    clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel',
    diseaseId: disease.id,
    diseaseName,
    brandNames: [],
    genericName: rc.drug as string,
    ipStatus: null,
    targets: null,
    pathways: null,
  }));
  // Real clinical trials for this disease
  const { data: trialsData } = useClinicalTrialsSearch({ condition: diseaseName, limit: 50 });
  const relatedTrials = trialsData?.items || [];

  return (
    <FadeIn>
      <PageHeader title={disease.name} description={`${disease.therapeuticArea} · ICD-10: ${disease.icdCode} · ${disease.prevalence}`} onBack={() => navigate({ page: 'app', section: 'search' })} />
      <Card className="mb-6"><CardContent className="p-4"><p className="text-sm">{disease.description}</p></CardContent></Card>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatCard icon={Search} value={rlLoading ? '—' : relatedCandidates.length} label="Drug Candidates" color={PRIMARY} />
        <StatCard icon={FlaskConical} value={relatedTrials.length} label="Clinical Trials" color={ACCENT_GREEN} />
        <StatCard icon={Activity} value={relatedCandidates.length > 0 ? Math.round(relatedCandidates.reduce((s, c) => s + c.compositeScore, 0) / relatedCandidates.length) : 0} label="Avg Score" color={ACCENT_ORANGE} />
      </div>
      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">Top Candidates</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {rlLoading ? <LoadingSpinner label="Loading RL candidates..." /> :
           relatedCandidates.length === 0 ? <EmptyState title="No candidates yet" description={`The RL ranker has no candidates for "${diseaseName}". Deploy the RL service to populate this list.`} /> :
           [...relatedCandidates].sort((a, b) => b.compositeScore - a.compositeScore).map(c => (
            <div key={c.id} className="flex items-center justify-between p-3 border rounded-lg cursor-pointer hover:bg-accent transition-colors" onClick={() => navigate({ page: 'app', section: 'candidate', id: c.id })}>
              <div className="flex items-center gap-3"><span className="font-medium">{c.drugName}</span><SafetyBadge tier={c.safetyTier} /><Badge variant="outline" className="text-xs">{c.clinicalPhase}</Badge></div>
              <ScoreBar score={c.compositeScore} size="sm" />
            </div>
          ))}
        </CardContent>
      </Card>
    </FadeIn>
  );
}
