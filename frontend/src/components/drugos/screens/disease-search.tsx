'use client';

import { useState, useMemo } from 'react';
import {
  Search, RefreshCw, ChevronRight, Clock, TrendingUp, Filter, ChevronDown,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Progress } from '@/components/ui/progress';
import { useDrugOSNav } from '../nav-context';
import { useDiseaseSearch } from '../use-api-data';
import { diseases, recentQueries, usageMetrics } from '@/lib/empty-defaults';
import { trendingDiseases } from '@/lib/static-content';
import { PRIMARY, scoreColor, FadeIn } from './_core-shared';

export function DiseaseSearchScreen() {
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
