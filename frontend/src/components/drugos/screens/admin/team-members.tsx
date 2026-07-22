'use client';

import { useState, useEffect } from 'react';
import { useDrugOSNav } from '../../nav-context';
import { api, type TeamMember } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Plus, Search } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 3. TEAM MEMBERS SCREEN
// ═══════════════════════════════════════════
export function TeamMembersScreen() {
  const { navigate } = useDrugOSNav();
  const [search, setSearch] = useState('');
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listTeamMembers().then(r => {
      if (mounted) { setMembers(r.items); setLoading(false); }
    }).catch(e => {
      if (mounted) { setErr(e?.message || 'Failed to load team members.'); setLoading(false); }
    });
    return () => { mounted = false };
  }, []);

  const filtered = members.filter(m =>
    (m.name || '').toLowerCase().includes(search.toLowerCase()) ||
    m.email.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader
        title="Team Members"
        desc={loading ? 'Loading members…' : `${members.length} member${members.length === 1 ? '' : 's'} in your organization`}
        actions={<>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Search members..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 w-56" />
          </div>
          <Button style={{ backgroundColor: PRIMARY }} onClick={() => setInviteOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Invite Member</Button>
        </>}
      />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading team members…</p>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No team members found.</p>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Member</TableHead><TableHead>Workspace Role</TableHead><TableHead>Account Role</TableHead>
              <TableHead>Status</TableHead><TableHead>Last Active</TableHead><TableHead>Joined</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {filtered.map(m => {
                const initials = (m.name || m.email || '?').split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s: string) => s[0]?.toUpperCase()).join('') || '?';
                return (
                  <TableRow key={m.id}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <Avatar className="h-8 w-8"><AvatarFallback className="bg-primary/10 text-primary text-xs">{initials}</AvatarFallback></Avatar>
                        <div>
                          <p className="font-medium text-sm">{m.name || '(no name)'}</p>
                          <p className="text-xs text-muted-foreground">{m.email}</p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell><Badge variant="outline" className="capitalize">{m.orgRole}</Badge></TableCell>
                    <TableCell><Badge variant="secondary" className="capitalize">{m.role.replace(/-/g, ' ')}</Badge></TableCell>
                    <TableCell><Badge variant={m.status === 'active' ? 'default' : 'outline'}>{m.status}</Badge></TableCell>
                    <TableCell className="text-sm text-muted-foreground">{m.lastLoginAt ? new Date(m.lastLoginAt).toLocaleString() : 'Never'}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{new Date(m.joinedAt).toLocaleDateString()}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Invite Team Member</DialogTitle>
          <DialogDescription>Send an invitation to join your DrugOS workspace</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div><Label>Email Address</Label><Input placeholder="colleague@company.com" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} /></div>
            <div><Label>Role</Label>
              <Select value={inviteRole} onValueChange={setInviteRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="researcher">Researcher</SelectItem>
                  <SelectItem value="viewer">Viewer</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setInviteOpen(false)}>Cancel</Button>
            <Button style={{ backgroundColor: PRIMARY }} onClick={() => setInviteOpen(false)}>Send Invitation</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div></FadeIn>
  );
}
