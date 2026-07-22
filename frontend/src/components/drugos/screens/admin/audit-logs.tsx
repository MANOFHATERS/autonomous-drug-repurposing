'use client';

import { useState, useEffect } from 'react';
import { api, type AuditLog } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Download } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 17. AUDIT LOGS SCREEN — real audit logs from /api/audit-logs
// ═══════════════════════════════════════════
export function AuditLogsScreen() {
  const [filter, setFilter] = useState('all');
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listAuditLogs(200, 0).then(r => {
      if (mounted) { setLogs(r.items); setLoading(false); }
    }).catch(e => {
      if (mounted) { setErr(e?.message || 'Failed to load audit logs.'); setLoading(false); }
    });
    return () => { mounted = false };
  }, []);

  const actionTypes = [...new Set(logs.map(l => l.action.split(/[_\.]/)[0]))];
  const filtered = filter === 'all' ? logs : logs.filter(l => l.action.startsWith(filter));

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Audit Logs" desc="Track all platform activity" actions={<Button variant="outline" size="sm" onClick={() => { const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), count: logs.length, items: logs }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `drugos-audit-logs-${new Date().toISOString().slice(0, 10)}.json`; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url); }} disabled={logs.length === 0}><Download className="h-4 w-4 mr-1.5" />Export</Button>} />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Badge variant={filter === 'all' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('all')}>All</Badge>
        {actionTypes.map(t => <Badge key={t} variant={filter === t ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter(t)}>{t}</Badge>)}
      </div>
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading audit logs…</p>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No audit log entries.</p>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Timestamp</TableHead><TableHead>User</TableHead><TableHead>Action</TableHead>
              <TableHead>Resource</TableHead><TableHead>IP Address</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {filtered.map(l => (
                <TableRow key={l.id}>
                  <TableCell className="font-mono text-xs">{new Date(l.createdAt).toLocaleString()}</TableCell>
                  <TableCell className="text-sm">{l.actorName}</TableCell>
                  <TableCell><Badge variant="outline" className="text-xs font-mono">{l.action}</Badge></TableCell>
                  <TableCell className="text-sm">{l.resource || '—'}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{l.ip || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
    </div></FadeIn>
  );
}
