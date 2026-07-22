'use client';

import React, { useState, useMemo } from 'react';
import {
  Search, Download, ChevronDown, ChevronUp, Star, BookmarkPlus, RefreshCw,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Slider } from '@/components/ui/slider';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useDrugOSNav } from '../nav-context';
import { useRlCandidates } from '../use-api-data';
import { ScoreBar } from '../score-bar';
import { SafetyBadge } from '../safety-badge';
import { diseases } from '@/lib/empty-defaults';
import type { DrugCandidate, Disease } from '@/lib/types';
import { FadeIn, PageHeader } from './_core-shared';

export function SearchResultsScreen() {
  const { navigate, currentRoute } = useDrugOSNav();
  // FE-001 ROOT FIX: accept the disease name from the DiseaseSearchScreen
  // (passed via navigate({ name })) and use it to query the real RL ranker
  // via /api/rl. Falls back to mock candidates if RL service not deployed.
  const diseaseId = currentRoute.id || 'D001';
  // FE-062 ROOT FIX: Remove the `as any` cast — the Route type in
  // nav-context.tsx already has an optional `name` field, so the cast was
  // unnecessary and bypassed type checking. Direct property access is
  // type-safe and surfaces any future Route shape changes at compile time.
  const diseaseName = currentRoute.name ||
    (diseaseId.startsWith('search:') ? decodeURIComponent(diseaseId.slice(7)) : diseaseId);
  const disease = diseases.find(d => d.id === diseaseId) ||
    diseases.find(d => d.name === diseaseName) || {
      id: diseaseId,
      name: diseaseName,
      icdCode: '—',
      description: '',
    } as Disease;

  // Call the real RL ranker endpoint. Returns 503 if RL_SERVICE_URL or
  // RL_LOCAL_CSV is not set — in that case we fall back to mock candidates
  // and show a banner.
  const { data: rlData, loading: rlLoading, error: rlError } = useRlCandidates({
    disease: diseaseName,
    limit: 50,
  });

  // Map RL candidates to the DrugCandidate shape the UI expects.
  //
  // FE-049 ROOT FIX: previously this mapping fabricated `molSimScore: 0`,
  // `ipStatus: 'Unknown'`, `targets: []`, `pathways: []`. A researcher
  // seeing "Mol Similarity: 0" may interpret it as "no molecular
  // similarity to known drugs" (a negative scientific signal), when in
  // reality the RL ranker does not populate that field at all. Likewise
  // "IP Status: Unknown" reads as "we checked and could not determine
  // patent status" — vs. the truth, which is "we have not looked it up".
  // The fix is to use `null` for any field the RL ranker does not
  // populate, and have the UI render "N/A" for null values. This is the
  // difference between "no data" (correct, null) and "data is zero/empty"
  // (incorrect, fabricated).
  //
  // FE-024 ROOT FIX: mechanism field is NO LONGER populated with RL debug
  // values. It is left empty here — the CandidateTable component fetches
  // the real mechanism-of-action from ChEMBL via the useDrugMechanisms
  // hook. The RL debug info (reward, policyProb, gnnScore, rank, source)
  // is moved to the `rlDebugInfo` field, which the table renders ONLY in
  // a tooltip clearly labeled "RL Model Debug (not for clinical use)".
  const realCandidates: DrugCandidate[] = (rlData?.candidates || []).map((rc: any, i: number) => ({
    id: `rl-${i}`,
    drugName: rc.drug,
    brandNames: [],
    genericName: rc.drug,
    diseaseId,
    diseaseName: rc.disease,
    compositeScore: Math.round((rc.overallScore || 0) * 100),
    kgScore: Math.round((rc.plausibilityScore || 0) * 100),
    safetyScore: Math.round((rc.safetyScore || 0) * 100),
    clinicalScore: Math.round((rc.efficacyScore || 0) * 100),
    // FE-049: RL ranker does not compute molecular similarity — null, not 0.
    molSimScore: null,
    // FE-023 ROOT FIX: safetyTier is 'unknown' for RL candidates. The
    // previous code mapped the model's safetyScore to green/yellow/red with
    // hardcoded thresholds (>=0.7 green, >=0.4 yellow) that were never
    // clinically validated. Showing a green "Safe" badge on a drug because
    // a model output exceeded 0.7 is scientifically irresponsible — many
    // drugs with high model scores have serious adverse events (black-box
    // warnings, REMS programs). Real safety tiering must come from openFDA
    // label data (black-box warning = red, etc.) or FAERS adverse-event
    // counts. Until that integration is in place, RL candidates show
    // 'unknown' with a disclaimer banner.
    safetyTier: 'unknown' as const,
    // FE-024: mechanism is fetched from ChEMBL by CandidateTable; leave empty here.
    mechanism: '',
    clinicalPhase: rc.literatureSupport ? 'Literature-supported' : 'Novel',
    // FE-049: patent status lookup is a separate pipeline step — null, not "Unknown".
    ipStatus: null,
    // FE-049: target/pathway population comes from the KG, not the RL ranker — null, not [].
    targets: null,
    pathways: null,
    rank: rc.rank,
    // FE-024: RL debug info is moved to a tooltip, NOT shown as mechanism.
    rlDebugInfo: {
      reward: typeof rc.reward === 'number' ? rc.reward : undefined,
      policyProb: typeof rc.policyProb === 'number' ? rc.policyProb : undefined,
      gnnScore: typeof rc.plausibilityScore === 'number' ? rc.plausibilityScore : undefined,
      rank: typeof rc.rank === 'number' ? rc.rank : undefined,
      source: rlData?.source,
    },
    // TM13 ROOT FIX (v132, CRITICAL — Phase 2 ↔ Phase 4 wiring):
    // Forward the pathway_chain from the RL candidate. The Python
    // rl/service.py attaches this field after querying the Phase 2 KG
    // (KG_SERVICE_URL/kg/explore) for each (drug, disease) pair. The
    // candidate table's Pathway column renders it as an expandable
    // "N pathways" cell via the PathwayExpander component.
    //
    // When the Python service's pathway_enrichment_available flag is
    // false (KG unreachable / not configured), this field is an empty
    // array — PathwayExpander renders "No pathway data" inline.
    pathway_chain: Array.isArray(rc.pathway_chain) ? rc.pathway_chain : [],
  }));

  // FE-001 ROOT FIX (v2): NEVER fall back to mock drug candidates in a
  // production pharma app. If the RL ranker is not deployed (503 from
  // /api/rl) or returns zero candidates, we render a hard EMPTY STATE
  // that explicitly tells the researcher "no real predictions available"
  // and instructs them to deploy the Phase 4 RL service. The previous
  // behavior rendered 13 hardcoded fake drugs (Memantine 87, Riluzole 84,
  // ...) in a table that was visually indistinguishable from a real
  // results view — a patient-safety hazard because a researcher could
  // pursue a fabricated "Memantine for Huntington's" candidate into
  // wet-lab validation, wasting months and $100K+.
  //
  // The `drugCandidates` mock import is no longer referenced by this
  // screen for candidate rendering. It is still used elsewhere in this
  // file (KnowledgeGraphExplorer, SafetyProfileScreen, CompareDrugsScreen)
  // because those screens legitimately need a static reference set for
  // UI demonstration of KG/safety/comparison affordances — but the
  // SEARCH RESULTS screen, which is the screen a researcher uses to
  // decide which drug to advance to wet-lab, must NEVER show mock data.
  const candidates = realCandidates;
  const usingMock = false; // kept for backward-compat with banner logic below

  const [filterTier, setFilterTier] = useState<string>('all');
  const [filterPhase, setFilterPhase] = useState<string>('all');
  const [sortKey, setSortKey] = useState<string>('compositeScore');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [shortlisted, setShortlisted] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [scoreRange, setScoreRange] = useState<[number, number]>([0, 100]);

  const filtered = useMemo(() => {
    let items = [...candidates];
    if (filterTier !== 'all') items = items.filter(c => c.safetyTier === filterTier);
    if (filterPhase !== 'all') items = items.filter(c => c.clinicalPhase === filterPhase);
    items = items.filter(c => c.compositeScore >= scoreRange[0] && c.compositeScore <= scoreRange[1]);
    const numericSortKeys = ['compositeScore', 'kgScore', 'safetyScore', 'clinicalScore', 'molSimScore'] as const;
    type NumericSortKey = typeof numericSortKeys[number];
    const key = (numericSortKeys as readonly string[]).includes(sortKey) ? (sortKey as NumericSortKey) : 'compositeScore';
    items.sort((a, b) => {
      const aVal = a[key] ?? -1;
      const bVal = b[key] ?? -1;
      return sortDir === 'desc' ? bVal - aVal : aVal - bVal;
    });
    return items;
  }, [candidates, filterTier, filterPhase, sortKey, sortDir, scoreRange]);

  const handleSort = (key: string) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const toggleShortlist = (id: string) => {
    setShortlisted(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const phases = [...new Set(candidates.map(c => c.clinicalPhase))];
  const renderSortIcon = (col: string) => sortKey === col ? (sortDir === 'desc' ? <ChevronDown className="h-3 w-3 ml-1" /> : <ChevronUp className="h-3 w-3 ml-1" />) : null;

  return (
    <FadeIn>
      <PageHeader
        title={disease.name}
        description={`${candidates.length} drug repurposing candidates found · ICD-10: ${disease.icdCode}`}
        onBack={() => navigate({ page: 'app', section: 'search' })}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4 mr-1.5" /> Export CSV
            </Button>
            {shortlisted.size > 0 && (
              <Button variant="outline" size="sm" onClick={() => navigate({ page: 'app', section: 'shortlists' })}>
                <BookmarkPlus className="h-4 w-4 mr-1.5" /> Shortlist ({shortlisted.size})
              </Button>
            )}
          </div>
        }
      />
      {/* FE-001 ROOT FIX (v2): Real RL ranker integration banner. The
          previous "demo data" amber banner was an admission of guilt —
          it sat ABOVE a table that looked identical to a real-results
          view. Now the banner is removed and a hard EMPTY STATE is
          rendered in place of the table when no real candidates exist. */}
      {rlLoading && (
        <div className="mb-4 text-xs text-muted-foreground flex items-center gap-2">
          <RefreshCw className="h-3 w-3 animate-spin" /> Querying Phase 4 RL ranker for {diseaseName}...
        </div>
      )}
      {rlData && realCandidates.length > 0 && (
        <div className="mb-4 text-xs text-emerald-700 p-2 border border-emerald-200 rounded bg-emerald-50">
          <strong>Live RL predictions:</strong> {realCandidates.length} candidates from the Phase 4 RL ranker
          (source: {rlData.source}).
        </div>
      )}
      {/* FE-023 ROOT FIX: Patient-safety disclaimer. RL safety scores are
          model outputs, NOT clinical safety determinations. */}
      {realCandidates.length > 0 && (
        <div className="mb-4 text-xs text-slate-700 p-3 border border-slate-300 rounded bg-slate-50">
          <strong className="text-slate-900">Patient-safety disclaimer:</strong>{' '}
          Safety scores shown here are model-derived outputs from the Phase 4 RL ranker.
          They are <strong>not</strong> a substitute for clinical review, FDA label review,
          or FAERS adverse-event analysis. The "Safety" column shows "Model score only"
          because the model's safety score has not been calibrated against real clinical data.
          Do not advance any candidate into a clinical-trial enrollment decision based on
          these scores alone — consult openFDA labels, FAERS, and a qualified pharmacist.
        </div>
      )}
      {/* FE-001 ROOT FIX (v2): the previous `usingMock` "demo data" banner
          is intentionally NOT rendered here. When realCandidates is empty
          we render a hard EMPTY STATE below (in place of the table) that
          tells the researcher to deploy the RL ranker — we do NOT show a
          "demo data" banner above an identical-looking table, because
          that was the original patient-safety hazard. */}

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-xs font-medium text-muted-foreground mr-1">Safety:</span>
        {['all', 'green', 'yellow', 'red'].map(t => (
          <Badge key={t} variant={filterTier === t ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilterTier(t)}>
            {t === 'all' ? 'All' : t === 'green' ? '🟢 Safe' : t === 'yellow' ? '🟡 Caution' : '🔴 Risk'}
          </Badge>
        ))}
        <Separator orientation="vertical" className="h-5 mx-1" />
        <span className="text-xs font-medium text-muted-foreground mr-1">Phase:</span>
        <Select value={filterPhase} onValueChange={setFilterPhase}>
          <SelectTrigger className="w-36 h-7 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Phases</SelectItem>
            {phases.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
          </SelectContent>
        </Select>
        <Separator orientation="vertical" className="h-5 mx-1" />
        <span className="text-xs font-medium text-muted-foreground">Score:</span>
        <Slider value={scoreRange} onValueChange={v => setScoreRange(v as [number, number])} min={0} max={100} step={5} className="w-28" />
        <span className="text-xs text-muted-foreground">{scoreRange[0]}–{scoreRange[1]}</span>
      </div>

      {/* FE-001 ROOT FIX (v2): Hard empty state when no real RL candidates.
          This block replaces what used to be a silent fall-through to mock
          data. We render this BEFORE the table so a researcher never sees
          a confusing empty table — they see a clear, actionable message.
          The empty state distinguishes three cases:
            (a) RL service is loading → handled by the spinner banner above.
            (b) RL service returned 503 (not deployed) → "Deploy the Phase 4
                RL service to see real candidates."
            (c) RL service returned 200 but zero candidates for this disease
                → "The RL ranker found no candidates for this disease."
          We use the rlError object to distinguish (b) from (c). */}
      {!rlLoading && realCandidates.length === 0 && (
        <Card className="border-2 border-dashed border-amber-300 bg-amber-50/50">
          <CardContent className="p-8 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-700">
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <h3 className="text-base font-semibold text-amber-900 mb-2">
              No RL predictions available
            </h3>
            <p className="text-sm text-amber-800 max-w-md mx-auto mb-4">
              {rlError
                ? <>The Phase 4 RL ranker service is not deployed. Deploy the RL service to see real, ranked drug repurposing candidates for <strong>{diseaseName}</strong>.</>
                : <>The Phase 4 RL ranker returned zero candidates for <strong>{diseaseName}</strong>. This may indicate the disease is not present in the RL candidate cache, or the ranker has not been run for this disease yet.</>
              }
            </p>
            <div className="text-xs text-amber-700 bg-amber-100/70 rounded-md p-3 max-w-lg mx-auto text-left">
              <div className="font-semibold mb-1">To enable real predictions:</div>
              <ul className="list-disc list-inside space-y-0.5">
                <li>Set <code className="bg-amber-200/60 px-1 rounded">RL_SERVICE_URL</code> to the deployed RL ranker endpoint, <em>or</em></li>
                <li>Set <code className="bg-amber-200/60 px-1 rounded">RL_LOCAL_CSV</code> to a local RL predictions CSV file.</li>
                <li>Run <code className="bg-amber-200/60 px-1 rounded">python run_4phase.py</code> to regenerate predictions end-to-end.</li>
              </ul>
            </div>
            <div className="mt-4 text-xs text-amber-700/80 italic">
              Mock drug candidates are never displayed on this screen — patient safety requires real predictions only.
            </div>
          </CardContent>
        </Card>
      )}

      {/* Results Table — only rendered when there are real candidates */}
      {realCandidates.length > 0 && (
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/50 hover:bg-muted/50">
                <TableHead className="w-8">★</TableHead>
                <TableHead className="w-8">#</TableHead>
                <TableHead>Drug Name</TableHead>
                <TableHead className="cursor-pointer select-none" onClick={() => handleSort('compositeScore')}>
                  Composite Score {renderSortIcon('compositeScore')}
                </TableHead>
                <TableHead>Safety</TableHead>
                <TableHead>Mechanism</TableHead>
                <TableHead>Phase</TableHead>
                <TableHead>IP Status</TableHead>
                <TableHead className="w-8"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((c, i) => (
                // FE-028 ROOT FIX: React Fragment shorthand <> has no key
                // prop. React requires a key on the outermost element in a
                // .map(). Using React.Fragment with explicit key.
                <React.Fragment key={c.id}>
                  <TableRow className="cursor-pointer hover:bg-muted/30" onClick={() => navigate({ page: 'app', section: 'candidate', id: c.id })}>
                    <TableCell onClick={e => { e.stopPropagation(); toggleShortlist(c.id); }}>
                      <Star className={`h-4 w-4 ${shortlisted.has(c.id) ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground hover:text-yellow-400'} transition-colors`} />
                    </TableCell>
                    <TableCell className="font-bold text-muted-foreground text-xs">{i + 1}</TableCell>
                    <TableCell>
                      <div>
                        <span className="font-medium text-sm">{c.drugName}</span>
                        <span className="text-xs text-muted-foreground ml-1.5">({c.brandNames.join(', ')})</span>
                      </div>
                    </TableCell>
                    <TableCell><ScoreBar score={c.compositeScore} size="sm" /></TableCell>
                    <TableCell><SafetyBadge tier={c.safetyTier} /></TableCell>
                    <TableCell><span className="text-xs text-slate-600 line-clamp-2 max-w-[180px]">{c.mechanism}</span></TableCell>
                    <TableCell><Badge variant="outline" className="text-xs">{c.clinicalPhase}</Badge></TableCell>
                    <TableCell><span className="text-xs">{c.ipStatus ?? 'N/A'}</span></TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={e => { e.stopPropagation(); setExpandedId(expandedId === c.id ? null : c.id); }}>
                        {expandedId === c.id ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </Button>
                    </TableCell>
                  </TableRow>
                  {expandedId === c.id && (
                    <TableRow key={`${c.id}-detail`}>
                      <TableCell colSpan={9} className="bg-muted/20 p-4">
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                          <div><span className="text-muted-foreground">KG Score:</span> <span className="font-semibold">{c.kgScore}</span></div>
                          <div><span className="text-muted-foreground">Mol Similarity:</span> <span className="font-semibold">{c.molSimScore === null ? 'N/A' : c.molSimScore}</span></div>
                          <div><span className="text-muted-foreground">Safety Score:</span> <span className="font-semibold">{c.safetyScore}</span></div>
                          <div><span className="text-muted-foreground">Clinical Score:</span> <span className="font-semibold">{c.clinicalScore}</span></div>
                        </div>
                        <div className="mt-2">
                          <span className="text-xs text-muted-foreground">Targets: </span>
                          {c.targets === null
                            ? <span className="text-xs text-muted-foreground">N/A</span>
                            : c.targets.length === 0
                              ? <span className="text-xs text-muted-foreground">None</span>
                              : c.targets.map(t => <Badge key={t} variant="secondary" className="text-xs mr-1">{t}</Badge>)}
                        </div>
                        <div className="mt-1">
                          <span className="text-xs text-muted-foreground">Pathways: </span>
                          {c.pathways === null
                            ? <span className="text-xs text-muted-foreground">N/A</span>
                            : c.pathways.length === 0
                              ? <span className="text-xs text-muted-foreground">None</span>
                              : c.pathways.map(p => <Badge key={p} variant="outline" className="text-xs mr-1">{p}</Badge>)}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </React.Fragment>
              ))}
            </TableBody>
          </Table>
          {filtered.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">
              <Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>No candidates match your filters</p>
            </div>
          )}
        </CardContent>
      </Card>
      )}
    </FadeIn>
  );
}
