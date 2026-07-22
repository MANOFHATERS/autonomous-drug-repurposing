'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { BookOpen } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 19. API DOCS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-029 / FE-037: REAL_ENDPOINTS is a MANUALLY-CURATED list of the real
 * Next.js App Router API endpoints under /api/. It is NOT auto-generated
 * from the filesystem (impossible client-side). When new routes are added,
 * update this list by hand, OR wire an OpenAPI spec generator.
 */
const REAL_ENDPOINTS = [
  { id: 'disease-search', method: 'GET' as const, path: '/api/diseases/search?q={query}&limit={n}', desc: 'Search diseases via NLM MeSH' },
  { id: 'drug-search', method: 'GET' as const, path: '/api/drugs/search?q={query}', desc: 'Search drugs via RxNorm' },
  { id: 'drug-safety', method: 'GET' as const, path: '/api/safety/{drugName}', desc: 'FDA adverse event data (openFDA)' },
  { id: 'clinical-trials', method: 'GET' as const, path: '/api/clinical-trials/search?condition={c}&intervention={i}', desc: 'ClinicalTrials.gov search' },
  { id: 'literature', method: 'GET' as const, path: '/api/literature/search?q={query}', desc: 'PubMed literature search' },
  { id: 'kg-stats', method: 'GET' as const, path: '/api/knowledge-graph', desc: 'Knowledge graph statistics' },
  { id: 'kg-query', method: 'GET' as const, path: '/api/knowledge-graph?drug={drug}&disease={disease}', desc: 'Knowledge graph subgraph query' },
  { id: 'evidence-package', method: 'POST' as const, path: '/api/evidence-package', desc: 'Build an evidence package' },
  { id: 'rl-rank', method: 'GET' as const, path: '/api/rl?drug={d}&disease={d}&limit={n}', desc: 'RL-ranked hypotheses' },
  { id: 'billing-plans', method: 'GET' as const, path: '/api/billing/plans', desc: 'List subscription plans' },
  { id: 'billing-subscription', method: 'GET' as const, path: '/api/billing/subscription', desc: 'Current subscription' },
  { id: 'billing-invoices', method: 'GET' as const, path: '/api/billing/invoices', desc: 'List invoices' },
  { id: 'projects', method: 'GET' as const, path: '/api/projects', desc: 'List projects' },
  { id: 'projects-create', method: 'POST' as const, path: '/api/projects', desc: 'Create a project' },
  { id: 'auth-me', method: 'GET' as const, path: '/api/auth/me', desc: 'Current user' },
  { id: 'admin-users', method: 'GET' as const, path: '/api/admin/users', desc: 'List users (admin)' },
  { id: 'system-status', method: 'GET' as const, path: '/api/system/status', desc: 'System health status' },
];

export function APIDocsScreen() {
  const [activeEndpoint, setActiveEndpoint] = useState('disease-search');
  const activeEp = REAL_ENDPOINTS.find(e => e.id === activeEndpoint) || REAL_ENDPOINTS[0];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="API Documentation" desc="Common DrugOS API endpoints (manually curated)" actions={<Button variant="outline" size="sm" disabled title="No openapi.json is published in this deployment — wire an OpenAPI generator to enable this download."><BookOpen className="h-4 w-4 mr-1.5" />OpenAPI Spec</Button>} />
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="space-y-1 max-h-[600px] overflow-y-auto">{REAL_ENDPOINTS.map(ep => (<button key={ep.id} onClick={() => setActiveEndpoint(ep.id)} className={`w-full text-left p-3 rounded-lg text-sm transition-colors ${activeEndpoint === ep.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-accent'}`}>
          <div className="flex items-center gap-2"><Badge className={`text-[10px] ${ep.method === 'GET' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'}`}>{ep.method}</Badge><span className="font-mono text-xs">{ep.path}</span></div><p className="text-xs text-muted-foreground mt-1">{ep.desc}</p>
        </button>))}</div>
        <div className="lg:col-span-3"><Card><CardHeader><CardTitle className="text-base flex items-center gap-2"><Badge className={activeEp.method === 'GET' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'}>{activeEp.method}</Badge><code className="text-sm">{activeEp.path}</code></CardTitle><CardDescription>{activeEp.desc}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div><h4 className="text-sm font-semibold mb-2">Base URL</h4><p className="text-sm text-muted-foreground">All endpoints are relative to your deployment origin. In development: <code className="bg-muted px-1.5 py-0.5 rounded text-xs">http://localhost:3000</code></p></div>
            <div><h4 className="text-sm font-semibold mb-2">Authentication</h4><p className="text-sm text-muted-foreground">All API requests require authentication via HTTP-only cookies (set on login). API keys can be created at <strong>Settings → API Keys</strong>.</p></div>
            <div><h4 className="text-sm font-semibold mb-2">Response Format</h4><p className="text-sm text-muted-foreground">All endpoints return JSON. List endpoints wrap results in <code className="bg-muted px-1.5 py-0.5 rounded text-xs">{`{ items: [...], total?: number }`}</code>. Errors use <code className="bg-muted px-1.5 py-0.5 rounded text-xs">{`{ error: string, message?: string }`}</code>.</p></div>
          </CardContent></Card></div>
      </div>
    </div></FadeIn>
  );
}
