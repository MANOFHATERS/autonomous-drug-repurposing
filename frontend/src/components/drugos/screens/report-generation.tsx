'use client';

import { useState } from 'react';
import {
  RefreshCw, FileText, BarChart3, BookOpen, Settings,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  useDrugSearch, useDiseaseSearch, useBuildEvidencePackage,
} from '../use-api-data';
import { PRIMARY, PageHeader, FadeIn } from './_core-shared';

export function ReportGenerationScreen() {
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
