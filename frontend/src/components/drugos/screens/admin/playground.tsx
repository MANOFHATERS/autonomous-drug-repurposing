'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { RefreshCw, Play, Code } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 21. PLAYGROUND SCREEN
// ═══════════════════════════════════════════
/**
 * FE-030 ROOT FIX: Wire the "Send" button to actually call the entered
 * endpoint via fetch(). The previous code had:
 *   - A hardcoded fake response with mock drugs ("Memantine 87", etc.)
 *   - A no-op onClick={() => {}} for the Send button
 *   - A hardcoded fake bearer token "sk-prod-xxxx" in the DOM
 *   - A fabricated response badge "200 OK - 142ms"
 *
 * This rewrite uses REAL endpoints from the codebase and actually calls
 * them. The response shows real data from the backend services. The fake
 * bearer token is removed — we use cookie-based auth (HttpOnly cookies
 * are sent automatically by fetch with credentials: "include").
 */
const PLAYGROUND_ENDPOINTS = [
  { label: 'GET /api/diseases/search', value: '/api/diseases/search?q=cancer', method: 'GET' as const },
  { label: 'GET /api/drugs/search', value: '/api/drugs/search?q=aspirin', method: 'GET' as const },
  { label: 'GET /api/safety/{drug}', value: '/api/safety/aspirin', method: 'GET' as const },
  { label: 'GET /api/clinical-trials/search', value: '/api/clinical-trials/search?condition=diabetes', method: 'GET' as const },
  { label: 'GET /api/literature/search', value: '/api/literature/search?q=repurposing', method: 'GET' as const },
  { label: 'GET /api/knowledge-graph', value: '/api/knowledge-graph', method: 'GET' as const },
  { label: 'GET /api/rl', value: '/api/rl', method: 'GET' as const },
  { label: 'GET /api/billing/plans', value: '/api/billing/plans', method: 'GET' as const },
  { label: 'GET /api/system/status', value: '/api/system/status', method: 'GET' as const },
  { label: 'GET /api/projects', value: '/api/projects', method: 'GET' as const },
  { label: 'POST /api/evidence-package', value: '/api/evidence-package', method: 'POST' as const, body: '{\n  "drug": "Aspirin",\n  "disease": "Diabetes Type 2"\n}' },
];

export function PlaygroundScreen() {
  const [endpointPath, setEndpointPath] = useState('/api/diseases/search?q=cancer');
  const [requestBody, setRequestBody] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusCode, setStatusCode] = useState<number | null>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);

  const executeQuery = async () => {
    setLoading(true);
    setResponse('');
    setStatusCode(null);
    setResponseTime(null);
    const start = performance.now();
    try {
      const method = PLAYGROUND_ENDPOINTS.find(e => e.value === endpointPath)?.method || 'GET';
      const init: RequestInit = {
        method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      };
      if (method === 'POST' && requestBody.trim()) {
        init.body = requestBody;
      }
      const res = await fetch(endpointPath, init);
      const text = await res.text();
      setStatusCode(res.status);
      setResponseTime(Math.round(performance.now() - start));
      // Pretty-print JSON if possible
      try {
        setResponse(JSON.stringify(JSON.parse(text), null, 2));
      } catch {
        setResponse(text);
      }
    } catch (e: any) {
      setResponse(`Error: ${e?.message || 'Request failed'}`);
      setStatusCode(0);
    } finally {
      setLoading(false);
    }
  };

  const handleEndpointChange = (value: string) => {
    setEndpointPath(value);
    const ep = PLAYGROUND_ENDPOINTS.find(e => e.value === value);
    if (ep?.body) {
      setRequestBody(ep.body);
    } else {
      setRequestBody('');
    }
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="API Playground" desc="Test real DrugOS API endpoints interactively (calls actual backend)" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Request</CardTitle></CardHeader><CardContent className="space-y-4">
          <div><Label>Endpoint</Label><Select value={endpointPath} onValueChange={handleEndpointChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{PLAYGROUND_ENDPOINTS.map(ep => (<SelectItem key={ep.value} value={ep.value}>{ep.label}</SelectItem>))}</SelectContent></Select></div>
          <div><Label>Headers</Label><div className="bg-muted p-3 rounded-lg text-xs font-mono"><div>Cookie: &lt;HttpOnly session cookie&gt;</div><div>Content-Type: application/json</div><p className="text-[10px] text-muted-foreground mt-1">Auth is cookie-based — no bearer token needed.</p></div></div>
          {PLAYGROUND_ENDPOINTS.find(e => e.value === endpointPath)?.method === 'POST' && (
            <div><Label>Body</Label><Textarea value={requestBody} onChange={e => setRequestBody(e.target.value)} className="font-mono text-xs min-h-[200px]" /></div>
          )}
          <Button className="w-full" style={{ backgroundColor: PRIMARY }} onClick={executeQuery} disabled={loading}>{loading ? <><RefreshCw className="h-4 w-4 mr-1.5 animate-spin" />Executing...</> : <><Play className="h-4 w-4 mr-1.5" />Execute</>}</Button>
        </CardContent></Card>
        <Card><CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-base">Response</CardTitle>{statusCode !== null && <Badge variant={statusCode >= 200 && statusCode < 300 ? 'default' : statusCode >= 400 ? 'destructive' : 'secondary'} className="text-[10px]">{statusCode} {responseTime !== null ? `— ${responseTime}ms` : ''}</Badge>}</div></CardHeader><CardContent>{response ? <pre className="bg-slate-950 text-green-400 p-4 rounded-lg text-xs overflow-x-auto min-h-[300px]">{response}</pre> : <div className="flex items-center justify-center h-[300px] text-muted-foreground"><div className="text-center"><Code className="h-8 w-8 mx-auto mb-2 opacity-30" /><p>Execute a request to see the real response</p></div></div>}</CardContent></Card>
      </div>
    </div></FadeIn>
  );
}
