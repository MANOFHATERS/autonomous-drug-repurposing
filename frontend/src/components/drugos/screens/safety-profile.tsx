'use client';

import { useState, useMemo } from 'react';
import {
  Search, ShieldCheck, AlertTriangle, AlertCircle,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  useDrugSafety, useDrugSearch,
  LoadingSpinner, ErrorDisplay,
} from '../use-api-data';
import { SafetyBadge } from '../safety-badge';
import {
  drugCandidates, admetProfiles, offTargetPredictions, drugInteractions,
} from '@/lib/empty-defaults';
import {
  ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,
  StatCard, PageHeader, FadeIn,
} from './_core-shared';
import { ADMETRadarChart } from './charts';

export function SafetyProfileScreen() {
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
