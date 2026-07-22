'use client';

import { useState, useEffect } from 'react';
import { useSession } from '../../session-provider';
import { api } from '@/lib/api-client';
import { roleLabel } from '@/lib/rbac';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Check } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 23. PROFILE SCREEN — real backend data via useSession + PATCH /api/auth/me
// ═══════════════════════════════════════════
export function ProfileScreen() {
  const { user, refresh, organizations, activeOrganizationId } = useSession();
  const [name, setName] = useState('');
  const [title, setTitle] = useState('');
  const [bio, setBio] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (user) {
      setName(user.name || '');
      setTitle(user.title || '');
      setBio(user.bio || '');
    }
  }, [user?.id, user?.name, user?.title, user?.bio]);

  const handleSave = async () => {
    setSaving(true);
    setSavedMsg(null);
    setErrorMsg(null);
    try {
      await api.updateMe({ name, title, bio });
      await refresh();
      setSavedMsg('Profile updated successfully.');
    } catch (e: any) {
      setErrorMsg(e?.message || 'Failed to update profile.');
    } finally {
      setSaving(false);
    }
  };

  if (!user) {
    return <FadeIn><div className="p-8 text-center text-muted-foreground">Loading profile…</div></FadeIn>;
  }

  const initials = (user.name || user.email || '?')
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase()).join('') || user.email[0]?.toUpperCase();
  const activeOrg = organizations.find(o => o.id === activeOrganizationId) || organizations[0];

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Profile" desc="Manage your personal information" />
      {savedMsg && (
        <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{savedMsg}</div>
      )}
      {errorMsg && (
        <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{errorMsg}</div>
      )}
      <Card><CardContent className="p-6">
        <div className="flex items-center gap-6 mb-6">
          <Avatar className="h-20 w-20"><AvatarFallback className="bg-primary text-white text-2xl">{initials}</AvatarFallback></Avatar>
          <div>
            <h3 className="text-lg font-semibold text-foreground">{user.name || user.email}</h3>
            <p className="text-sm text-muted-foreground">{user.email}</p>
            <div className="flex items-center gap-2 mt-2">
              <Badge variant="secondary">{roleLabel(user.role)}</Badge>
              {activeOrg && <Badge variant="outline">{activeOrg.name} · {activeOrg.plan}</Badge>}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div><Label>Full Name</Label><Input value={name} onChange={e => setName(e.target.value)} disabled={saving} /></div>
          <div><Label>Email</Label><Input value={user.email} type="email" disabled /></div>
          <div><Label>Title</Label><Input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Principal Scientist" disabled={saving} /></div>
          <div><Label>Role</Label><Input value={roleLabel(user.role)} disabled /></div>
          <div className="sm:col-span-2"><Label>Bio</Label><Textarea value={bio} onChange={e => setBio(e.target.value)} placeholder="Tell us about your research focus" rows={3} disabled={saving} /></div>
          <div><Label>Member since</Label><Input value={user.createdAt ? new Date(user.createdAt).toLocaleDateString() : ''} disabled /></div>
          <div><Label>Last login</Label><Input value={user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString() : '—'} disabled /></div>
        </div>
        <div className="flex justify-end mt-6">
          <Button onClick={handleSave} disabled={saving} style={{ backgroundColor: PRIMARY }}>
            <Check className="h-4 w-4 mr-1.5" />{saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </div>
      </CardContent></Card>
    </div></FadeIn>
  );
}
