'use client';

import { useState, useEffect, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Cell, ResponsiveContainer,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  useRlCandidates, LoadingSpinner, ErrorDisplay, EmptyState,
} from '../use-api-data';
import type { DrugCandidate } from '@/lib/types';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE,
  scoreColor, PageHeader, FadeIn,
} from './_core-shared';

export function ScoreBreakdownScreen() {
  // FE-050 ROOT FIX (v118): the previous code used `drugCandidates.find(c => c.id === selectedId)`
  // and `drugCandidates.map(c => ...)` directly on the empty-defaults array —
  // both always returned undefined / []. The select dropdown was always empty
  // and the chart never rendered. Now we fetch real RL candidates via /api/rl.
  const { data: rlData, loading: rlLoading, error: rlError } = useRlCandidates({ limit: 50 });
  const rlCandidates: DrugCandidate[] = useMemo(() => {
    return (rlData?.candidates || []).map((rc: any, i: number) => ({
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
      diseaseName: rc.disease as string,
      diseaseId: '',
      brandNames: [],
      genericName: rc.drug as string,
      ipStatus: null,
      targets: null,
      pathways: null,
    }));
  }, [rlData]);
  const [selectedId, setSelectedId] = useState<string>('');
  useEffect(() => {
    if (!selectedId && rlCandidates.length > 0) setSelectedId(rlCandidates[0].id);
  }, [rlCandidates, selectedId]);
  const candidate = rlCandidates.find(c => c.id === selectedId) || null;
  if (rlLoading) return <FadeIn><PageHeader title="Composite Score Breakdown" description="Detailed score decomposition for drug candidates" /><LoadingSpinner label="Loading RL candidates..." /></FadeIn>;
  if (rlError) return <FadeIn><PageHeader title="Composite Score Breakdown" description="Detailed score decomposition for drug candidates" /><ErrorDisplay error={rlError} /></FadeIn>;
  if (!candidate) return <FadeIn><PageHeader title="Composite Score Breakdown" description="Detailed score decomposition for drug candidates" /><EmptyState title="No candidates available" description="The Phase 4 RL ranker returned no candidates. Deploy the RL service to populate this screen." /></FadeIn>;

  const chartData = [
    { name: 'KG Score', value: candidate.kgScore, fill: PRIMARY },
    { name: 'Mol Similarity', value: candidate.molSimScore ?? 0, fill: '#3B82F6' },
    { name: 'Safety', value: candidate.safetyScore, fill: ACCENT_GREEN },
    { name: 'Clinical', value: candidate.clinicalScore, fill: ACCENT_ORANGE },
  ];

  return (
    <FadeIn>
      <PageHeader title="Composite Score Breakdown" description="Detailed score decomposition for drug candidates" />
      <div className="mb-4">
        <Select value={selectedId} onValueChange={setSelectedId} disabled={rlCandidates.length === 0}>
          <SelectTrigger className="w-64"><SelectValue placeholder={rlCandidates.length === 0 ? 'No candidates loaded' : 'Select a candidate'} /></SelectTrigger>
          <SelectContent className="max-h-72 overflow-y-auto">{rlCandidates.map(c => <SelectItem key={c.id} value={c.id}>{c.drugName}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">{candidate.drugName} — Score: {candidate.compositeScore}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {chartData.map(s => (
              <div key={s.name}>
                <div className="flex justify-between text-sm mb-1"><span>{s.name}</span><span className="font-bold" style={{ color: scoreColor(s.value) }}>{s.value}</span></div>
                <div className="w-full bg-slate-100 rounded-full h-3 overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${s.value}%`, backgroundColor: s.fill }} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Score Comparison Chart</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 100]} />
                <RechartsTooltip />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, index) => <Cell key={index} fill={entry.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}
