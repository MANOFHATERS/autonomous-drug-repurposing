'use client';

import { useState, useEffect, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Cell, ResponsiveContainer,
} from 'recharts';
import { Brain, Target, Info } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  useRlCandidates, LoadingSpinner, ErrorDisplay, EmptyState,
} from '../use-api-data';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE,
  StatCard, PageHeader, FadeIn,
} from './_core-shared';

export function PredictionExplorerScreen() {
  // FE-002 ROOT FIX: drugCandidates is [] (empty-defaults). drugCandidates[0]
  // is undefined → .drugName throws TypeError on mount. Fetch real RL
  // candidates via /api/rl and guard for empty.
  const { data: rlData, loading: rlLoading, error: rlError } = useRlCandidates({ limit: 50 });
  const rlCandidates = useMemo(() => {
    return (rlData?.candidates || []).map((rc: any, i: number) => ({
      id: rc.id || `rl-${i}`,
      drugName: rc.drug as string,
      compositeScore: Math.round((rc.overallScore || rc.reward || 0) * 100),
      kgScore: Math.round((rc.gnnScore || 0) * 100),
      safetyScore: Math.round((rc.safetyScore || 0) * 100),
      clinicalScore: 0,
      molSimScore: null as number | null,
      safetyTier: 'unknown' as const,
      mechanism: '',
      clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel',
      diseaseName: rc.disease as string,
    }));
  }, [rlData]);
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  useEffect(() => {
    if (!selectedDrug && rlCandidates.length > 0) setSelectedDrug(rlCandidates[0].drugName);
  }, [rlCandidates, selectedDrug]);
  const candidate = rlCandidates.find(c => c.drugName === selectedDrug) || null;
  if (rlLoading) return <FadeIn><PageHeader title="Prediction Explorer" description="Explore AI predictions in detail" /><LoadingSpinner label="Loading RL candidates..." /></FadeIn>;
  if (rlError) return <FadeIn><PageHeader title="Prediction Explorer" description="Explore AI predictions in detail" /><ErrorDisplay error={rlError} /></FadeIn>;
  if (!candidate) return <FadeIn><PageHeader title="Prediction Explorer" description="Explore AI predictions in detail" /><EmptyState title="No predictions available" description="The Phase 4 RL ranker returned no candidates. Deploy the RL service to populate this screen." /></FadeIn>;

  return (
    <FadeIn>
      <PageHeader title="Prediction Explorer" description="Explore AI predictions in detail" />
      <div className="mb-4">
        <Select value={selectedDrug} onValueChange={setSelectedDrug}>
          <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
          <SelectContent>{[...new Set(rlCandidates.map(c => c.drugName))].map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatCard icon={Brain} value={candidate.compositeScore} label="AI Composite Score" color={PRIMARY} />
        <StatCard icon={Target} value={candidate.kgScore} label="Graph Prediction" color={ACCENT_GREEN} />
        {/* FE-007 ROOT FIX: Removed fabricated "Confidence" StatCard that
            multiplied compositeScore by 0.85. The RL model does not report a
            confidence interval. */}
        <Card className="flex items-center justify-center p-4 text-center">
          <div>
            <div className="flex items-center justify-center mb-1"><Info className="h-4 w-4 text-muted-foreground" /></div>
            <p className="text-xs text-muted-foreground">Confidence interval not reported by the RL model.</p>
          </div>
        </Card>
      </div>
      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">Prediction Breakdown</CardTitle></CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={[
              { name: 'KG Score', value: candidate.kgScore, fill: PRIMARY },
              { name: 'Molecular', value: candidate.molSimScore ?? 0, fill: '#3B82F6' },
              { name: 'Safety', value: candidate.safetyScore, fill: ACCENT_GREEN },
              { name: 'Clinical', value: candidate.clinicalScore, fill: ACCENT_ORANGE },
            ]}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis domain={[0, 100]} />
              <RechartsTooltip />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {[PRIMARY, '#3B82F6', ACCENT_GREEN, ACCENT_ORANGE].map((c, i) => <Cell key={i} fill={c} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </FadeIn>
  );
}
