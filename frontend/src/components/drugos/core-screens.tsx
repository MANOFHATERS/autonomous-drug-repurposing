'use client';

import { remainingScreens } from './remaining-screens';
import React, { useState, useMemo, useCallback, useEffect } from 'react';
import {
  Search, Download, ChevronDown, ChevronUp, Star, ArrowLeft,
  ShieldCheck, AlertTriangle, FlaskConical, FileBarChart, Package,
  Filter, CheckCircle2, XCircle, Clock, TrendingUp, BookOpen,
  GitBranch, BarChart3, FileText, Layers, Target, Activity,
  Zap, Database, Globe, ChevronRight, Plus, Minus, Eye,
  BookmarkPlus, Share2, ExternalLink, Info, AlertCircle,
  PieChart, LineChart, ClipboardCheck, Scale, Beaker,
  Atom, Hash, Calendar, Users, ArrowRight, Maximize2,
  RotateCcw, ZoomIn, ZoomOut, GripVertical, Trash2, Play,
  FileUp, Send, Sparkles, Brain, Timer, CheckSquare,
  Square, CircleDot, HelpCircle, Settings, RefreshCw,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger,
} from '@/components/ui/sheet';
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  PieChart as RechartsPie, Pie, Cell, ResponsiveContainer, Legend,
  LineChart as RechartsLine, Line,
} from 'recharts';
import { motion, AnimatePresence } from 'framer-motion';
import { useDrugOSNav } from './nav-context';
// FE-053 ROOT FIX: Use the dedicated ScoreBar and SafetyBadge components
// from ./score-bar and ./safety-badge instead of inline duplicates that
// had different color thresholds, size mappings, and visual styles.
// Single source of truth = bug fixes propagate everywhere.
import { ScoreBar } from './score-bar';
import { SafetyBadge } from './safety-badge';
// FE-001 ROOT FIX: Real API hooks replace direct mock-data imports.
import {
  useDiseaseSearch, useDrugSearch, useDrugSafety, useClinicalTrialsSearch,
  useLiteratureSearch, useKnowledgeGraph, useBuildEvidencePackage, useRlCandidates,
  useApiList, useDrugMechanisms,
  LoadingSpinner, ErrorDisplay, EmptyState, DemoDataBanner,
} from './use-api-data';
import { KnowledgeGraphViewer } from './knowledge-graph-viewer';
import { PathwayViz } from './pathway-viz';
import { Progress } from '@/components/ui/progress';
import { api } from '@/lib/api-client';
// FE-034 ROOT FIX: `mock-data.ts` deleted (dangerous name invited future
// engineers to re-add fabricated data). Empty defaults now live in
// `@/lib/empty-defaults`. Type imports come from `@/lib/types`.
import {
  diseases, drugCandidates, clinicalTrials, graphNodes, graphEdges,
  recentQueries, savedQueries, usageMetrics,
  patents, evidenceItems, admetProfiles, offTargetPredictions,
  drugInteractions,
} from '@/lib/empty-defaults';
// FE-043 v123 FORENSIC ROOT FIX: import `trendingDiseases` from
// `@/lib/static-content` (curated REAL marketing content with 4 entries:
// Huntington's, Alzheimer's, Pancreatic Cancer, ALS) instead of from
// `@/lib/empty-defaults` (which exports `trendingDiseases = []`). The
// previous code imported the EMPTY array — the dashboard's "Trending
// Diseases" section showed ZERO diseases even though the marketing team
// had curated a real list. The empty-defaults export is kept for other
// consumers that want a typed empty placeholder, but the dashboard should
// use the real curated list.
import { trendingDiseases } from '@/lib/static-content';
import type {
  DrugCandidate, Disease, ClinicalTrial,
  GraphNode, GraphEdge, Patent, EvidenceItem,
  ADMETProfile, OffTargetPrediction, DrugInteraction,
} from '@/lib/types';

// ═══════════════════════════════════════════
// SHARED HELPERS
// ═══════════════════════════════════════════

const PRIMARY = '#5B4FCF';
const ACCENT_GREEN = '#1D9E75';
const ACCENT_ORANGE = '#D4853A';
const ACCENT_RED = '#C0392B';
const BG = '#F8F8FA';

function scoreColor(s: number) {
  if (s >= 80) return ACCENT_GREEN;
  if (s >= 60) return ACCENT_ORANGE;
  return ACCENT_RED;
}

// ═══════════════════════════════════════════
// FE-051 / FE-053 / FE-054 / FE-055 / FE-056 ROOT FIX HELPERS (TM13)
// ═══════════════════════════════════════════
// These helpers fix the cluster of broken-empty-state + fragile-matching
// bugs the audit found across core-screens.tsx. They are used by multiple
// screens below.

// FE-051 ROOT FIX (Teammate 13, MEDIUM): parsePrevalence + FDA orphan-drug
// threshold logic lives in @/lib/orphan-drug so it can be unit-tested
// directly (importing this component file in Jest is expensive — it pulls
// in recharts + framer-motion). It is WIRED INTO RegulatoryPathwayScreen
// below — see the "Orphan Drug Status" card. The previous version of this
// screen used `prevalence?.includes('per 100,000')` (fragile string match
// that falsely qualified "5,000 per 100,000" — half the population — as
// orphan-eligible). The current version calls parsePrevalence, which:
//   1. Parses the numeric value + unit out of common biomedical prevalence
//      phrasings ("N per 100,000", "1 in N", "N per million", bare counts).
//   2. Converts to an estimated US-population count (US_POPULATION constant).
//   3. Compares against the FDA statutory orphan-drug threshold
//      (< 200,000 people in the US — 21 U.S.C. §360ee).
//   4. Returns { eligible: boolean | null, estimate, note } — `null` when
//      prevalence data is not available, so the UI NEVER guesses.
// When the disease prevalence API is wired (currently no /api/diseases/[id]
// endpoint returns prevalence), the screen will light up automatically.
import { parsePrevalence, type OrphanEligibility } from '@/lib/orphan-drug';

/**
 * Shared empty-state for screens that have no data yet. Per the project
 * doc (Team_Cosmic_Build_Process_Updated.docx) and the FE-034 root fix,
 * production code must NEVER fabricate sample data — it must show an
 * honest empty state that tells the researcher the data is not loaded
 * (and, where relevant, how to load it). This replaces the previous
 * pattern of `.map()` over an empty array rendering nothing (leaving
 * the researcher staring at a blank table with no explanation).
 */
function EmptyDataState({ title, hint }: { title: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Database className="h-10 w-10 mx-auto text-muted-foreground/50 mb-3" aria-hidden />
        <p className="font-medium text-foreground">{title}</p>
        {hint && <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">{hint}</p>}
      </CardContent>
    </Card>
  );
}


function StatCard({ icon: Icon, value, label, color = PRIMARY }: { icon: React.ElementType; value: string | number; label: string; color?: string }) {
  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-2xl font-bold mt-1">{value}</p>
          </div>
          <div className="rounded-lg p-2.5" style={{ backgroundColor: `${color}15` }}>
            <Icon className="h-5 w-5" style={{ color }} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function PageHeader({ title, description, actions, onBack }: { title: string; description?: string; actions?: React.ReactNode; onBack?: () => void }) {
  const { navigate } = useDrugOSNav();
  return (
    <div className="flex items-start justify-between mb-6">
      <div className="flex items-start gap-3">
        {onBack && (
          <Button variant="ghost" size="sm" onClick={onBack} className="mt-0.5 h-8 w-8 p-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        )}
        <div>
          <h1 className="text-2xl font-bold text-foreground">{title}</h1>
          {description && <p className="text-sm text-muted-foreground mt-0.5">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay }}>
      {children}
    </motion.div>
  );
}

// ═══════════════════════════════════════════
// 1. DISEASE SEARCH SCREEN
// ═══════════════════════════════════════════

function DiseaseSearchScreen() {
  const { navigate } = useDrugOSNav();
  const [query, setQuery] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [therapeuticArea, setTherapeuticArea] = useState('all');
  const [geneticOnly, setGeneticOnly] = useState(false);

  // FE-001 ROOT FIX: Replace mock-data autocomplete with real /api/diseases/search
  // (backed by NLM MeSH). The previous code filtered a local `diseases` array
  // of 8 mock entries — researchers could never find real diseases. Now we
  // query the real MeSH database via the API.
  const { data: diseaseResults, loading: diseasesLoading, error: diseasesError } = useDiseaseSearch(query, 2);

  // FE-023 ROOT FIX: Use `descriptorUi` (lowercase 'i') and `name` to match
  // the actual MeshDescriptor shape returned by the MeSH service. The previous
  // code used `descriptorUI` (uppercase 'I') and `descriptorName` — both
  // undefined — causing blank dropdown suggestions.
  const suggestions = useMemo(() => {
    if (!diseaseResults?.items) return [];
    return diseaseResults.items.slice(0, 8).map(d => ({
      id: d.descriptorUi,
      name: d.name,
      icdCode: d.descriptorUi, // MeSH descriptor UI (no ICD code from MeSH)
      therapeuticArea: d.scopeNote ? d.scopeNote.slice(0, 60) + '...' : '',
    }));
  }, [diseaseResults]);

  const filteredTrending = useMemo(() => {
    let items = trendingDiseases;
    if (therapeuticArea !== 'all') {
      const areaDiseases = diseases.filter(d => d.therapeuticArea === therapeuticArea).map(d => d.name);
      items = items.filter(t => areaDiseases.some(ad => t.name.includes(ad.split(' ')[0])));
    }
    return items;
  }, [therapeuticArea]);

  const handleSelectDisease = (diseaseId: string, diseaseName?: string) => {
    // FE-001: pass the disease name (not just id) so SearchResultsScreen can
    // query the real API by name.
    navigate({ page: 'app', section: 'results', id: diseaseId, name: diseaseName });
  };

  const handleSearch = () => {
    if (query.trim()) {
      // Try to match against the real API results first.
      // FE-023 ROOT FIX: Use `name` and `descriptorUi` matching MeshDescriptor.
      const match = diseaseResults?.items?.find(d =>
        d.name.toLowerCase().includes(query.toLowerCase())
      );
      if (match) {
        handleSelectDisease(match.descriptorUi, match.name);
      } else {
        // No MeSH match — navigate with the raw query so SearchResultsScreen
        // can do a drug search by disease name.
        handleSelectDisease('search:' + encodeURIComponent(query), query);
      }
    }
  };

  const quickStartTemplates = [
    { name: "Huntington's Disease", id: 'search:Huntington%27s%20Disease', icon: '🧬' },
    { name: "Alzheimer's Disease", id: 'search:Alzheimer%27s%20Disease', icon: '🧠' },
    { name: 'Pancreatic Cancer', id: 'search:Pancreatic%20Cancer', icon: '🎯' },
  ];

  const therapeuticAreas = [...new Set(diseases.map(d => d.therapeuticArea))];

  return (
    <FadeIn>
      <div className="max-w-4xl mx-auto">
        {/* Hero Search */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold mb-2">Find Drug Repurposing Candidates</h1>
          <p className="text-muted-foreground mb-6">Search for a disease to discover ranked drug candidates powered by AI</p>
          <div className="relative max-w-2xl mx-auto">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" />
            <Input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="Search diseases (real MeSH database)..."
              className="pl-12 pr-28 h-12 text-base border-2 border-primary/20 focus:border-primary rounded-xl shadow-lg shadow-primary/5"
            />
            <Button onClick={handleSearch} className="absolute right-1.5 top-1.5 h-9 px-5 rounded-lg" style={{ backgroundColor: PRIMARY }}>
              Search
            </Button>
            {/* Autocomplete dropdown — real MeSH results */}
            {(suggestions.length > 0 || diseasesLoading) && (
              <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-xl shadow-xl overflow-hidden">
                {diseasesLoading && (
                  <div className="px-4 py-2.5 text-sm text-muted-foreground flex items-center gap-2">
                    <RefreshCw className="h-3 w-3 animate-spin" /> Searching MeSH...
                  </div>
                )}
                {suggestions.map(d => (
                  <button
                    key={d.id}
                    onClick={() => handleSelectDisease(d.id, d.name)}
                    className="flex items-center justify-between w-full px-4 py-2.5 text-sm hover:bg-accent text-left transition-colors"
                  >
                    <div>
                      <span className="font-medium">{d.name}</span>
                      <span className="ml-2 text-xs text-muted-foreground">{d.therapeuticArea}</span>
                    </div>
                    <Badge variant="secondary" className="text-xs font-mono">{d.icdCode}</Badge>
                  </button>
                ))}
              </div>
            )}
            {diseasesError && query.length >= 2 && (
              <div className="absolute z-50 w-full mt-1 bg-popover border border-red-200 rounded-xl shadow-xl p-3 text-xs text-red-700">
                Failed to search MeSH: {diseasesError.message}
              </div>
            )}
          </div>
          <div className="flex items-center justify-center gap-2 mt-3">
            <span className="text-xs text-muted-foreground">{usageMetrics.queries.used}/{usageMetrics.queries.limit} queries used this period</span>
            <Progress value={usageMetrics.queries.limit > 0 ? Math.min((usageMetrics.queries.used / usageMetrics.queries.limit) * 100, 100) : 0} className="w-20 h-1.5" />
          </div>
        </div>

        {/* Quick Start Templates */}
        <div className="mb-6">
          <h3 className="text-sm font-semibold text-muted-foreground mb-3">Quick Start</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {quickStartTemplates.map(t => (
              <Card key={t.id} className="cursor-pointer hover:shadow-md hover:border-primary/30 transition-all" onClick={() => handleSelectDisease(t.id, t.name)}>
                <CardContent className="p-4 flex items-center gap-3">
                  <span className="text-2xl">{t.icon}</span>
                  <span className="font-medium text-sm">{t.name}</span>
                  <ChevronRight className="h-4 w-4 text-muted-foreground ml-auto" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Recent Queries */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Clock className="h-4 w-4 text-muted-foreground" /> Recent Queries
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {recentQueries.map(q => {
                const disease = diseases.find(d => d.name === q.disease);
                return (
                  <button
                    key={q.id}
                    onClick={() => disease && handleSelectDisease(disease.id, disease.name)}
                    className="flex items-center justify-between w-full p-2.5 rounded-lg hover:bg-accent text-left text-sm transition-colors"
                  >
                    <div>
                      <span className="font-medium">{q.disease}</span>
                      <span className="text-xs text-muted-foreground ml-2">{q.date}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">{q.candidates} candidates</Badge>
                      <span className="text-xs font-bold" style={{ color: scoreColor(q.topScore) }}>{q.topScore}</span>
                    </div>
                  </button>
                );
              })}
            </CardContent>
          </Card>

          {/* Trending Diseases */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-muted-foreground" /> Trending Diseases
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {filteredTrending.map((t, i) => {
                const disease = diseases.find(d => d.name === t.name || d.name.includes(t.name.split(' ')[0]));
                return (
                  <button
                    key={i}
                    onClick={() => disease && handleSelectDisease(disease.id, disease.name)}
                    className="flex items-center justify-between w-full p-2.5 rounded-lg hover:bg-accent text-left text-sm transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{t.name}</span>
                      <span className="text-xs text-emerald-600 flex items-center gap-0.5">
                        <TrendingUp className="h-3 w-3" />+{t.change}%
                      </span>
                    </div>
                    <Badge variant="outline" className="text-xs">{t.queries} queries</Badge>
                  </button>
                );
              })}
            </CardContent>
          </Card>
        </div>

        {/* Advanced Search */}
        <Collapsible open={showAdvanced} onOpenChange={setShowAdvanced} className="mt-6">
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full">
              <Filter className="h-4 w-4 mr-2" />
              {showAdvanced ? 'Hide' : 'Show'} Advanced Search
              <ChevronDown className={`h-4 w-4 ml-auto transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-4">
            <Card>
              <CardContent className="p-6 space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div>
                    <label className="text-sm font-medium mb-1.5 block">Therapeutic Area</label>
                    <Select value={therapeuticArea} onValueChange={setTherapeuticArea}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Areas</SelectItem>
                        {therapeuticAreas.map(a => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-sm font-medium mb-1.5 block">Prevalence</label>
                    <Select defaultValue="all">
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">Any</SelectItem>
                        <SelectItem value="rare">Rare (&lt;1/2000)</SelectItem>
                        <SelectItem value="common">Common</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-end gap-2 pb-1">
                    <Checkbox id="genetic" checked={geneticOnly} onCheckedChange={v => setGeneticOnly(!!v)} />
                    <label htmlFor="genetic" className="text-sm font-medium">Genetic basis only</label>
                  </div>
                </div>
                <Button className="w-full" style={{ backgroundColor: PRIMARY }} onClick={handleSearch}>
                  <Search className="h-4 w-4 mr-2" /> Search with Filters
                </Button>
              </CardContent>
            </Card>
          </CollapsibleContent>
        </Collapsible>

        {/* Browse All Diseases */}
        <div className="mt-6">
          <h3 className="text-sm font-semibold text-muted-foreground mb-3">Browse All Diseases</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {diseases
              .filter(d => therapeuticArea === 'all' || d.therapeuticArea === therapeuticArea)
              .filter(d => !geneticOnly || d.geneticBasis)
              .map(d => (
                <Card key={d.id} className="cursor-pointer hover:shadow-md hover:border-primary/30 transition-all" onClick={() => handleSelectDisease(d.id, d.name)}>
                  <CardContent className="p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-medium text-sm">{d.name}</h4>
                      <Badge variant="secondary" className="text-[10px] font-mono">{d.icdCode}</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2">{d.description}</p>
                    <div className="flex items-center gap-2 mt-2">
                      <Badge variant="outline" className="text-[10px]">{d.therapeuticArea}</Badge>
                      <span className="text-[10px] text-muted-foreground">{d.prevalence}</span>
                    </div>
                  </CardContent>
                </Card>
              ))}
          </div>
        </div>
      </div>
    </FadeIn>
  );
}

// FE-025 ROOT FIX: the local `function Progress({ value, max })` that
// shadowed the shadcn Progress has been DELETED. The canonical shadcn
// Progress (imported above from @/components/ui/progress) is now the single
// source of truth. It takes `value` (0-100 percentage) and an optional
// `className` — NOT `{ value, max }`. All call sites have been updated.

// ═══════════════════════════════════════════
// 2. SEARCH RESULTS SCREEN
// ═══════════════════════════════════════════

function SearchResultsScreen() {
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

// ═══════════════════════════════════════════
// 3. CANDIDATE DETAIL SCREEN
// ═══════════════════════════════════════════

function CandidateDetailScreen() {
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

// ═══════════════════════════════════════════
// SUB-COMPONENTS FOR CANDIDATE DETAIL
// ═══════════════════════════════════════════

function PathwayDiagram({ candidate, disease }: { candidate: DrugCandidate; disease: Disease }) {
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

function ADMETRadarChart({ data }: { data: ADMETProfile }) {
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

function PhaseDistributionChart({ trials }: { trials: ClinicalTrial[] }) {
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

function PatentTimeline({ patents }: { patents: Patent[] }) {
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

// ═══════════════════════════════════════════
// 4. KNOWLEDGE GRAPH SCREEN
// ═══════════════════════════════════════════

/**
 * FE-018 ROOT FIX: Compute positions for real KG nodes using a circular
 * layout when pre-computed positions are missing. The previous code
 * initialized positions from graphNodes (empty array from empty-defaults.ts),
 * producing an empty Map. When real KG nodes arrived from /api/knowledge-graph,
 * they had no entries in positions — every edge and node returned null.
 *
 * This helper builds a Map with a circular layout for nodes that don't
 * already have pre-computed positions. It is called whenever the node set
 * changes so real nodes always get positions.
 */
function computePositions(
  nodes: Array<{ id: string; x?: number; y?: number }>,
  existing?: Map<string, { x: number; y: number }>
): Map<string, { x: number; y: number }> {
  const pos = new Map<string, { x: number; y: number }>(existing);
  const cx = 400, cy = 250, radius = 180;
  const needsLayout = nodes.filter(n => !pos.has(n.id));
  needsLayout.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / Math.max(needsLayout.length, 1) - Math.PI / 2;
    pos.set(n.id, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) });
  });
  return pos;
}

function KnowledgeGraphScreen() {
  const { navigate } = useDrugOSNav();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [nodeFilters, setNodeFilters] = useState<Record<string, boolean>>({ drug: true, disease: true, gene: true, protein: true, pathway: true });
  const [evidenceThreshold, setEvidenceThreshold] = useState(0.3);
  // FE-018 ROOT FIX: Start with empty Map and compute positions dynamically
  // whenever nodes change. Pre-computed positions from graphNodes (empty) are
  // merged with auto-generated circular-layout positions for real nodes.
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(() => new Map());

  // FE-001 + FE-003 ROOT FIX: Call the real /api/knowledge-graph endpoint.
  // The previous code rendered mock graphNodes/graphEdges. Now we call the
  // real KG service (returns 503 if KG_SERVICE_URL is not set, which we
  // surface honestly). When the KG service IS deployed, we merge the real
  // nodes/edges with the mock ones for display.
  const { data: kgData, loading: kgLoading, error: kgError } = useKnowledgeGraph({
    drug: searchQuery.length >= 2 ? searchQuery : undefined,
  });

  // FE-067 ROOT FIX: "View candidate detail" button used to look up the
  // clicked drug node in the MOCK `drugCandidates` array. For real RL
  // candidates (sourced from /api/rl), the mock lookup returned undefined
  // and the button silently did nothing. Now we fetch the real RL top-N
  // candidates via the same /api/rl endpoint the dashboard uses, and look
  // up the clicked drug by name in that real list. The candidate's `id`
  // for navigation is synthesized as `${drug}|${disease}` when the API
  // does not return one (the RL CSV doesn't have a stable row id), so the
  // navigation is stable across re-renders.
  const { data: rlData } = useRlCandidates({ limit: 200 });
  const realRlCandidates = useMemo(() => {
    const list = rlData?.candidates || [];
    return list.map((c: any) => ({
      id: c.id || `${c.drug}|${c.disease}`,
      drugName: c.drug as string,
      diseaseName: c.disease as string,
      overallScore: c.overallScore as number,
    }));
  }, [rlData]);

  const realNodes = kgData?.nodes || [];
  const realEdges = kgData?.edges || [];

  // FE-018 ROOT FIX: Recompute positions whenever the merged node set changes.
  // Real nodes from the KG service get circular-layout positions so they are
  // actually visible. Pre-computed positions (if any) are preserved.
  const allNodes = useMemo(() => [...graphNodes, ...realNodes], [realNodes]);
  const allEdges = useMemo(() => [...graphEdges, ...realEdges], [realEdges]);
  useEffect(() => {
    setPositions(prev => computePositions(allNodes, prev));
  }, [allNodes]);

  const filteredNodes = allNodes.filter(n => nodeFilters[n.type]);
  // FE-019 ROOT FIX: GraphEdge has `weight?: number` and `type: string` —
  // there is NO `evidence` field and `relation` is only a backward-compat alias.
  // The Python phase2/service.py returns `type` not `relation`. Use `e.weight`
  // (with fallback to 0.5) for filtering/coloring and `e.type` for labels.
  const filteredEdges = allEdges.filter(e => {
    const src = allNodes.find(n => n.id === e.source);
    const tgt = allNodes.find(n => n.id === e.target);
    const w = (e as any).weight ?? 0.5;
    return w >= evidenceThreshold && src && tgt && nodeFilters[src.type] && nodeFilters[tgt.type];
  });

  const searchedNodes = searchQuery.length >= 2
    ? filteredNodes.filter(n => n.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : filteredNodes;

  const nodeColors: Record<string, string> = { drug: PRIMARY, disease: ACCENT_RED, gene: '#3B82F6', protein: ACCENT_GREEN, pathway: ACCENT_ORANGE };

  const connectedToSelected = useMemo(() => {
    if (!selectedNode) return new Set<string>();
    const s = new Set<string>();
    s.add(selectedNode);
    filteredEdges.forEach(e => {
      if (e.source === selectedNode) s.add(e.target);
      if (e.target === selectedNode) s.add(e.source);
    });
    return s;
  }, [selectedNode, filteredEdges]);

  return (
    <FadeIn>
      <PageHeader title="Knowledge Graph Explorer" description="Explore relationships between drugs, diseases, genes, proteins, and pathways" />
      {kgLoading && (
        <div className="mb-3 text-xs text-muted-foreground flex items-center gap-2">
          <RefreshCw className="h-3 w-3 animate-spin" /> Querying Neo4j knowledge graph service...
        </div>
      )}
      {kgError && (
        <div className="mb-3 text-xs text-amber-700 p-2 border border-amber-200 rounded bg-amber-50">
          <strong>KG service status:</strong> {kgError.message} — showing demo graph data.
          Set <code>KG_SERVICE_URL</code> to connect the real Neo4j Phase 2 service.
        </div>
      )}
      {kgData && realNodes.length > 0 && (
        <div className="mb-3 text-xs text-emerald-700 p-2 border border-emerald-200 rounded bg-emerald-50">
          <strong>Live Neo4j data:</strong> {realNodes.length} nodes, {realEdges.length} edges from the KG service.
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-4">
        {/* Sidebar */}
        <div className="w-full lg:w-64 space-y-4 shrink-0">
          <Card>
            <CardContent className="p-4">
              <Input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Search entities..." className="mb-3" />
              <div className="space-y-2">
                <p className="text-xs font-semibold text-muted-foreground">Node Types</p>
                {Object.entries(nodeFilters).map(([type, checked]) => (
                  <label key={type} className="flex items-center gap-2 cursor-pointer">
                    <Checkbox checked={checked} onCheckedChange={v => setNodeFilters(p => ({ ...p, [type]: !!v }))} />
                    <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: nodeColors[type] }} />
                    <span className="text-sm capitalize">{type}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{allNodes.filter(n => n.type === type).length}</span>
                  </label>
                ))}
              </div>
              <Separator className="my-3" />
              <div>
                <p className="text-xs font-semibold text-muted-foreground mb-2">Evidence Threshold: {evidenceThreshold.toFixed(1)}</p>
                <Slider value={[evidenceThreshold]} onValueChange={v => setEvidenceThreshold(v[0])} min={0} max={1} step={0.1} />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-semibold text-muted-foreground mb-2">Statistics</p>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Nodes</span><span className="font-medium">{searchedNodes.length}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Edges</span><span className="font-medium">{filteredEdges.length}</span></div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-semibold text-muted-foreground mb-2">Quick Start</p>
              <div className="space-y-1.5">
                <button onClick={() => setSearchQuery('BRCA1')} className="text-xs text-primary hover:underline block w-full text-left">Find drugs targeting BRCA1</button>
                <button onClick={() => setSearchQuery("Alzheimer's")} className="text-xs text-primary hover:underline block w-full text-left">Show pathways in Alzheimer's</button>
                <button onClick={() => setSearchQuery('Memantine')} className="text-xs text-primary hover:underline block w-full text-left">Memantine mechanism of action</button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Graph Area */}
        <Card className="flex-1">
          <CardContent className="p-0 relative">
            <KnowledgeGraphViewer nodes={searchedNodes} edges={filteredEdges} height={500} />
            {/* Selected node info */}
            {selectedNode && (() => {
              // FE-020 ROOT FIX: Search in the merged allNodes array (which
              // includes real nodes from the KG service) instead of just
              // graphNodes (which is the empty array from empty-defaults.ts).
              // Previously find() always returned undefined and the panel
              // NEVER rendered — researchers could not see node details.
              const node = allNodes.find(n => n.id === selectedNode);
              if (!node) return null;
              const nodeEdges = filteredEdges.filter(e => e.source === selectedNode || e.target === selectedNode);
              return (
                <div className="absolute bottom-3 left-3 bg-background/90 backdrop-blur-sm border rounded-lg p-3 max-w-[240px]">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-sm">{node.label}</span>
                    <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={() => setSelectedNode(null)}>×</Button>
                  </div>
                  <Badge variant="secondary" className="text-[10px]" style={{ color: nodeColors[node.type] }}>{node.type}</Badge>
                  <p className="text-xs text-muted-foreground mt-1">{nodeEdges.length} connections</p>
                  {node.type === 'drug' && (
                    <Button variant="link" size="sm" className="h-6 p-0 text-xs mt-1" onClick={() => {
                      // FE-067 ROOT FIX: Look up the clicked drug in the
                      // REAL RL candidate list (sourced from /api/rl).
                      // Previously this searched the mock `drugCandidates`
                      // array, which silently failed for any drug that
                      // wasn't in the mock set. Now we prefer the real RL
                      // data; if the RL service isn't deployed yet, we
                      // fall back to the mock array so the button still
                      // works for demo drugs.
                      const cand = realRlCandidates.find(c => c.drugName === node.label)
                        || drugCandidates.find(c => c.drugName === node.label);
                      if (cand) navigate({ page: 'app', section: 'candidate', id: cand.id });
                    }}>View candidate detail →</Button>
                  )}
                </div>
              );
            })()}
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 5. CLINICAL TRIALS SCREEN
// ═══════════════════════════════════════════

function ClinicalTrialsScreen() {
  const [search, setSearch] = useState('');
  const [phaseFilter, setPhaseFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedTrial, setSelectedTrial] = useState<ClinicalTrial | null>(null);

  // FE-001 ROOT FIX: Real ClinicalTrials.gov v2 API integration. The previous
  // code rendered a local `clinicalTrials` mock array of 5 hardcoded entries.
  // Now we query the real CT.gov database (15,000+ trials) via the API.
  // The search input is debounced by the hook (300ms).
  const { data: trialsData, loading: trialsLoading, error: trialsError } = useClinicalTrialsSearch({
    condition: search.trim() || undefined,
    limit: 50,
  });

  // Map the real API response to the UI's ClinicalTrial shape.
  const realTrials: ClinicalTrial[] = useMemo(() => {
    if (!trialsData?.items) return [];
    return trialsData.items.map((t: any) => ({
      id: t.nctId,
      nctId: t.nctId,
      title: t.title,
      phase: t.phase || 'N/A',
      status: t.status,
      enrollment: t.enrollment,
      startDate: t.startDate,
      completionDate: t.completionDate,
      drugName: (t.interventions || []).join(', '),
      disease: (t.conditions || []).join(', '),
      outcome: t.briefSummary || '',
    }));
  }, [trialsData]);

  const filtered = useMemo(() => {
    return realTrials.filter(t => {
      const matchPhase = phaseFilter === 'all' || t.phase === phaseFilter;
      const matchStatus = statusFilter === 'all' || t.status === statusFilter;
      return matchPhase && matchStatus;
    });
  }, [realTrials, phaseFilter, statusFilter]);

  const phases = [...new Set(realTrials.map(t => t.phase))];
  const statuses = [...new Set(realTrials.map(t => t.status))];

  return (
    <FadeIn>
      <PageHeader title="Clinical Trial Search" description="Search ClinicalTrials.gov data for drug repurposing trials (real API)" />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search by disease (e.g., Huntington's)..." className="pl-9" />
        </div>
        <Select value={phaseFilter} onValueChange={setPhaseFilter}>
          <SelectTrigger className="w-36"><SelectValue placeholder="Phase" /></SelectTrigger>
          <SelectContent><SelectItem value="all">All Phases</SelectItem>{phases.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent><SelectItem value="all">All Status</SelectItem>{statuses.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="p-0">
          {trialsLoading && <LoadingSpinner label="Searching ClinicalTrials.gov..." />}
          {trialsError && <ErrorDisplay error={trialsError} />}
          {!trialsLoading && !trialsError && (
            <Table>
              <TableHeader><TableRow className="bg-muted/50"><TableHead>NCT ID</TableHead><TableHead>Title</TableHead><TableHead>Phase</TableHead><TableHead>Status</TableHead><TableHead>Enrollment</TableHead><TableHead>Dates</TableHead></TableRow></TableHeader>
              <TableBody>
                {filtered.map(t => (
                  <TableRow key={t.id} className="cursor-pointer hover:bg-muted/30" onClick={() => setSelectedTrial(selectedTrial?.id === t.id ? null : t)}>
                    <TableCell><span className="font-mono text-xs text-primary">{t.nctId}</span></TableCell>
                    <TableCell className="max-w-[300px]"><span className="text-sm line-clamp-2">{t.title}</span></TableCell>
                    <TableCell><Badge variant="secondary" className="text-xs">{t.phase}</Badge></TableCell>
                    <TableCell><Badge className="text-xs">{t.status}</Badge></TableCell>
                    <TableCell className="text-sm">{t.enrollment ?? '—'}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{t.startDate || '—'} → {t.completionDate || '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!trialsLoading && !trialsError && filtered.length === 0 && !search && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>Enter a disease name to search ClinicalTrials.gov</p>
            </div>
          )}
        </CardContent>
      </Card>

      {selectedTrial && (
        <Card className="mt-4">
          <CardHeader className="pb-3"><CardTitle className="text-base">{selectedTrial.title}</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div><span className="text-muted-foreground">NCT ID:</span> <span className="font-mono">{selectedTrial.nctId}</span></div>
              <div><span className="text-muted-foreground">Phase:</span> <Badge variant="secondary">{selectedTrial.phase}</Badge></div>
              <div><span className="text-muted-foreground">Status:</span> <Badge>{selectedTrial.status}</Badge></div>
              <div><span className="text-muted-foreground">Enrollment:</span> {selectedTrial.enrollment ?? '—'}</div>
            </div>
            <div><span className="text-muted-foreground">Drug:</span> {selectedTrial.drugName} · <span className="text-muted-foreground">Disease:</span> {selectedTrial.disease}</div>
            {selectedTrial.outcome && <div><span className="text-muted-foreground">Summary:</span> {selectedTrial.outcome.slice(0, 300)}...</div>}
          </CardContent>
        </Card>
      )}
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 6. SAFETY PROFILE SCREEN
// ═══════════════════════════════════════════

function SafetyProfileScreen() {
  // FE-017 ROOT FIX: drugCandidates is the empty array from empty-defaults.ts.
  // drugCandidates[0] is undefined — accessing .drugName on undefined throws
  // TypeError during initial state initialization, so the component never mounts.
  // Fix: Initialize selectedDrug to empty string. All downstream code that
  // depended on drugCandidates[0] as a fallback now guards against empty.
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  const [drugSearch, setDrugSearch] = useState('');
  const candidate = drugCandidates.find(c => c.drugName === selectedDrug) || null;
  const admet = admetProfiles.find(a => a.drugName === selectedDrug);
  const offTargets = offTargetPredictions.filter(o => o.drugName === selectedDrug);
  const interactions = drugInteractions.filter(d => d.drug1 === selectedDrug);
  const [ddiQuery, setDdiQuery] = useState('');
  const uniqueDrugNames = [...new Set(drugCandidates.map(c => c.drugName))];

  // FE-001 ROOT FIX: Real openFDA adverse event data. The previous code
  // showed mock safety scores that had ZERO relationship to real FDA data.
  // Now we fetch the real adverse event report count, serious report count,
  // and top reactions from the FDA Adverse Event Reporting System (FAERS)
  // via the /api/safety/[drug] endpoint.
  const { data: safetyData, loading: safetyLoading, error: safetyError } = useDrugSafety(selectedDrug);

  const ddiResults = useMemo(() => {
    if (!ddiQuery.trim()) return [];
    return drugInteractions.filter(d =>
      ((d.drug1 ?? "").toLowerCase().includes(ddiQuery.toLowerCase()) || (d.drug2 ?? "").toLowerCase().includes(ddiQuery.toLowerCase())) &&
      (d.drug1 === selectedDrug || d.drug2 === selectedDrug)
    );
  }, [ddiQuery, selectedDrug]);

  // Also search real drugs via RxNorm when the user types in the drug search.
  const { data: drugSearchResults } = useDrugSearch(drugSearch, 3);
  const realDrugOptions = drugSearchResults?.items?.map(d => d.name) || [];

  return (
    <FadeIn>
      <PageHeader title="Safety Profile Dashboard" description="Comprehensive safety analysis (real FDA adverse event data via openFDA)" />

      <div className="mb-4 flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={drugSearch || selectedDrug}
            onChange={e => {
              setDrugSearch(e.target.value);
              setSelectedDrug(e.target.value);
            }}
            placeholder="Search for a drug (real RxNorm)..."
            className="pl-9"
          />
          {realDrugOptions.length > 0 && drugSearch.length >= 3 && (
            <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-xl shadow-xl overflow-hidden max-h-60 overflow-y-auto">
              {realDrugOptions.slice(0, 8).map(name => (
                <button
                  key={name}
                  onClick={() => { setSelectedDrug(name); setDrugSearch(''); }}
                  className="flex items-center w-full px-4 py-2 text-sm hover:bg-accent text-left"
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>
        <Select value={selectedDrug} onValueChange={setSelectedDrug}>
          <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
          <SelectContent>{uniqueDrugNames.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      {/* Real openFDA safety stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatCard icon={ShieldCheck} value={safetyData?.totalReports ?? '—'} label="FDA Adverse Event Reports" color={ACCENT_GREEN} />
        <StatCard icon={AlertTriangle} value={safetyData?.seriousReports ?? '—'} label="Serious Reports" color={ACCENT_ORANGE} />
        <StatCard icon={AlertCircle} value={safetyData?.topReactions?.length ?? 0} label="Top Reactions Reported" color={ACCENT_RED} />
      </div>

      {safetyLoading && <LoadingSpinner label="Fetching openFDA adverse event data..." />}
      {safetyError && <ErrorDisplay error={safetyError} />}

      {safetyData && (
        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Top Reported Adverse Events (FDA FAERS)</CardTitle>
          </CardHeader>
          <CardContent>
            {safetyData.topReactions && safetyData.topReactions.length > 0 ? (
              <div className="space-y-2">
                {safetyData.topReactions.slice(0, 10).map((r, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span>{r.term}</span>
                    <Badge variant="secondary">{r.count} reports</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No adverse event reports found for this drug.</p>
            )}
            <p className="text-xs text-muted-foreground mt-4 italic">{safetyData.disclaimer}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Safety Tier</CardTitle>
              <SafetyBadge tier={candidate?.safetyTier ?? 'unknown'} />
            </div>
          </CardHeader>
          <CardContent>
            {admet && <ADMETRadarChart data={admet} />}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Off-Target Interaction Profile</CardTitle></CardHeader>
          <CardContent>
            {offTargets.length > 0 ? (
              <div className="space-y-2">
                {offTargets.map((o, i) => (
                  <div key={i} className="flex items-center justify-between p-2.5 border rounded-lg">
                    <div>
                      <span className="text-sm font-medium">{o.target}</span>
                      <span className="text-xs text-muted-foreground ml-2">({o.organSystem})</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs">{Math.round(o.probability * 100)}%</span>
                      <Badge variant={o.severity === 'high' ? 'destructive' : o.severity === 'medium' ? 'secondary' : 'outline'} className="text-xs">{o.severity}</Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : <p className="text-sm text-muted-foreground">No off-target predictions</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Drug-Drug Interaction Checker</CardTitle></CardHeader>
          <CardContent>
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input value={ddiQuery} onChange={e => setDdiQuery(e.target.value)} placeholder="Enter medication name to check..." className="pl-9" />
            </div>
            {ddiResults.length > 0 ? ddiResults.map((r, i) => (
              <div key={i} className="p-2.5 border rounded-lg mb-2">
                <div className="flex items-center gap-2">
                  <Badge variant={r.severity === 'contraindicated' ? 'destructive' : r.severity === 'major' ? 'secondary' : 'outline'} className="text-xs">{r.severity}</Badge>
                  <span className="text-sm">{r.drug1} ↔ {r.drug2}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-1">{r.description}</p>
              </div>
            )) : ddiQuery && <p className="text-sm text-muted-foreground">No interactions found</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">Black Box Warning Status</CardTitle></CardHeader>
          <CardContent>
            {/* FE-004 ROOT FIX: Previously this card fabricated 4 adverse-event
                frequencies with the legacy pseudo-random API — a patient-safety
                hazard. Real FAERS top-reactions are shown in the "Top Reported
                Adverse Events" card above. This card now shows the honest
                black-box-warning status: openFDA label integration is not yet
                wired. */}
            <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg">
              <div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-amber-600" /><span className="text-sm font-medium text-amber-700">Black-box warning status not loaded</span></div>
              <p className="text-xs text-amber-700 mt-1">openFDA label integration (black-box warnings, REMS programs, contraindications) is not yet wired into this view. Verify black-box warning status directly on the <a href={`https://api.fda.gov/drug/label.json?search=openfda.brand_name:${encodeURIComponent(selectedDrug)}`} target="_blank" rel="noopener noreferrer" className="underline">FDA label</a> before any clinical decision.</p>
            </div>
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 7. IP PATENTS SCREEN
// ═══════════════════════════════════════════

function IPPatentsScreen() {
  // FE-017 ROOT FIX: Same crash as SafetyProfileScreen — drugCandidates[0]
  // is undefined because drugCandidates is the empty array. Initialize to
  // empty string and guard all downstream accesses.
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  const uniqueDrugNames = [...new Set(drugCandidates.map(c => c.drugName))];
  const candidate = drugCandidates.find(c => c.drugName === selectedDrug) || null;
  // FE-005 ROOT FIX: Fetch real patents via /api/patents/search.
  const { data: patentSearchResult, loading: patentsLoading, error: patentsError } = useApiList(
    () => selectedDrug.trim().length >= 2 ? api.searchPatents(selectedDrug.trim()) : Promise.resolve({ items: [] as any[] }),
    [selectedDrug]
  );
  const relatedPatents: Patent[] = (patentSearchResult?.items || []).map((p: any, i: number) => ({
    id: p.id || `pat-${i}`,
    drugName: selectedDrug,
    title: p.title || p.patentTitle || 'Untitled',
    patentNumber: p.patentNumber || p.number || '—',
    status: p.status || 'unknown',
    jurisdiction: p.jurisdiction || '—',
    claims: p.claims ?? 0,
    assignee: p.assignee || '—',
    filingDate: p.filingDate || '',
    grantDate: p.grantDate || '',
    abstract: p.abstract || '',
    expirationDate: p.expirationDate ?? null,
  }));
  const ipRiskScore = useMemo(() => {
    if (relatedPatents.length === 0) return null;
    const active = relatedPatents.filter(p => p.status === 'active').length;
    return Math.min(100, active * 10 + Math.max(0, 30 - relatedPatents.length));
  }, [relatedPatents]);

  return (
    <FadeIn>
      <PageHeader title="IP & Patent Status" description="Track intellectual property and patent status for candidates" />

      <div className="mb-4">
        <Select value={selectedDrug} onValueChange={setSelectedDrug}>
          <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
          <SelectContent>{uniqueDrugNames.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      {patentsError && (
        <div className="mb-4">
          <ErrorDisplay error={patentsError} />
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 mb-6">
        {/* FE-049 ROOT FIX (v118): the stat cards previously filtered the
            `patents` array imported from @/lib/empty-defaults — which is
            ALWAYS empty — so every card showed 0. They now filter the REAL
            `relatedPatents` array sourced from /api/patents/search. */}
        <StatCard icon={Scale} value={relatedPatents.filter(p => p.status === 'active').length} label="Active Patents" color={ACCENT_GREEN} />
        <StatCard icon={Clock} value={relatedPatents.filter(p => p.status === 'pending').length} label="Pending" color={ACCENT_ORANGE} />
        <StatCard icon={FileText} value={relatedPatents.filter(p => p.status === 'expired').length} label="Expired" />
        <StatCard icon={AlertCircle} value={relatedPatents.filter(p => p.status === 'abandoned').length} label="Abandoned" color={ACCENT_RED} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Patent Search Results</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {relatedPatents.length > 0 ? relatedPatents.map(p => (
                <div key={p.id} className="p-4 border rounded-lg">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{p.title}</span>
                    <Badge variant={p.status === 'active' ? 'default' : p.status === 'expired' ? 'secondary' : p.status === 'pending' ? 'outline' : 'destructive'}>{p.status}</Badge>
                  </div>
                  <div className="text-xs text-muted-foreground space-y-0.5">
                    <p>{p.patentNumber} · {p.jurisdiction} · {p.claims} claims</p>
                    <p>Assignee: {p.assignee}</p>
                    <p>Filed: {p.filingDate} · Expires: {p.expirationDate}</p>
                  </div>
                </div>
              )) : <p className="text-sm text-muted-foreground">No patents found for {selectedDrug}</p>}
            </CardContent>
          </Card>
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Freedom to Operate</CardTitle></CardHeader>
            <CardContent>
              <div className="text-center">
                <div className="text-3xl font-bold" style={{ color: candidate?.ipStatus === 'Off-Patent' || candidate?.ipStatus === 'Patent Expired' ? ACCENT_GREEN : candidate?.ipStatus === null ? '#94A3B8' : ACCENT_ORANGE }}>
                  {candidate?.ipStatus === 'Off-Patent' || candidate?.ipStatus === 'Patent Expired' ? 'Clear' : candidate?.ipStatus === 'Novel Use Patentable' ? 'Partial' : candidate?.ipStatus === null ? 'N/A' : 'Restricted'}
                </div>
                <p className="text-sm text-muted-foreground mt-1">{candidate?.ipStatus ?? 'N/A'}</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">IP Risk Score</CardTitle></CardHeader>
            <CardContent>
              <div className="text-center">
                {patentsLoading ? (
                  <div className="text-sm text-muted-foreground"><RefreshCw className="h-3 w-3 inline mr-1 animate-spin" />Searching patents...</div>
                ) : ipRiskScore === null ? (
                  <>
                    <div className="text-3xl font-bold text-slate-400">N/A</div>
                    <p className="text-sm text-muted-foreground mt-1">{selectedDrug ? 'No patent data available for this drug.' : 'Select a drug to compute IP risk.'}</p>
                  </>
                ) : (
                  <>
                    <div className="text-3xl font-bold" style={{ color: scoreColor(ipRiskScore) }}>{ipRiskScore}</div>
                    <p className="text-sm text-muted-foreground mt-1">out of 100 · {relatedPatents.filter(p => p.status === 'active').length} active patent(s)</p>
                  </>
                )}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Patent Timeline</CardTitle></CardHeader>
            <CardContent><PatentTimeline patents={relatedPatents} /></CardContent>
          </Card>
        </div>
      </div>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 8. EVIDENCE BUILDER SCREEN
// ═══════════════════════════════════════════

function EvidenceBuilderScreen() {
  const [selectedDrug, setSelectedDrug] = useState<string>('');
  const [selectedDisease, setSelectedDisease] = useState<string>('');
  const [drugQuery, setDrugQuery] = useState('');
  const [diseaseQuery, setDiseaseQuery] = useState('');
  const { data: drugSearchResults } = useDrugSearch(drugQuery, 3);
  const { data: diseaseSearchResults } = useDiseaseSearch(diseaseQuery, 2);
  const drugOptions = drugSearchResults?.items?.map(d => d.name) || [];
  const diseaseOptions = diseaseSearchResults?.items?.map(d => ({ id: d.descriptorUi, name: d.name })) || [];
  const canBuild = selectedDrug.trim().length > 0 && selectedDisease.trim().length > 0;
  const [selectedEvidence, setSelectedEvidence] = useState<Set<string>>(new Set());
  const [template, setTemplate] = useState('internal');

  // FE-001 ROOT FIX: Real evidence package builder. The previous code had
  // a "Build Evidence Package" button that did nothing — it was just a
  // styled <Button> with no onClick. Now we call the real
  // /api/evidence-package endpoint which assembles a bundle from PubMed,
  // ClinicalTrials.gov, and openFDA data.
  const { data: builtPackage, loading: building, error: buildError, build } = useBuildEvidencePackage();

  // FE-049 ROOT FIX (v118): the previous code computed `availableEvidence`
  // and `diseaseEvidence` by filtering `evidenceItems` imported from
  // @/lib/empty-defaults — which is ALWAYS empty. So the "Available Evidence"
  // panel was always blank, and the "Selected" panel could never find the
  // evidence by id (line 2109 `evidenceItems.find()` always returned undefined).
  //
  // Real evidence comes from the BUILT package — the /api/evidence-package
  // endpoint returns literature, clinicalTrials, and safety sections each
  // with `items` arrays. We flatten them into a single list once a package
  // has been built. Before the first build, we show an honest empty state
  // telling the user to click "Build Evidence Package".
  const allEvidence: Array<{ id: string; type: string; title: string; source: string; year?: number; summary?: string }> = useMemo(() => {
    if (!builtPackage) return [];
    const pkg = (builtPackage as any).package || {};
    const lit: any[] = pkg?.literature?.items || [];
    const trials: any[] = pkg?.clinicalTrials?.items || [];
    const safetyReactions: any[] = (pkg?.safety?.topReactions) || [];
    return [
      ...lit.map((a: any, i: number) => ({
        id: `lit-${a.pmid || i}`,
        type: 'literature',
        title: a.title || 'Untitled article',
        source: 'PubMed',
        year: a.pubDate ? Number(String(a.pubDate).slice(0, 4)) : undefined,
        summary: a.abstract || '',
      })),
      ...trials.map((t: any, i: number) => ({
        id: `trial-${t.nctId || i}`,
        type: 'clinical',
        title: t.title || 'Untitled trial',
        source: 'ClinicalTrials.gov',
        year: t.startDate ? Number(String(t.startDate).slice(0, 4)) : undefined,
        summary: t.briefSummary || '',
      })),
      ...safetyReactions.map((r: any, i: number) => ({
        id: `safety-${i}`,
        type: 'safety',
        title: `${r.term || 'Reaction'} (${r.count} reports)`,
        source: 'openFDA FAERS',
        year: undefined,
        summary: '',
      })),
    ];
  }, [builtPackage]);

  // FE-049 ROOT FIX (v118): `availableEvidence` and `diseaseEvidence` now
  // derive from the REAL built package (allEvidence) — not the empty
  // `evidenceItems` array. The drug/disease filter is no longer relevant
  // (the package was already built for the selected drug+disease), so we
  // show all evidence from the package.
  const availableEvidence = allEvidence;
  const diseaseEvidence: typeof allEvidence = [];

  const toggleEvidence = (id: string) => {
    setSelectedEvidence(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleBuild = () => {
    build({
      drug: selectedDrug,
      disease: selectedDisease,
      notes: `Template: ${template}. Selected evidence: ${[...selectedEvidence].join(', ')}`,
    }).catch(() => { /* error already in state */ });
  };

  // FE-038 ROOT FIX (v118): the "Preview Package" button previously had no
  // onClick. Now it opens a new window with the rendered markdown — same as
  // the "Download markdown" link, but rendered inline (window.open) so the
  // user can read it without downloading. Disabled until a package is built.
  const handlePreview = () => {
    if (!builtPackage) return;
    const md = (builtPackage as any).markdown || '';
    if (!md) return;
    // Render the markdown as a simple <pre> in a new window — no markdown
    // renderer dependency required. Production-grade: a real renderer
    // (react-markdown) can be wired later.
    const w = window.open('', '_blank');
    if (!w) return;
    w.document.write(`<!doctype html><html><head><title>Evidence Package Preview — ${selectedDrug} / ${selectedDisease}</title><style>body{font:14px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}pre{white-space:pre-wrap;background:#f5f5f5;padding:1rem;border-radius:6px;overflow-x:auto}</style></head><body><h1>Evidence Package Preview</h1><p><strong>Drug:</strong> ${selectedDrug}<br><strong>Disease:</strong> ${selectedDisease}<br><strong>Template:</strong> ${template}</p><pre>${md.replace(/</g, '&lt;')}</pre></body></html>`);
    w.document.close();
  };

  const templates = [
    { id: 'internal', name: 'Internal Review' },
    { id: 'pre-ind', name: 'Pre-IND' },
    { id: 'investor', name: 'Investor' },
    { id: 'partnership', name: 'Partnership' },
    { id: 'publication', name: 'Publication' },
    { id: 'grant', name: 'Grant' },
  ];

  return (
    <FadeIn>
      <PageHeader title="Evidence Package Builder" description="Build comprehensive evidence packages (real PubMed + CT.gov + openFDA)" />

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="relative">
          <Input value={drugQuery || selectedDrug} onChange={e => { setDrugQuery(e.target.value); setSelectedDrug(''); }} placeholder="Search drug (RxNorm)..." className="w-48" />
          {drugOptions.length > 0 && drugQuery.length >= 3 && !selectedDrug && (
            <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-lg shadow-xl max-h-60 overflow-y-auto">
              {drugOptions.slice(0, 8).map(name => <button key={name} onClick={() => { setSelectedDrug(name); setDrugQuery(''); }} className="block w-full px-3 py-2 text-sm text-left hover:bg-accent">{name}</button>)}
            </div>
          )}
        </div>
        <div className="relative">
          <Input value={diseaseQuery || selectedDisease} onChange={e => { setDiseaseQuery(e.target.value); setSelectedDisease(''); }} placeholder="Search disease (MeSH)..." className="w-56" />
          {diseaseOptions.length > 0 && diseaseQuery.length >= 2 && !selectedDisease && (
            <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-lg shadow-xl max-h-60 overflow-y-auto">
              {diseaseOptions.slice(0, 8).map(d => <button key={d.id} onClick={() => { setSelectedDisease(d.name); setDiseaseQuery(''); }} className="block w-full px-3 py-2 text-sm text-left hover:bg-accent">{d.name}</button>)}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Available Evidence */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Available Evidence ({availableEvidence.length + diseaseEvidence.length})</CardTitle>
              <Badge variant="secondary">{selectedEvidence.size} selected</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 max-h-96 overflow-y-auto">
            {[...availableEvidence, ...diseaseEvidence].filter((e, i, arr) => arr.findIndex(x => x.id === e.id) === i).map(ev => (
              <div key={ev.id} className={`p-3 border rounded-lg cursor-pointer transition-colors ${selectedEvidence.has(ev.id) ? 'border-primary bg-primary/5' : 'hover:bg-accent'}`} onClick={() => toggleEvidence(ev.id)}>
                <div className="flex items-center gap-2">
                  {selectedEvidence.has(ev.id) ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4 text-muted-foreground" />}
                  <Badge variant="secondary" className="text-[10px]">{ev.type}</Badge>
                  <span className="text-sm font-medium flex-1">{ev.title}</span>
                  {/* FE-049: `quality` is no longer accessed — the real
                      evidence from PubMed/CT.gov/openFDA does not have a
                       quality score, so we show the source+year instead. */}
                </div>
                <p className="text-xs text-muted-foreground mt-1 ml-6">{ev.source} · {ev.year ?? '—'}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-4">
          {/* Selected Evidence Panel */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Selected ({selectedEvidence.size})</CardTitle></CardHeader>
            <CardContent>
              {selectedEvidence.size === 0 ? (
                <p className="text-sm text-muted-foreground">Click evidence items to add them</p>
              ) : (
                <div className="space-y-1">
                  {/* FE-049 ROOT FIX (v118): look up selected evidence in
                      the REAL built-package list (`allEvidence`), not the
                      empty `evidenceItems` array from empty-defaults. */}
                  {[...selectedEvidence].map(id => {
                    const ev = allEvidence.find(e => e.id === id);
                    return ev ? (
                      <div key={id} className="flex items-center gap-2 text-xs p-1.5 bg-accent rounded">
                        <span className="flex-1 truncate">{ev.title}</span>
                        <button onClick={() => toggleEvidence(id)} className="text-muted-foreground hover:text-foreground"><XCircle className="h-3.5 w-3.5" /></button>
                      </div>
                    ) : null;
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Template Selection */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Template</CardTitle></CardHeader>
            <CardContent className="space-y-1.5">
              {templates.map(t => (
                <button key={t.id} onClick={() => setTemplate(t.id)} className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${template === t.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-accent'}`}>
                  {t.name}
                </button>
              ))}
            </CardContent>
          </Card>

          {/* Actions */}
          <div className="space-y-2">
            <Button className="w-full" style={{ backgroundColor: PRIMARY }} onClick={handleBuild} disabled={building || !canBuild}>
              {building ? (
                <>
                  <RefreshCw className="h-4 w-4 mr-2 animate-spin" /> Building...
                </>
              ) : (
                <>
                  <Package className="h-4 w-4 mr-2" /> Build Evidence Package
                </>
              )}
            </Button>
            {buildError && (
              <div className="text-xs text-red-600 p-2 border border-red-200 rounded">
                {buildError.message}
              </div>
            )}
            {builtPackage && (
              <div className="text-xs text-emerald-700 p-2 border border-emerald-200 rounded bg-emerald-50">
                Built package with {(builtPackage as any).package?.literature?.total || 0} literature articles,
                {' '}{(builtPackage as any).package?.clinicalTrials?.total || 0} clinical trials,
                {' '}{(builtPackage as any).package?.safety?.totalReports || 0} safety reports.
                <div className="mt-1">
                  <button
                    onClick={() => {
                      const blob = new Blob([(builtPackage as any).markdown || ''], { type: 'text/markdown' });
                      const url = URL.createObjectURL(blob);
                      window.open(url, '_blank');
                    }}
                    className="text-primary hover:underline"
                  >
                    Download markdown
                  </button>
                </div>
              </div>
            )}
            <Button variant="outline" className="w-full" onClick={handlePreview} disabled={!builtPackage} title={builtPackage ? 'Open a preview of the built evidence package markdown' : 'Build a package first to enable preview'}>
              <Eye className="h-4 w-4 mr-2" /> Preview Package
            </Button>
          </div>
        </div>
      </div>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 9. REPORT GENERATION SCREEN
// ═══════════════════════════════════════════

function ReportGenerationScreen() {
  const [template, setTemplate] = useState('standard');
  const [selectedDisease, setSelectedDisease] = useState('');
  const [selectedDrug, setSelectedDrug] = useState('');
  const [drugQuery, setDrugQuery] = useState('');
  const [diseaseQuery, setDiseaseQuery] = useState('');
  const { data: builtPackage, loading: generating, error: buildError, build } = useBuildEvidencePackage();
  const { data: drugSearchResults } = useDrugSearch(drugQuery, 3);
  const { data: diseaseSearchResults } = useDiseaseSearch(diseaseQuery, 2);
  const drugOptions = drugSearchResults?.items?.map(d => d.name) || [];
  const diseaseOptions = diseaseSearchResults?.items?.map(d => ({ id: d.descriptorUi, name: d.name })) || [];
  const canGenerate = selectedDrug.trim().length > 0 && selectedDisease.trim().length > 0;

  const templates = [
    { id: 'standard', name: 'Standard Report', desc: 'Comprehensive analysis with all sections', icon: FileText },
    { id: 'executive', name: 'Executive Summary', desc: 'High-level overview for decision makers', icon: BarChart3 },
    { id: 'detailed', name: 'Detailed Analysis', desc: 'Full technical deep-dive', icon: BookOpen },
    { id: 'custom', name: 'Custom Report', desc: 'Configure your own sections', icon: Settings },
  ];

  const handleGenerate = () => {
    if (!canGenerate) return;
    build({ drug: selectedDrug, disease: selectedDisease, notes: `Template: ${template}.` }).catch(() => {});
  };
  const handleDownloadMarkdown = () => {
    if (!builtPackage) return;
    const blob = new Blob([(builtPackage as any).markdown || ''], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = window.document.createElement('a');
    a.href = url; a.download = `report-${selectedDrug}-${selectedDisease}.md`.replace(/\s+/g, '_');
    window.document.body.appendChild(a); a.click(); window.document.body.removeChild(a); URL.revokeObjectURL(url);
  };

  return (
    <FadeIn>
      <PageHeader title="Report Generation" description="Generate and preview repurposing analysis reports" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Template Gallery */}
        <div className="lg:col-span-2 space-y-4">
          <h3 className="text-sm font-semibold text-muted-foreground">Report Template</h3>
          <div className="grid grid-cols-2 gap-3">
            {templates.map(t => (
              <Card key={t.id} className={`cursor-pointer transition-all ${template === t.id ? 'border-primary ring-2 ring-primary/20' : 'hover:border-primary/30'}`} onClick={() => setTemplate(t.id)}>
                <CardContent className="p-4">
                  <t.icon className="h-6 w-6 mb-2" style={{ color: PRIMARY }} />
                  <h4 className="font-medium text-sm">{t.name}</h4>
                  <p className="text-xs text-muted-foreground mt-1">{t.desc}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Preview Panel */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Report Preview</CardTitle></CardHeader>
            <CardContent>
              <div className="border rounded-lg p-6 bg-white min-h-[300px]">
                <div className="text-center border-b pb-4 mb-4">
                  <h2 className="text-lg font-bold" style={{ color: PRIMARY }}>DrugOS Repurposing Report</h2>
                  <p className="text-sm text-muted-foreground">{selectedDisease || 'Select a disease'} — {template.charAt(0).toUpperCase() + template.slice(1)} Report</p>
                  <p className="text-xs text-muted-foreground mt-1">Generated: {new Date().toLocaleDateString()}</p>
                </div>
                <div className="space-y-3">
                  <div><h3 className="font-semibold text-sm mb-1">Executive Summary</h3><div className="h-2 w-full bg-slate-100 rounded" /><div className="h-2 w-3/4 bg-slate-100 rounded mt-1" /></div>
                  <div>
                    <h3 className="font-semibold text-sm mb-1">Subject</h3>
                    <p className="text-xs text-muted-foreground">{selectedDrug || '—'} for {selectedDisease || '—'}</p>
                  </div>
                  <div><h3 className="font-semibold text-sm mb-1">Methodology</h3><div className="h-2 w-full bg-slate-100 rounded" /><div className="h-2 w-5/6 bg-slate-100 rounded mt-1" /></div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Configuration */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Configuration</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-1.5 block">Drug</label>
                <div className="relative">
                  <Input value={drugQuery || selectedDrug} onChange={e => { setDrugQuery(e.target.value); setSelectedDrug(''); }} placeholder="Search drug (RxNorm)..." className="w-full" />
                  {drugOptions.length > 0 && drugQuery.length >= 3 && !selectedDrug && (
                    <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-lg shadow-xl max-h-60 overflow-y-auto">
                      {drugOptions.slice(0, 8).map(name => <button key={name} onClick={() => { setSelectedDrug(name); setDrugQuery(''); }} className="block w-full px-3 py-2 text-sm text-left hover:bg-accent">{name}</button>)}
                    </div>
                  )}
                </div>
              </div>
              <div>
                <label className="text-sm font-medium mb-1.5 block">Disease</label>
                <div className="relative">
                  <Input value={diseaseQuery || selectedDisease} onChange={e => { setDiseaseQuery(e.target.value); setSelectedDisease(''); }} placeholder="Search disease (MeSH)..." className="w-full" />
                  {diseaseOptions.length > 0 && diseaseQuery.length >= 2 && !selectedDisease && (
                    <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-lg shadow-xl max-h-60 overflow-y-auto">
                      {diseaseOptions.slice(0, 8).map(d => <button key={d.id} onClick={() => { setSelectedDisease(d.name); setDiseaseQuery(''); }} className="block w-full px-3 py-2 text-sm text-left hover:bg-accent">{d.name}</button>)}
                    </div>
                  )}
                </div>
              </div>
              <Button className="w-full" style={{ backgroundColor: PRIMARY }} onClick={handleGenerate} disabled={generating || !canGenerate}>
                {generating ? <RefreshCw className="h-4 w-4 mr-2 animate-spin" /> : <FileText className="h-4 w-4 mr-2" />}
                {generating ? 'Generating...' : 'Generate Report (Markdown)'}
              </Button>
              {buildError && (
                <div className="text-xs text-red-600 p-2 border border-red-200 rounded">{buildError.message}</div>
              )}
              {builtPackage && (
                <div className="text-xs text-emerald-700 p-2 border border-emerald-200 rounded bg-emerald-50">
                  <div className="font-medium mb-1">Report ready</div>
                  <button onClick={handleDownloadMarkdown} className="text-primary hover:underline">Download markdown (.md)</button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Report History</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <p className="text-xs text-muted-foreground">Previously generated reports will appear here once the report persistence service is wired.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 10-25. ADDITIONAL SCREENS
// ═══════════════════════════════════════════

function AdvancedSearchScreen() {
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

function SavedQueriesScreen() {
  const [queries, setQueries] = useState(savedQueries);
  const { navigate } = useDrugOSNav();
  return (
    <FadeIn>
      <PageHeader title="Saved Queries" description="Manage and re-run your saved search queries" />
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow className="bg-muted/50"><TableHead>Name</TableHead><TableHead>Disease</TableHead><TableHead>Filters</TableHead><TableHead>Results</TableHead><TableHead>Created</TableHead><TableHead></TableHead></TableRow></TableHeader>
            <TableBody>
              {queries.map(q => (
                <TableRow key={q.id} className="cursor-pointer hover:bg-muted/30" onClick={() => {
                  const disease = diseases.find(d => d.name === q.disease);
                  if (disease) navigate({ page: 'app', section: 'results', id: disease.id });
                }}>
                  <TableCell className="font-medium">{q.name}</TableCell>
                  <TableCell>{q.disease}</TableCell>
                  <TableCell><span className="text-xs text-muted-foreground">{q.filters}</span></TableCell>
                  <TableCell><Badge variant="secondary">{q.results}</Badge></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{q.created}</TableCell>
                  <TableCell><Button variant="ghost" size="sm" className="h-7" onClick={e => { e.stopPropagation(); setQueries(prev => prev.filter(x => x.id !== q.id)); }}><Trash2 className="h-3.5 w-3.5 text-muted-foreground" /></Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </FadeIn>
  );
}

function DrugComparisonScreen() {
  const { navigate } = useDrugOSNav();
  // FE-050 ROOT FIX (v118): previously used `drugCandidates.find(c => c.id === id)`
  // and `drugCandidates.map(c => c.drugName)` directly on the empty-defaults
  // array. The selected drug list was always empty and the comparison table
  // never rendered. Now we fetch real RL candidates via /api/rl for the list.
  const { data: rlData, loading: rlLoading } = useRlCandidates({ limit: 50 });
  const rlCandidates: DrugCandidate[] = useMemo(() =>
    (rlData?.candidates || []).map((rc: any, i: number) => ({
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
      diseaseId: '',
      diseaseName: rc.disease as string,
      brandNames: [],
      genericName: rc.drug as string,
      ipStatus: null,
      targets: null,
      pathways: null,
    })),
    [rlData]
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  useEffect(() => {
    // Pre-select the first two candidates once RL data arrives so the
    // comparison table isn't empty on first load.
    if (selectedIds.length === 0 && rlCandidates.length >= 2) {
      setSelectedIds([rlCandidates[0].id, rlCandidates[1].id]);
    }
  }, [rlCandidates, selectedIds]);
  const compared = selectedIds.map(id => rlCandidates.find(c => c.id === id)).filter(Boolean) as DrugCandidate[];
  const uniqueDrugNames = [...new Set(rlCandidates.map(c => c.drugName))];

  const toggleDrug = (id: string) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : prev.length < 4 ? [...prev, id] : prev);
  };

  return (
    <FadeIn>
      <PageHeader title="Drug Comparison" description="Compare up to 4 drug candidates side-by-side" />
      <Card className="mb-6">
        <CardContent className="p-4">
          <p className="text-sm font-medium mb-2">Select drugs to compare ({selectedIds.length}/4):</p>
          {rlLoading ? <LoadingSpinner label="Loading RL candidates..." /> :
           rlCandidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">No drug candidates loaded. The RL ranker returned no candidates. Deploy the RL service to populate this screen.</p>
          ) : (
            // FE-055 ROOT FIX (TM13): removed the arbitrary `slice(0, 13)`
            // magic number. All candidates are shown in a scrollable
            // container so none are silently hidden; the compare limit
            // (4) is enforced by toggleDrug() above.
            <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto">
              {rlCandidates.map(c => (
                <Badge key={c.id} variant={selectedIds.includes(c.id) ? 'default' : 'outline'} className="cursor-pointer" onClick={() => toggleDrug(c.id)}>{c.drugName}</Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
      {compared.length > 1 && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <Table>
              <TableHeader><TableRow className="bg-muted/50"><TableHead>Metric</TableHead>{compared.map(c => <TableHead key={c.id} className="text-center">{c.drugName}</TableHead>)}</TableRow></TableHeader>
              <TableBody>
                {[
                  { label: 'Composite Score', key: 'compositeScore' },
                  { label: 'KG Score', key: 'kgScore' },
                  { label: 'Mol Similarity', key: 'molSimScore' },
                  { label: 'Safety Score', key: 'safetyScore' },
                  { label: 'Clinical Score', key: 'clinicalScore' },
                ].map(row => (
                  <TableRow key={row.key}>
                    <TableCell className="font-medium text-sm">{row.label}</TableCell>
                    {compared.map(c => {
                      const val = (c as unknown as Record<string, unknown>)[row.key] as number;
                      const max = Math.max(...compared.map(x => (x as unknown as Record<string, unknown>)[row.key] as number));
                      return (
                        <TableCell key={c.id} className="text-center">
                          <span className={`font-bold ${val === max ? 'text-emerald-600' : ''}`}>{val}</span>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell className="font-medium text-sm">Safety Tier</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center"><SafetyBadge tier={c.safetyTier} /></TableCell>)}
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-sm">Phase</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center"><Badge variant="outline" className="text-xs">{c.clinicalPhase}</Badge></TableCell>)}
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium text-sm">IP Status</TableCell>
                  {compared.map(c => <TableCell key={c.id} className="text-center text-xs">{c.ipStatus ?? 'N/A'}</TableCell>)}
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </FadeIn>
  );
}

function DrugInteractionScreen() {
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

function MolecularSimilarityScreen() {
  return (
    <FadeIn>
      <PageHeader title="Molecular Similarity Search" description="Find drugs with similar molecular structures" />
      <Card>
        <CardContent className="py-16 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100">
            <Atom className="h-6 w-6 text-amber-700" />
          </div>
          <h3 className="text-base font-semibold text-foreground mb-2">Molecular similarity service not deployed</h3>
          <p className="text-sm text-muted-foreground max-w-lg mx-auto mb-4">
            Molecular similarity requires a Tanimoto/ECFP computation service (RDKit) that is not yet deployed.
            This screen will populate automatically once the RDKit similarity service is live at
            <code className="bg-muted px-1 rounded mx-1">POST /api/similarity</code>.
          </p>
          <div className="mt-4 text-xs text-amber-700/80 italic">
            Fabricated similarity scores are never shown on this screen — patient safety requires real Tanimoto computations only.
          </div>
        </CardContent>
      </Card>
    </FadeIn>
  );
}

function ScoreBreakdownScreen() {
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

function DiseaseDetailScreen() {
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

interface Shortlist { id: string; name: string; drugs: string[]; created: string; }
const SHORTLISTS_STORAGE_KEY = 'drugos:shortlists-v2';
function useShortlists() {
  const [shortlists, setShortlists] = useState<Shortlist[]>(() => {
    if (typeof window === 'undefined') return [];
    try { const raw = window.localStorage.getItem(SHORTLISTS_STORAGE_KEY); return raw ? (JSON.parse(raw) as Shortlist[]) : []; } catch { return []; }
  });
  useEffect(() => { if (typeof window === 'undefined') return; try { window.localStorage.setItem(SHORTLISTS_STORAGE_KEY, JSON.stringify(shortlists)); } catch {} }, [shortlists]);
  const createShortlist = useCallback((name: string) => { const sl: Shortlist = { id: `sl-${Date.now()}`, name: name || `Shortlist ${Date.now()}`, drugs: [], created: new Date().toISOString().slice(0, 10) }; setShortlists(prev => [...prev, sl]); return sl.id; }, []);
  const deleteShortlist = useCallback((id: string) => { setShortlists(prev => prev.filter(sl => sl.id !== id)); }, []);
  return { shortlists, createShortlist, deleteShortlist };
}

function ShortlistsScreen() {
  const { shortlists, createShortlist, deleteShortlist } = useShortlists();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const { navigate } = useDrugOSNav();
  if (shortlists.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Shortlists & Collections" description="Manage your candidate shortlists" actions={<Button style={{ backgroundColor: PRIMARY }}><Plus className="h-4 w-4 mr-2" />New Shortlist</Button>} />
        <EmptyDataState title="No shortlists yet" hint="Create a shortlist from the search results to save and compare candidate collections." />
      </FadeIn>
    );
  }
  return (
    <FadeIn>
      <PageHeader title="Shortlists & Collections" description="Manage your candidate shortlists" actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setShowCreate(s => !s)}><Plus className="h-4 w-4 mr-2" />New Shortlist</Button>} />
      {showCreate && (
        <Card className="mb-4">
          <CardContent className="p-4 flex items-center gap-2">
            <Input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Shortlist name (e.g. HD Top Picks)" className="flex-1" />
            <Button style={{ backgroundColor: PRIMARY }} onClick={() => { if (newName.trim()) { createShortlist(newName.trim()); setNewName(''); setShowCreate(false); } }}>Create</Button>
            <Button variant="outline" onClick={() => { setShowCreate(false); setNewName(''); }}>Cancel</Button>
          </CardContent>
        </Card>
      )}
      {shortlists.length === 0 ? (
        <EmptyState title="No shortlists yet" description="Create a shortlist to save and organize drug candidates for review. Shortlists are persisted to your browser's local storage." />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {shortlists.map(sl => (
            <Card key={sl.id}>
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="text-base">{sl.name}</CardTitle>
                    <CardDescription>{sl.drugs.length} drugs · Created {sl.created}</CardDescription>
                  </div>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => deleteShortlist(sl.id)} title="Delete shortlist"><Trash2 className="h-3.5 w-3.5 text-muted-foreground" /></Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {sl.drugs.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-2">No drugs in this shortlist yet.</p>
                ) : sl.drugs.map(d => {
                  const cand = drugCandidates.find(c => c.drugName === d);
                  return (
                    <div key={d} className="flex items-center justify-between p-2 rounded-lg hover:bg-accent cursor-pointer" onClick={() => cand && navigate({ page: 'app', section: 'candidate', id: cand.id })}>
                      <span className="text-sm">{d}</span>
                      {cand && <ScoreBar score={cand.compositeScore} size="sm" />}
                    </div>
                  );
                })}
                <Button variant="outline" size="sm" className="w-full mt-2" onClick={() => navigate({ page: 'app', section: 'comparison' })}><BarChart3 className="h-4 w-4 mr-1.5" />Compare</Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </FadeIn>
  );
}

function QueryHistoryScreen() {
  const { navigate } = useDrugOSNav();
  // FE-054 ROOT FIX (TM13): recentQueries is empty until a query-history
  // API is wired. Show an honest empty state instead of a blank table.
  if (recentQueries.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Query History" description="Your past search history" />
        <EmptyDataState title="No queries yet" hint="Your past disease searches will appear here so you can re-run them." />
      </FadeIn>
    );
  }
  return (
    <FadeIn>
      <PageHeader title="Query History" description="Your past search history" />
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow className="bg-muted/50"><TableHead>Date</TableHead><TableHead>Disease</TableHead><TableHead>Candidates</TableHead><TableHead>Top Score</TableHead><TableHead></TableHead></TableRow></TableHeader>
            <TableBody>
              {recentQueries.map(q => {
                const disease = diseases.find(d => d.name === q.disease);
                return (
                  <TableRow key={q.id}>
                    <TableCell className="text-sm text-muted-foreground">{q.date}</TableCell>
                    <TableCell className="font-medium">{q.disease}</TableCell>
                    <TableCell><Badge variant="secondary">{q.candidates}</Badge></TableCell>
                    <TableCell><span className="font-bold" style={{ color: scoreColor(q.topScore) }}>{q.topScore}</span></TableCell>
                    <TableCell><Button variant="ghost" size="sm" onClick={() => disease && navigate({ page: 'app', section: 'results', id: disease.id })}>Re-run</Button></TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </FadeIn>
  );
}

function BatchQueryScreen() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState<{ disease: string; count: number; topScore: number; error?: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);

  const handleRun = async () => {
    const lines = input.split('\n').map(l => l.trim()).filter(Boolean);
    if (lines.length === 0) return;
    setRunning(true); setBatchError(null);
    setResults(lines.map(d => ({ disease: d, count: -1, topScore: 0 })));
    try {
      const settled = await Promise.allSettled(lines.map(disease => fetch('/api/rl', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ disease, limit: 50 }) }).then(async res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); const body = await res.json(); const cands: any[] = body?.candidates || []; const topScore = cands.length > 0 ? Math.round(Math.max(...cands.map((c: any) => c.overallScore || 0)) * 100) : 0; return { disease, count: cands.length, topScore }; })));
      setResults(settled.map((s, i) => s.status === 'fulfilled' ? { disease: lines[i], count: s.value.count, topScore: s.value.topScore } : { disease: lines[i], count: 0, topScore: 0, error: (s.reason as Error)?.message || 'failed' }));
    } catch (e: any) { setBatchError(e?.message || 'Batch query failed'); } finally { setRunning(false); }
  };

  return (
    <FadeIn>
      <PageHeader title="Batch Query Mode" description="Run queries for multiple diseases at once (real /api/rl ranker)" />
      <Card className="mb-6">
        <CardContent className="p-6 space-y-4">
          <label className="text-sm font-medium">Enter diseases (one per line):</label>
          <textarea value={input} onChange={e => setInput(e.target.value)} placeholder={"Huntington's Disease\nAlzheimer's Disease\nPancreatic Cancer"} className="w-full h-32 px-3 py-2 border rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary/20" />
          <Button style={{ backgroundColor: PRIMARY }} onClick={handleRun} disabled={running || !input.trim()}>
            {running ? <RefreshCw className="h-4 w-4 mr-2 animate-spin" /> : <Play className="h-4 w-4 mr-2" />}
            {running ? 'Running...' : 'Run Batch Query'}
          </Button>
          {batchError && (
            <div className="text-xs text-red-600 p-2 border border-red-200 rounded">{batchError}</div>
          )}
        </CardContent>
      </Card>
      {results.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow className="bg-muted/50"><TableHead>Disease</TableHead><TableHead>Candidates</TableHead><TableHead>Top Score</TableHead></TableRow></TableHeader>
              <TableBody>
                {results.map((r, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium">{r.disease}</TableCell>
                    <TableCell>
                      {r.count === -1 ? (
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><RefreshCw className="h-3 w-3 animate-spin" />Querying...</span>
                      ) : r.error ? (
                        <span className="text-xs text-red-600" title={r.error}>failed</span>
                      ) : (
                        r.count
                      )}
                    </TableCell>
                    <TableCell><span className="font-bold" style={{ color: scoreColor(r.topScore) }}>{r.topScore || 'N/A'}</span></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </FadeIn>
  );
}

function PredictionExplorerScreen() {
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

function EvidenceTimelineScreen() {
  const [query, setQuery] = useState('');
  const { data: literatureData, loading, error } = useLiteratureSearch(query, 3);
  const evidence = useMemo(() => {
    const items = (literatureData?.items || []).map((a: any, i: number) => ({ id: a.pmid || `lit-${i}`, drugName: '', type: 'literature', title: a.title || 'Untitled', source: 'PubMed', quality: undefined as unknown as number, year: a.pubDate ? Number(String(a.pubDate).slice(0, 4)) : undefined as unknown as number, summary: a.abstract || '' }));
    return [...items].sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
  }, [literatureData]);
  return (
    <FadeIn>
      <PageHeader title="Evidence Timeline" description="Timeline of evidence for drug-disease pairs (real PubMed literature)" />
      <div className="mb-6">
        <div className="relative max-w-xl">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search PubMed literature (e.g. 'memantine Huntington')..." className="pl-9" />
        </div>
      </div>
      {loading && <LoadingSpinner label="Searching PubMed..." />}
      {error && <ErrorDisplay error={error} />}
      {!loading && !error && query.trim().length >= 3 && evidence.length === 0 && (
        <EmptyState title="No literature found" description={`No PubMed articles match "${query}". Try a different query.`} />
      )}
      {!loading && !error && query.trim().length < 3 && (
        <EmptyState title="Enter a search query" description="Type at least 3 characters to search PubMed for published evidence on drug-disease pairs." />
      )}
      {!loading && !error && evidence.length > 0 && (
        <div className="relative">
          <div className="absolute left-6 top-0 bottom-0 w-0.5 bg-border" />
          <div className="space-y-6">
            {evidence.map((ev, i) => (
              <div key={ev.id} className="relative pl-14">
                <div className="absolute left-4 w-5 h-5 rounded-full border-2 bg-background" style={{ borderColor: ev.type === 'clinical' ? ACCENT_GREEN : ev.type === 'preclinical' ? PRIMARY : ACCENT_ORANGE }} />
                <Card><CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1"><Badge variant="secondary" className="text-[10px]">{ev.type}</Badge><span className="text-xs text-muted-foreground">{ev.year ?? 0}</span><span className="font-medium text-sm">{ev.drugName}</span></div>
                  <p className="text-sm font-medium">{ev.title}</p>
                  <p className="text-xs text-muted-foreground mt-1">{ev.source} · Quality: {ev.quality}</p>
                </CardContent></Card>
              </div>
            ))}
          </div>
        </div>
      )}
    </FadeIn>
  );
}

function MechanismOfActionScreen() {
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

function RegulatoryPathwayScreen() {
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

// ═══════════════════════════════════════════
// EXPORT
// ═══════════════════════════════════════════

export const coreScreens: Record<string, React.ComponentType> = {
  'search': DiseaseSearchScreen,
  'results': SearchResultsScreen,
  'candidate': CandidateDetailScreen,
  'knowledge-graph': KnowledgeGraphScreen,
  'clinical-trials': ClinicalTrialsScreen,
  'safety': SafetyProfileScreen,
  'ip-patents': IPPatentsScreen,
  'evidence-builder': EvidenceBuilderScreen,
  'reports': ReportGenerationScreen,
  'advanced-search': AdvancedSearchScreen,
  'saved-queries': SavedQueriesScreen,
  'comparison': DrugComparisonScreen,
  'interactions': DrugInteractionScreen,
  'molecular-similarity': MolecularSimilarityScreen,
  'score-breakdown': ScoreBreakdownScreen,
  'disease-detail': DiseaseDetailScreen,
  'shortlists': ShortlistsScreen,
  'history': QueryHistoryScreen,
  'batch-query': BatchQueryScreen,
  'prediction-explorer': PredictionExplorerScreen,
  'evidence-timeline': EvidenceTimelineScreen,
  'mechanism': MechanismOfActionScreen,
  'regulatory': RegulatoryPathwayScreen,
  ...remainingScreens,
};
