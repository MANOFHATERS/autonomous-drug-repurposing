'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Plus, Search, MoreHorizontal, Check, X } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 14. USERS ADMIN SCREEN — real user data from /api/admin/users
// ═══════════════════════════════════════════
export function UsersAdminScreen() {
  const [search, setSearch] = useState('');
  const [inviteOpen, setInviteOpen] = useState(false);
  const [adminUsers, setAdminUsers] = useState<Array<{ id: string; email: string; name: string | null; role: string; status: string; emailVerified: boolean; createdAt: string; lastLoginAt: string | null }>>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [updatingRole, setUpdatingRole] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listUsers(100, 0).then(r => {
      if (mounted) { setAdminUsers(r.items); setLoading(false); }
    }).catch(e => {
      if (mounted) { setErr(e?.message || 'Failed to load users.'); setLoading(false); }
    });
    return () => { mounted = false };
  }, []);

  const filtered = adminUsers.filter(u =>
    (u.name || '').toLowerCase().includes(search.toLowerCase()) ||
    u.email.toLowerCase().includes(search.toLowerCase())
  );

  const handleRoleChange = async (userId: string, newRole: string) => {
    setUpdatingRole(userId);
    try {
      const updated = await api.updateUser({ userId, role: newRole });
      setAdminUsers(prev => prev.map(u => u.id === userId ? updated : u));
    } catch (e: any) {
      setErr(e?.message || 'Failed to update user role.');
    } finally {
      setUpdatingRole(null);
    }
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader
        title="User Management"
        desc={loading ? 'Loading users…' : `${adminUsers.length} user${adminUsers.length === 1 ? '' : 's'} registered`}
        actions={<>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Search users..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 w-56" />
          </div>
          <Button style={{ backgroundColor: PRIMARY }} onClick={() => setInviteOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Add User</Button>
        </>}
      />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading users…</p>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No users found.</p>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>User</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead>
              <TableHead>Email Verified</TableHead><TableHead>Last Active</TableHead><TableHead>Joined</TableHead><TableHead></TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {filtered.map(u => {
                const initials = (u.name || u.email || '?').split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s: string) => s[0]?.toUpperCase()).join('') || '?';
                return (
                  <TableRow key={u.id}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <Avatar className="h-8 w-8"><AvatarFallback className="bg-primary/10 text-primary text-xs">{initials}</AvatarFallback></Avatar>
                        <div>
                          <p className="font-medium text-sm">{u.name || '(no name)'}</p>
                          <p className="text-xs text-muted-foreground">{u.email}</p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Select defaultValue={u.role} onValueChange={(v) => handleRoleChange(u.id, v)} disabled={updatingRole === u.id}>
                        <SelectTrigger className="h-7 w-36 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="admin">Admin</SelectItem>
                          <SelectItem value="owner">Owner</SelectItem>
                          <SelectItem value="researcher">Researcher</SelectItem>
                          <SelectItem value="data_scientist">Data Scientist</SelectItem>
                          <SelectItem value="pi">Principal Investigator</SelectItem>
                          <SelectItem value="business_dev">Business Dev</SelectItem>
                          <SelectItem value="developer">Developer</SelectItem>
                          <SelectItem value="viewer">Viewer</SelectItem>
                          <SelectItem value="billing">Billing</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell><Badge variant={u.status === 'active' ? 'default' : u.status === 'suspended' ? 'destructive' : 'secondary'}>{u.status}</Badge></TableCell>
                    <TableCell>{u.emailVerified ? <Check className="h-4 w-4 text-emerald-500" /> : <X className="h-4 w-4 text-muted-foreground/40" />}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : 'Never'}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{new Date(u.createdAt).toLocaleDateString()}</TableCell>
                    <TableCell><Button variant="ghost" size="sm" className="h-7 w-7 p-0"><MoreHorizontal className="h-4 w-4" /></Button></TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Invite New User</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Invite colleagues by email. They'll receive a sign-up link valid for 7 days.</p>
            <div><Label>Email Address</Label><Input placeholder="colleague@company.com" /></div>
            <div><Label>Role</Label>
              <Select defaultValue="researcher">
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="researcher">Researcher</SelectItem>
                  <SelectItem value="data_scientist">Data Scientist</SelectItem>
                  <SelectItem value="viewer">Viewer</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setInviteOpen(false)}>Cancel</Button>
            <Button style={{ backgroundColor: PRIMARY }} onClick={() => setInviteOpen(false)}>Send Invite</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div></FadeIn>
  );
}
