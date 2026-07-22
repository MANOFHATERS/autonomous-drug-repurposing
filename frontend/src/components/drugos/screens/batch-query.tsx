'use client';

import { useState } from 'react';
import { RefreshCw, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { PRIMARY, scoreColor, PageHeader, FadeIn } from './_core-shared';

export function BatchQueryScreen() {
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
