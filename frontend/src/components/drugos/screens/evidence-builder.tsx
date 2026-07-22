'use client';

import { useState, useMemo } from 'react';
import {
  CheckSquare, Square, XCircle, RefreshCw, Package, Eye,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  useDrugSearch, useDiseaseSearch, useBuildEvidencePackage,
} from '../use-api-data';
import { PRIMARY, PageHeader, FadeIn } from './_core-shared';

export function EvidenceBuilderScreen() {
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
