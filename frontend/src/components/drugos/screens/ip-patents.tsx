'use client';

import { useState, useMemo } from 'react';
import {
  Scale, Clock, FileText, AlertCircle, RefreshCw,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useApiList, ErrorDisplay } from '../use-api-data';
import { api } from '@/lib/api-client';
import { drugCandidates } from '@/lib/empty-defaults';
import type { Patent } from '@/lib/types';
import {
  ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,
  scoreColor, StatCard, PageHeader, FadeIn,
} from './_core-shared';
import { PatentTimeline } from './charts';

export function IPPatentsScreen() {
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
