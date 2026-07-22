'use client';

import { useState, useMemo } from 'react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { useDrugOSNav } from '../nav-context';
import {
  useRlCandidates, LoadingSpinner, ErrorDisplay, EmptyState,
} from '../use-api-data';
import { ScoreBar } from '../score-bar';
import { SafetyBadge } from '../safety-badge';
import { PageHeader, FadeIn } from './_core-shared';

export function AdvancedSearchScreen() {
  const { navigate } = useDrugOSNav();
  const [query, setQuery] = useState('');
  const [area, setArea] = useState('all');
  const [scoreMin, setScoreMin] = useState(0);
  const [phase, setPhase] = useState('all');
  const [tier, setTier] = useState('all');

  // FE-050 ROOT FIX: previously filtered the empty `drugCandidates` array,
  // so the screen always rendered 0 results. Now we fetch real RL candidates
  // via /api/rl (optionally filtered by `query` as a disease name when the
  // user types ≥2 chars) and filter those.
  const { data: rlData, loading: rlLoading, error: rlError } = useRlCandidates(query.trim().length >= 2 ? { disease: query.trim(), limit: 100 } : { limit: 100 });
  const realCandidates = useMemo(() => {
    return (rlData?.candidates || []).map((rc: any, i: number) => ({ id: rc.id || `rl-${i}`, drugName: rc.drug as string, diseaseName: rc.disease as string, compositeScore: Math.round((rc.overallScore || 0) * 100), safetyScore: Math.round((rc.safetyScore || 0) * 100), clinicalScore: 0, kgScore: Math.round((rc.gnnScore || 0) * 100), molSimScore: null as number | null, safetyTier: 'unknown' as const, mechanism: '', clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel' }));
  }, [rlData]);
  const results = useMemo(() => {
    return realCandidates.filter(c => {
      const matchQuery = !query || c.drugName.toLowerCase().includes(query.toLowerCase());
      const matchScore = c.compositeScore >= scoreMin;
      const matchPhase = phase === 'all' || c.clinicalPhase === phase;
      const matchTier = tier === 'all' || c.safetyTier === tier;
      return matchQuery && matchScore && matchPhase && matchTier;
    });
  }, [realCandidates, query, scoreMin, phase, tier]);

  return (
    <FadeIn>
      <PageHeader title="Advanced Search" description="Multi-filter search across all drug candidates" onBack={() => navigate({ page: 'app', section: 'search' })} />
      <Card className="mb-6">
        <CardContent className="p-6 space-y-4">
          <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by drug name, mechanism, target..." />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div><label className="text-sm font-medium mb-1.5 block">Therapeutic Area</label>
              <Select value={area} onValueChange={setArea}><SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="all">All</SelectItem></SelectContent>
              </Select>
            </div>
            <div><label className="text-sm font-medium mb-1.5 block">Min Score: {scoreMin}</label>
              <Slider value={[scoreMin]} onValueChange={v => setScoreMin(v[0])} min={0} max={100} step={5} />
            </div>
            <div><label className="text-sm font-medium mb-1.5 block">Phase</label>
              <Select value={phase} onValueChange={setPhase}><SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="all">All</SelectItem>{[...new Set(realCandidates.map(c => c.clinicalPhase))].map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div><label className="text-sm font-medium mb-1.5 block">Safety Tier</label>
              <Select value={tier} onValueChange={setTier}><SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="green">Safe</SelectItem><SelectItem value="yellow">Caution</SelectItem><SelectItem value="red">High Risk</SelectItem></SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>
      <p className="text-sm text-muted-foreground mb-3">{rlLoading ? 'Loading...' : `${results.length} results`}</p>
      {rlLoading && <LoadingSpinner label="Querying Phase 4 RL ranker..." />}
      {rlError && <ErrorDisplay error={rlError} />}
      {!rlLoading && !rlError && results.length === 0 && (
        <EmptyState title="No candidates match your filters" description="Adjust the filters above, or deploy the Phase 4 RL ranker to populate this screen with real candidates." />
      )}
      <div className="space-y-2">
        {results.slice(0, 20).map(c => (
          <Card key={c.id} className="cursor-pointer hover:shadow-md transition-shadow" onClick={() => navigate({ page: 'app', section: 'candidate', id: c.id })}>
            <CardContent className="p-4 flex items-center gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2"><span className="font-medium">{c.drugName}</span><SafetyBadge tier={c.safetyTier} /><Badge variant="outline" className="text-xs">{c.clinicalPhase}</Badge></div>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{c.mechanism || '—'}</p>
              </div>
              <ScoreBar score={c.compositeScore} size="sm" />
            </CardContent>
          </Card>
        ))}
      </div>
    </FadeIn>
  );
}
