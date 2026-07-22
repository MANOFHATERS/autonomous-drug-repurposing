'use client';

import { useState } from 'react';
import {
  Search, AlertCircle, Activity, Database, ShieldCheck, FlaskConical,
  Package, CheckCircle2, XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useDrugOSNav } from '../nav-context';
import {
  useClinicalTrialsSearch, useApiList, useDrugSafety,
} from '../use-api-data';
import { api } from '@/lib/api-client';
import { drugCandidates, diseases } from '@/lib/empty-defaults';
import type {
  DrugCandidate, Disease, ClinicalTrial, Patent,
  OffTargetPrediction, DrugInteraction,
} from '@/lib/types';
import { SafetyBadge } from '../safety-badge';
import {
  PathwayDiagram, ADMETRadarChart, PhaseDistributionChart, PatentTimeline,
} from './charts';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,
  scoreColor, StatCard, PageHeader, FadeIn,
} from './_core-shared';

export function CandidateDetailScreen() {
  const { navigate, currentRoute } = useDrugOSNav();
  // FE-016 ROOT FIX (Team Member 15, v108): The previous code did:
  //   const candidateId = currentRoute.id || 'DC001';
  //   const candidate = drugCandidates.find(c => c.id === candidateId) || drugCandidates[0];
  //   const disease = diseases.find(d => d.id === candidate.diseaseId) || diseases[0];
  // `drugCandidates` and `diseases` are imported from @/lib/empty-defaults
  // and are EMPTY arrays (`[]`). `find()` returns undefined, `drugCandidates[0]`
  // is undefined, then line 803 accesses `candidate.diseaseId` →
  // TypeError: Cannot read properties of undefined (reading 'diseaseId').
  // The screen crashed on every mount — the entire candidate evaluation
  // workflow was unreachable. The 'DC001' fallback was a fabricated mock id
  // that masked the bug only when currentRoute.id was unset; with empty
  // arrays the crash still happened.
  //
  // ROOT FIX: there is no /api/hypothesis/[id] endpoint yet, so we cannot
  // hydrate a real candidate by id. We treat the empty-defaults lookup as
  // what it is — a fallback that yields nothing — and render an honest
  // EmptyState that tells the researcher exactly what to do next
  // (navigate back to search results and pick a real candidate produced
  // by the RL ranker via /api/rl). This eliminates the TypeError at the
  // root cause: no property access on undefined, no fabricated fallback.
  const candidateId = currentRoute.id || '';
  const candidate = drugCandidates.find(c => c.id === candidateId) || null;
  const [activeTab, setActiveTab] = useState('overview');

  // FE-049 ROOT FIX: Hooks must be called unconditionally (before any early
  // return) per the Rules of Hooks. The previous code only called
  // clinicalTrials.filter(...), patents.filter(...), etc. on the (empty)
  // local arrays. We now call the real /api/clinical-trials/search,
  // /api/patents/search, and /api/safety/[drug] endpoints via their hooks,
  // passing the (possibly null) drugName so the hooks short-circuit cleanly
  // when no candidate is selected. The mapping to the UI types happens
  // AFTER the early returns (so we can rely on `candidate` being non-null).
  const drugNameForHooks = candidate?.drugName ?? null;
  const { data: trialsData, loading: trialsLoading, error: trialsError } = useClinicalTrialsSearch({ intervention: drugNameForHooks || undefined, limit: 20 });
  const { data: patentData, loading: patentsLoading, error: patentsError } = useApiList(() => drugNameForHooks ? api.searchPatents(drugNameForHooks) : Promise.resolve({ items: [] as any[] }), [drugNameForHooks]);
  const { data: safetyData } = useDrugSafety(drugNameForHooks);

  const disease = candidate ? (diseases.find(d => d.id === candidate.diseaseId) || null) : null;

  // FE-016: Honest empty state when no candidate is available. This is
  // the production-grade pattern — researchers see a clear, actionable
  // message instead of a white-screen TypeError.
  if (!candidate) {
    return (
      <FadeIn>
        <PageHeader
          title="Candidate Detail"
          description="Drug repurposing candidate detail view"
          onBack={() => navigate({ page: 'app', section: 'search' })}
        />
        <Card>
          <CardContent className="py-16 text-center text-muted-foreground">
            <Search className="h-10 w-10 mx-auto mb-3 opacity-40" />
            <p className="text-base font-medium text-foreground">No candidate selected</p>
            <p className="text-sm mt-2 max-w-md mx-auto">
              Run a disease search and pick a ranked candidate from the results to view its
              full detail page (scores, safety profile, pathway diagram, clinical trials,
              IP status, and evidence items).
            </p>
            <Button
              className="mt-5"
              onClick={() => navigate({ page: 'app', section: 'search' })}
            >
              <Search className="h-4 w-4 mr-1.5" /> Go to Disease Search
            </Button>
          </CardContent>
        </Card>
      </FadeIn>
    );
  }

  // FE-016: Defensive guard — if the candidate was found but the disease
  // wasn't (e.g. orphaned drugCandidates entry), don't crash on
  // `disease.name` access in PathwayDiagram. Render a clear message.
  if (!disease) {
    return (
      <FadeIn>
        <PageHeader
          title={candidate.drugName}
          description="Drug repurposing candidate detail view"
          onBack={() => navigate({ page: 'app', section: 'search' })}
        />
        <Card>
          <CardContent className="py-16 text-center text-muted-foreground">
            <AlertCircle className="h-10 w-10 mx-auto mb-3 opacity-40" />
            <p className="text-base font-medium text-foreground">Disease record not found</p>
            <p className="text-sm mt-2 max-w-md mx-auto">
              The candidate &ldquo;{candidate.drugName}&rdquo; references a disease that
              is not in the database. This is likely a data integrity issue — please
              report it to your administrator.
            </p>
          </CardContent>
        </Card>
      </FadeIn>
    );
  }

  const relatedTrials: ClinicalTrial[] = (trialsData?.items || []).map((t: any, i: number) => ({ id: t.nctId || `trial-${i}`, nctId: t.nctId, title: t.title, phase: t.phase || 'N/A', status: t.status, enrollment: t.enrollment || 0, startDate: t.startDate || '', completionDate: t.completionDate || '', drugName: (t.interventions || []).join(', '), disease: (t.conditions || []).join(', '), outcome: '' }));
  const relatedPatents: Patent[] = (patentData?.items || []).map((p: any, i: number) => ({ id: p.id || `pat-${i}`, drugName: candidate.drugName, title: p.title || p.patentTitle || 'Untitled', patentNumber: p.patentNumber || p.number || '—', status: p.status || 'unknown', jurisdiction: p.jurisdiction || '—', claims: p.claims ?? 0, assignee: p.assignee || '—', filingDate: p.filingDate || '', grantDate: p.grantDate || '', abstract: p.abstract || '', expirationDate: p.expirationDate ?? null }));
  const relatedEvidence = safetyData?.topReactions ? safetyData.topReactions.map((r, i) => ({ id: `ae-${i}`, drugName: candidate.drugName, type: 'safety', title: r.term, source: 'FDA FAERS', quality: undefined as unknown as number, year: undefined as unknown as number, summary: `${r.count} reports` })) : [];
  const admet = null;
  const offTargets: OffTargetPrediction[] = [];
  const interactions: DrugInteraction[] = [];

  return (
    <FadeIn>
      <PageHeader
        title={candidate.drugName}
        description={`${candidate.genericName} · ${candidate.brandNames.join(', ')} · for ${disease.name}`}
        onBack={() => navigate({ page: 'app', section: 'results', id: candidate.diseaseId })}
        actions={
          <div className="flex items-center gap-2">
            <SafetyBadge tier={candidate.safetyTier} />
            <Badge variant="outline">{candidate.clinicalPhase}</Badge>
            <Badge variant="outline">{candidate.ipStatus ?? 'N/A'}</Badge>
          </div>
        }
      />

      {/* Stat Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <StatCard icon={Activity} value={candidate.compositeScore} label="Composite Score" color={scoreColor(candidate.compositeScore)} />
        <StatCard icon={Database} value={candidate.kgScore} label="KG Score" color={PRIMARY} />
        <StatCard icon={ShieldCheck} value={candidate.safetyScore} label="Safety Score" color={ACCENT_GREEN} />
        <StatCard icon={FlaskConical} value={candidate.clinicalScore} label="Clinical Score" color={ACCENT_ORANGE} />
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full justify-start h-auto p-1 bg-muted/50 rounded-lg flex-wrap">
          {['overview', 'pathway', 'safety', 'clinical', 'ip', 'evidence'].map(tab => (
            <TabsTrigger key={tab} value={tab} className="capitalize gap-1.5 data-[state=active]:bg-background data-[state=active]:shadow-sm">
              {tab}
              {tab === 'clinical' && relatedTrials.length > 0 && <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium bg-primary/10 text-primary rounded-full">{relatedTrials.length}</span>}
              {tab === 'ip' && relatedPatents.length > 0 && <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium bg-primary/10 text-primary rounded-full">{relatedPatents.length}</span>}
              {tab === 'evidence' && relatedEvidence.length > 0 && <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium bg-primary/10 text-primary rounded-full">{relatedEvidence.length}</span>}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* OVERVIEW TAB */}
        <TabsContent value="overview" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-4">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Score Breakdown</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  {[
                    { label: 'Knowledge Graph Score', value: candidate.kgScore },
                    { label: 'Molecular Similarity', value: candidate.molSimScore === null ? null : candidate.molSimScore },
                    { label: 'Safety Profile', value: candidate.safetyScore },
                    { label: 'Clinical Evidence', value: candidate.clinicalScore },
                  ].map(s => {
                    const pct = s.value === null ? 0 : (s.value as number);
                    return (
                    <div key={s.label}>
                      <div className="flex justify-between text-sm mb-1"><span className="text-muted-foreground">{s.label}</span><span className="font-semibold">{s.value === null ? 'N/A' : s.value}</span></div>
                      <div className="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
                        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: scoreColor(pct) }} />
                      </div>
                    </div>
                    );
                  })}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Mechanism of Action</CardTitle></CardHeader>
                <CardContent>
                  <p className="text-sm">{candidate.mechanism}</p>
                  <div className="mt-3">
                    <span className="text-xs font-medium text-muted-foreground">Target Proteins: </span>
                    {candidate.targets === null
                      ? <span className="text-xs text-muted-foreground">N/A</span>
                      : candidate.targets.length === 0
                        ? <span className="text-xs text-muted-foreground">None</span>
                        : candidate.targets.map(t => <Badge key={t} variant="secondary" className="text-xs mr-1 font-mono">{t}</Badge>)}
                  </div>
                  <div className="mt-2">
                    <span className="text-xs font-medium text-muted-foreground">Pathways: </span>
                    {candidate.pathways === null
                      ? <span className="text-xs text-muted-foreground">N/A</span>
                      : candidate.pathways.length === 0
                        ? <span className="text-xs text-muted-foreground">None</span>
                        : candidate.pathways.map(p => <Badge key={p} variant="outline" className="text-xs mr-1">{p}</Badge>)}
                  </div>
                </CardContent>
              </Card>
            </div>
            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Key Evidence</CardTitle></CardHeader>
                <CardContent className="space-y-2">
                  {relatedEvidence.slice(0, 4).map(ev => (
                    <div key={ev.id} className="p-2.5 border rounded-lg text-sm">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge variant="secondary" className="text-[10px]">{ev.type}</Badge>
                        <span className="font-medium text-xs">{ev.source}</span>
                      </div>
                      <p className="text-xs text-muted-foreground line-clamp-2">{ev.title}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Drug Info</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-muted-foreground">Generic</span><span className="font-medium">{candidate.genericName}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Brand</span><span className="font-medium">{candidate.brandNames.join(', ')}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Phase</span><Badge variant="outline" className="text-xs">{candidate.clinicalPhase}</Badge></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">IP</span><Badge variant="outline" className="text-xs">{candidate.ipStatus ?? 'N/A'}</Badge></div>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        {/* PATHWAY TAB */}
        <TabsContent value="pathway" className="mt-4">
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Drug → Target → Pathway → Disease</CardTitle></CardHeader>
            <CardContent>
              <PathwayDiagram candidate={candidate} disease={disease} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* SAFETY TAB */}
        <TabsContent value="safety" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">Safety Tier</CardTitle>
                  <SafetyBadge tier={candidate.safetyTier} />
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  {candidate.safetyTier === 'green' ? 'Low risk profile — suitable for repurposing investigation with standard monitoring.' :
                   candidate.safetyTier === 'yellow' ? 'Moderate risk — requires enhanced monitoring and risk mitigation strategies.' :
                   candidate.safetyTier === 'red' ? 'High risk — significant safety concerns require careful benefit-risk assessment.' :
                   'Model-derived safety score only — NOT a clinical safety determination. Tier will be assigned once openFDA label data (black-box warnings, REMS) and FAERS adverse-event counts are loaded. Do not advance into clinical-trial enrollment decisions without consulting FDA labels and a qualified pharmacist.'}
                </p>
                {admet && <ADMETRadarChart data={admet} />}
              </CardContent>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Off-Target Predictions</CardTitle></CardHeader>
                <CardContent>
                  {offTargets.length > 0 ? (
                    <Table>
                      <TableHeader><TableRow><TableHead>Target</TableHead><TableHead>Probability</TableHead><TableHead>Severity</TableHead><TableHead>System</TableHead></TableRow></TableHeader>
                      <TableBody>
                        {offTargets.map((o, i) => (
                          <TableRow key={i}>
                            <TableCell className="text-sm">{o.target}</TableCell>
                            <TableCell className="text-sm">{Math.round(o.probability * 100)}%</TableCell>
                            <TableCell><Badge variant={o.severity === 'high' ? 'destructive' : o.severity === 'medium' ? 'secondary' : 'outline'} className="text-xs">{o.severity}</Badge></TableCell>
                            <TableCell className="text-xs">{o.organSystem}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : <p className="text-sm text-muted-foreground">No off-target predictions available</p>}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Drug-Drug Interactions</CardTitle></CardHeader>
                <CardContent className="space-y-2">
                  {interactions.length > 0 ? interactions.map((int, i) => (
                    <div key={i} className="p-2.5 border rounded-lg">
                      <div className="flex items-center gap-2">
                        <Badge variant={int.severity === 'contraindicated' ? 'destructive' : int.severity === 'major' ? 'secondary' : 'outline'} className="text-xs">{int.severity}</Badge>
                        <span className="text-sm font-medium">{int.drug2}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">{int.description} — {int.mechanism}</p>
                    </div>
                  )) : <p className="text-sm text-muted-foreground">No known interactions</p>}
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        {/* CLINICAL TAB */}
        <TabsContent value="clinical" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Clinical Trials</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  {relatedTrials.length > 0 ? relatedTrials.map(trial => (
                    <Card key={trial.id} className="border">
                      <CardContent className="p-4">
                        <h4 className="font-medium text-sm">{trial.title}</h4>
                        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                          <Badge variant="outline" className="text-xs font-mono">{trial.nctId}</Badge>
                          <Badge variant="secondary" className="text-xs">{trial.phase}</Badge>
                          <Badge className="text-xs">{trial.status}</Badge>
                        </div>
                        <p className="text-xs text-muted-foreground mt-2">Enrollment: {trial.enrollment} · {trial.startDate} – {trial.completionDate}</p>
                        {trial.outcome && <p className="text-xs mt-1"><span className="font-medium">Outcome:</span> {trial.outcome}</p>}
                      </CardContent>
                    </Card>
                  )) : <p className="text-sm text-muted-foreground">No clinical trials found</p>}
                </CardContent>
              </Card>
            </div>
            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Phase Distribution</CardTitle></CardHeader>
                <CardContent>
                  <PhaseDistributionChart trials={relatedTrials} />
                </CardContent>
              </Card>
              {/* FE-026 ROOT FIX: The "Success Prediction" card has been
                  removed. It displayed `clinicalScore * 0.6 + 15` as a
                  "Predicted trial success rate" — a completely fabricated
                  formula with no clinical validation. A drug with
                  clinicalScore=80 showed "63% predicted trial success
                  rate", a number with no scientific basis. Clinical trial
                  success prediction requires Phase II data, historical
                  benchmarking, and regulatory consultation — not a linear
                  transform of a model score. If this feature is needed in
                  the future, it must be implemented as a real ML model
                  trained on ClinicalTrials.gov historical outcomes with a
                  published validation study. */}
            </div>
          </div>
        </TabsContent>

        {/* IP TAB */}
        <TabsContent value="ip" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Patent Status</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  {relatedPatents.length > 0 ? relatedPatents.map(pat => (
                    <div key={pat.id} className="p-4 border rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium text-sm">{pat.title}</span>
                        <Badge variant={pat.status === 'active' ? 'default' : pat.status === 'expired' ? 'secondary' : pat.status === 'pending' ? 'outline' : 'destructive'}>
                          {pat.status}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground space-y-0.5">
                        <p>{pat.patentNumber} · {pat.jurisdiction} · {pat.claims} claims</p>
                        <p>Assignee: {pat.assignee}</p>
                        <p>Filed: {pat.filingDate} · Expires: {pat.expirationDate}</p>
                      </div>
                    </div>
                  )) : <p className="text-sm text-muted-foreground">No patents found for {candidate.drugName}</p>}
                </CardContent>
              </Card>
            </div>
            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Freedom to Operate</CardTitle></CardHeader>
                <CardContent>
                  <div className="text-center">
                    <div className="text-3xl font-bold" style={{ color: candidate.ipStatus === 'Off-Patent' || candidate.ipStatus === 'Patent Expired' ? ACCENT_GREEN : candidate.ipStatus === 'Novel Use Patentable' ? ACCENT_ORANGE : candidate.ipStatus === null ? '#94A3B8' /* slate-400 for N/A */ : ACCENT_RED }}>
                      {candidate.ipStatus === 'Off-Patent' || candidate.ipStatus === 'Patent Expired' ? 'Clear' : candidate.ipStatus === 'Novel Use Patentable' ? 'Partial' : candidate.ipStatus === null ? 'N/A' : 'Restricted'}
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">IP Status: {candidate.ipStatus ?? 'N/A'}</p>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-3"><CardTitle className="text-base">Patent Timeline</CardTitle></CardHeader>
                <CardContent>
                  <PatentTimeline patents={relatedPatents} />
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        {/* EVIDENCE TAB */}
        <TabsContent value="evidence" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <Card>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">Evidence Items</CardTitle>
                    <Button size="sm" onClick={() => navigate({ page: 'app', section: 'evidence-builder' })}>
                      <Package className="h-4 w-4 mr-1.5" /> Build Package
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {relatedEvidence.length > 0 ? relatedEvidence.map(ev => (
                    <div key={ev.id} className="p-3 border rounded-lg">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge variant="secondary" className="text-[10px]">{ev.type}</Badge>
                        <span className="font-medium text-sm">{ev.title}</span>
                        <span className="ml-auto text-xs font-bold" style={{ color: scoreColor(ev.quality ? Number(ev.quality) : 0) }}>{ev.quality}</span>
                      </div>
                      <p className="text-xs text-muted-foreground">{ev.source} · {ev.year ?? 0}</p>
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{ev.summary}</p>
                    </div>
                  )) : <p className="text-sm text-muted-foreground">No evidence items found</p>}
                </CardContent>
              </Card>
            </div>
            <Card>
              <CardHeader className="pb-3"><CardTitle className="text-base">Gap Analysis</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {['clinical', 'preclinical', 'computational', 'literature', 'patent'].map(type => {
                  const has = relatedEvidence.some(e => e.type === type);
                  return (
                    <div key={type} className="flex items-center gap-2">
                      {has ? <CheckCircle2 className="h-4 w-4" style={{ color: ACCENT_GREEN }} /> : <XCircle className="h-4 w-4 text-slate-300" />}
                      <span className={`text-sm ${has ? 'text-foreground' : 'text-muted-foreground'}`}>{type.charAt(0).toUpperCase() + type.slice(1)} Evidence</span>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </FadeIn>
  );
}
