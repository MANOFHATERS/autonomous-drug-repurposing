'use client';

import { useState, useEffect } from 'react';
import { api, type Invoice } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { FileText } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 13. INVOICES SCREEN — real invoices from /api/billing/invoices
// ═══════════════════════════════════════════
export function InvoicesScreen() {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listInvoices().then(r => {
      if (mounted) { setInvoices(r.items); setLoading(false); }
    }).catch(e => {
      if (mounted) { setErr(e?.message || 'Failed to load invoices.'); setLoading(false); }
    });
    return () => { mounted = false };
  }, []);

  const statusColor = (status: string) => {
    if (status === 'paid') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
    if (status === 'open') return 'bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300';
    if (status === 'void' || status === 'uncollectible') return 'bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300';
    return 'bg-muted text-muted-foreground';
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Invoices" desc="Billing history and invoice management" />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading invoices…</p>
        ) : invoices.length === 0 ? (
          <div className="p-8 text-center">
            <FileText className="h-10 w-10 text-muted-foreground/50 mx-auto mb-3" />
            <p className="text-sm font-medium">No invoices yet</p>
            <p className="text-xs text-muted-foreground mt-1">Invoices will appear here once you upgrade to a paid plan.</p>
          </div>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Invoice #</TableHead><TableHead>Date</TableHead><TableHead>Period</TableHead>
              <TableHead>Amount</TableHead><TableHead>Status</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {invoices.map(inv => (
                <TableRow key={inv.id}>
                  <TableCell className="font-mono text-sm">{inv.number}</TableCell>
                  <TableCell>{new Date(inv.createdAt).toLocaleDateString()}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{new Date(inv.periodStart).toLocaleDateString()} → {new Date(inv.periodEnd).toLocaleDateString()}</TableCell>
                  <TableCell className="font-semibold">${(inv.amountCents / 100).toFixed(2)} {inv.currency.toUpperCase()}</TableCell>
                  <TableCell><Badge className={statusColor(inv.status)}>{inv.status}</Badge></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
    </div></FadeIn>
  );
}
