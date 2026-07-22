'use client';

// FE-023-A: Four chart helper sub-components extracted from core-screens.tsx
// (lines 1330-1413). They are kept together in one file because they are
// small and tightly related (all consumed by CandidateDetailScreen and
// SafetyProfileScreen/IPPatentsScreen).
//
// Exports:
//   - PathwayDiagram({ candidate, disease })
//   - ADMETRadarChart({ data })
//   - PhaseDistributionChart({ trials })
//   - PatentTimeline({ patents })
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  PieChart as RechartsPie, Pie, Cell, ResponsiveContainer,
  Tooltip as RechartsTooltip,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { PathwayViz } from '../pathway-viz';
import type {
  DrugCandidate, Disease, ClinicalTrial, Patent, ADMETProfile,
} from '@/lib/types';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,
} from './_core-shared';

export function PathwayDiagram({ candidate, disease }: { candidate: DrugCandidate; disease: Disease }) {
  // FE-009 ROOT FIX: replaced inline <svg> with the canvas-based <PathwayViz>.
  // The pathwayData is computed directly (no useMemo) — the computation is
  // trivial (a handful of array maps) and the React Compiler's
  // preserve-manual-memoization rule flags useMemo with array-typed deps
  // (candidate.targets/pathways) because their reference identity is unstable.
  // Direct computation is both simpler and compiler-friendly.
  const targets = candidate.targets ?? [];
  const pathways = candidate.pathways ?? [];
  const drugId = `drug-${candidate.drugName}`;
  const diseaseId = `disease-${disease.name}`;
  const targetIds = targets.map((t, i) => `target-${i}-${t}`);
  const pathwayIds = pathways.map((p, i) => `pathway-${i}-${p}`);
  const pathwayData = {
    nodes: [
      { id: drugId, label: candidate.drugName, type: 'drug' as const },
      ...targets.map((t, i) => ({ id: targetIds[i], label: t, type: 'protein' as const })),
      ...pathways.map((p, i) => ({ id: pathwayIds[i], label: p, type: 'pathway' as const })),
      { id: diseaseId, label: disease.name, type: 'disease' as const },
    ],
    edges: [
      ...targets.map((t, i) => ({ source: drugId, target: targetIds[i], label: 'inhibits', type: 'activation' as const })),
      ...targets.flatMap((_, ti) => pathways.map((_, pi) => ({ source: targetIds[ti], target: pathwayIds[pi], label: 'regulates', type: 'activation' as const }))),
      ...pathways.map((_, i) => ({ source: pathwayIds[i], target: diseaseId, label: 'associated_with', type: 'activation' as const })),
      ...(targets.length === 0 && pathways.length === 0 ? [{ source: drugId, target: diseaseId, label: 'repurposed_for', type: 'activation' as const }] : []),
    ],
    name: `${candidate.drugName} → ${disease.name}`,
  };
  return <PathwayViz pathwayData={pathwayData} />;
}

export function ADMETRadarChart({ data }: { data: ADMETProfile }) {
  const chartData = [
    { subject: 'Absorption', value: data.absorption },
    { subject: 'Distribution', value: data.distribution },
    { subject: 'Metabolism', value: data.metabolism },
    { subject: 'Excretion', value: data.excretion },
    { subject: 'Toxicity', value: data.toxicity },
  ];
  return (
    <ResponsiveContainer width="100%" height={280}>
      <RadarChart data={chartData}>
        <PolarGrid stroke="#E2E1EA" />
        <PolarAngleAxis dataKey="subject" tick={{ fontSize: 11, fill: '#64748B' }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 9 }} />
        <Radar name="ADMET" dataKey="value" stroke={PRIMARY} fill={PRIMARY} fillOpacity={0.2} strokeWidth={2} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

export function PhaseDistributionChart({ trials }: { trials: ClinicalTrial[] }) {
  const phaseCounts = trials.reduce<Record<string, number>>((acc, t) => { acc[t.phase] = (acc[t.phase] || 0) + 1; return acc; }, {});
  const data = Object.entries(phaseCounts).map(([name, value]) => ({ name, value }));
  const COLORS = [PRIMARY, ACCENT_GREEN, ACCENT_ORANGE, '#8B5CF6', ACCENT_RED];
  return data.length > 0 ? (
    <ResponsiveContainer width="100%" height={200}>
      <RechartsPie>
        <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} label={({ name, value }) => `${name}: ${value}`}>
          {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <RechartsTooltip />
      </RechartsPie>
    </ResponsiveContainer>
  ) : <p className="text-sm text-muted-foreground text-center py-8">No trial data</p>;
}

export function PatentTimeline({ patents }: { patents: Patent[] }) {
  if (patents.length === 0) return <p className="text-sm text-muted-foreground">No patent data</p>;
  return (
    <div className="space-y-3">
      {patents.map(p => (
        <div key={p.id} className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: p.status === 'active' ? ACCENT_GREEN : p.status === 'pending' ? ACCENT_ORANGE : '#94A3B8' }} />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium truncate">{p.patentNumber}</p>
            <p className="text-[10px] text-muted-foreground">{p.filingDate.slice(0,4)} → {(p.expirationDate ?? "").slice(0,4)}</p>
          </div>
          <Badge variant={p.status === 'active' ? 'default' : 'secondary'} className="text-[10px]">{p.status}</Badge>
        </div>
      ))}
    </div>
  );
}
