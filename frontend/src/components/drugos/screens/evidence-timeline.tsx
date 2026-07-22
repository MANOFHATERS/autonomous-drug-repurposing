'use client';

import { useState, useMemo } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import {
  useLiteratureSearch, LoadingSpinner, ErrorDisplay, EmptyState,
} from '../use-api-data';
import {
  ACCENT_GREEN, ACCENT_ORANGE, PRIMARY,
  PageHeader, FadeIn,
} from './_core-shared';

export function EvidenceTimelineScreen() {
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
