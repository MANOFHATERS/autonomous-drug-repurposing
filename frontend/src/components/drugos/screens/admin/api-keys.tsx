'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Plus, Key, Copy, Trash2 } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 20. API KEYS SCREEN — real API keys from /api/api-keys
// ═══════════════════════════════════════════
export function APIKeysScreen() {
  const [createOpen, setCreateOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [keys, setKeys] = useState<Array<{ id: string; name: string; prefix: string; lastUsedAt: string | null; revokedAt: string | null; createdAt: string; rawKey?: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newlyCreatedKey, setNewlyCreatedKey] = useState<string | null>(null);

  const loadKeys = () => {
    setLoading(true);
    api.listApiKeys().then(r => {
      setKeys(r.items);
      setLoading(false);
    }).catch(e => {
      setErr(e?.message || 'Failed to load API keys.');
      setLoading(false);
    });
  };

  useEffect(() => { loadKeys(); }, []);

  const handleCreate = async () => {
    if (!newKeyName.trim()) return;
    setCreating(true); setErr(null);
    try {
      const created = await api.createApiKey(newKeyName.trim());
      setNewlyCreatedKey(created.rawKey || null);
      setNewKeyName('');
      setCreateOpen(false);
      loadKeys();
    } catch (e: any) {
      setErr(e?.message || 'Failed to create API key.');
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    if (!confirm('Revoke this API key? This cannot be undone.')) return;
    try {
      await api.revokeApiKey(id);
      loadKeys();
    } catch (e: any) {
      setErr(e?.message || 'Failed to revoke key.');
    }
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="API Keys" desc="Manage your API authentication keys" actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Create Key</Button>} />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      {newlyCreatedKey && (
        <Card className="border-emerald-300 bg-emerald-50 dark:bg-emerald-950/30 dark:border-emerald-800">
          <CardContent className="p-4">
            <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300 mb-2">Your new API key — copy it now, you won't see it again:</p>
            <div className="flex items-center gap-2">
              <code className="font-mono text-xs bg-white dark:bg-slate-900 p-2 rounded flex-1 break-all">{newlyCreatedKey}</code>
              <Button variant="outline" size="sm" onClick={() => { navigator.clipboard.writeText(newlyCreatedKey); }}><Copy className="h-3 w-3 mr-1" />Copy</Button>
              <Button variant="outline" size="sm" onClick={() => setNewlyCreatedKey(null)}>Dismiss</Button>
            </div>
          </CardContent>
        </Card>
      )}
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading API keys…</p>
        ) : keys.length === 0 ? (
          <div className="p-8 text-center">
            <Key className="h-10 w-10 text-muted-foreground/50 mx-auto mb-3" />
            <p className="text-sm font-medium">No API keys yet</p>
            <p className="text-xs text-muted-foreground mt-1">Create an API key to start using the DrugOS API.</p>
          </div>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Name</TableHead><TableHead>Key Prefix</TableHead><TableHead>Created</TableHead>
              <TableHead>Last Used</TableHead><TableHead>Status</TableHead><TableHead></TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {keys.map(k => (
                <TableRow key={k.id}>
                  <TableCell className="font-medium">{k.name}</TableCell>
                  <TableCell className="font-mono text-xs">drugos_{k.prefix}…</TableCell>
                  <TableCell className="text-sm">{new Date(k.createdAt).toLocaleDateString()}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleString() : 'Never'}</TableCell>
                  <TableCell><Badge variant={k.revokedAt ? 'destructive' : 'default'}>{k.revokedAt ? 'revoked' : 'active'}</Badge></TableCell>
                  <TableCell>
                    {!k.revokedAt && (
                      <Button variant="ghost" size="sm" className="h-7 text-red-500" onClick={() => handleRevoke(k.id)}>
                        <Trash2 className="h-3 w-3 mr-1" />Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create New API Key</DialogTitle>
            <DialogDescription>Generate a new API key for programmatic access. The full key will only be shown once.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div><Label>Key Name</Label><Input placeholder="e.g. Production Integration" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button style={{ backgroundColor: PRIMARY }} onClick={handleCreate} disabled={creating || !newKeyName.trim()}>{creating ? 'Creating…' : 'Create Key'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div></FadeIn>
  );
}
