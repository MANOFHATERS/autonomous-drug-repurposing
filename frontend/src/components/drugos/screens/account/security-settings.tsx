'use client';

import { useState, useEffect } from 'react';
import { useSession } from '../../session-provider';
import { type AuditLog } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { QrCode, Eye, Activity } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 24. SECURITY SETTINGS SCREEN — real 2FA status, real sessions, real password change
// ═══════════════════════════════════════════
export function SecuritySettingsScreen() {
  const { user, refresh } = useSession();
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [pwSaving, setPwSaving] = useState(false);

  const [twoFAOpen, setTwoFAOpen] = useState(false);
  const [twoFASecret, setTwoFASecret] = useState<string>('');
  const [twoFAShowSecret, setTwoFAShowSecret] = useState(false);
  const [twoFACode, setTwoFACode] = useState('');
  const [twoFAMsg, setTwoFAMsg] = useState<string | null>(null);
  const [twoFAErr, setTwoFAErr] = useState<string | null>(null);
  const [twoFABusy, setTwoFABusy] = useState(false);

  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    // Use the user-scoped activity endpoint (not admin-only audit-logs).
    fetch('/api/auth/activity', { credentials: 'include' })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then((r: { items: AuditLog[] }) => {
        if (mounted) { setAuditLogs(r.items || []); setLogsLoading(false); }
      })
      .catch(() => { if (mounted) setLogsLoading(false); });
    return () => { mounted = false };
  }, []);

  const handlePwUpdate = async () => {
    setPwMsg(null); setPwErr(null);
    if (!currentPw || !newPw || !confirmPw) { setPwErr('All three fields are required.'); return; }
    if (newPw !== confirmPw) { setPwErr('New password and confirmation do not match.'); return; }
    if (newPw.length < 10) { setPwErr('New password must be at least 10 characters.'); return; }
    setPwSaving(true);
    try {
      const res = await fetch('/api/auth/password', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ currentPassword: currentPw, newPassword: newPw }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.message || 'Failed to update password.');
      setPwMsg('Password updated successfully.');
      setCurrentPw(''); setNewPw(''); setConfirmPw('');
    } catch (e: any) {
      setPwErr(e?.message || 'Failed to update password.');
    } finally {
      setPwSaving(false);
    }
  };

  const start2FAEnrollment = async () => {
    setTwoFAMsg(null); setTwoFAErr(null); setTwoFABusy(true);
    try {
      const res = await fetch('/api/auth/2fa/setup', { method: 'POST', credentials: 'include' });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.message || 'Failed to start 2FA enrollment.');
      setTwoFASecret(body.secret);
      setTwoFAOpen(true);
    } catch (e: any) {
      setTwoFAErr(e?.message || 'Failed to start 2FA enrollment.');
    } finally {
      setTwoFABusy(false);
    }
  };

  const confirm2FA = async () => {
    setTwoFAMsg(null); setTwoFAErr(null); setTwoFABusy(true);
    try {
      const res = await fetch('/api/auth/2fa/verify', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: twoFACode }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.message || 'Invalid 2FA code.');
      await refresh();
      setTwoFAMsg('Two-factor authentication enabled.');
      setTwoFAOpen(false);
      setTwoFASecret(''); setTwoFACode('');
    } catch (e: any) {
      setTwoFAErr(e?.message || 'Invalid 2FA code.');
    } finally {
      setTwoFABusy(false);
    }
  };

  const disable2FA = async () => {
    setTwoFAMsg(null); setTwoFAErr(null); setTwoFABusy(true);
    try {
      const res = await fetch('/api/auth/2fa/disable', { method: 'POST', credentials: 'include' });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.message || 'Failed to disable 2FA.');
      await refresh();
      setTwoFAMsg('Two-factor authentication disabled.');
    } catch (e: any) {
      setTwoFAErr(e?.message || 'Failed to disable 2FA.');
    } finally {
      setTwoFABusy(false);
    }
  };

  if (!user) {
    return <FadeIn><div className="p-8 text-center text-muted-foreground">Loading security settings…</div></FadeIn>;
  }

  // Build a list of recent login events from audit logs (real data).
  const loginEvents = auditLogs.filter(l => l.action === 'login' || l.action === 'logout' || l.action === 'register').slice(0, 5);

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Security" desc="Manage your account security" />

      {/* Password */}
      <Card>
        <CardHeader><CardTitle className="text-base">Password Management</CardTitle></CardHeader>
        <CardContent className="space-y-3 max-w-md">
          {pwMsg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{pwMsg}</div>}
          {pwErr && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{pwErr}</div>}
          <div><Label>Current Password</Label><Input type="password" value={currentPw} onChange={e => setCurrentPw(e.target.value)} disabled={pwSaving} /></div>
          <div><Label>New Password</Label><Input type="password" value={newPw} onChange={e => setNewPw(e.target.value)} disabled={pwSaving} /></div>
          <div><Label>Confirm New Password</Label><Input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)} disabled={pwSaving} /></div>
          <Button onClick={handlePwUpdate} disabled={pwSaving} style={{ backgroundColor: PRIMARY }}>Update Password</Button>
        </CardContent>
      </Card>

      {/* 2FA */}
      <Card>
        <CardHeader><CardTitle className="text-base">Two-Factor Authentication</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {twoFAMsg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{twoFAMsg}</div>}
          {twoFAErr && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{twoFAErr}</div>}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Authenticator App</p>
              <p className="text-xs text-muted-foreground">
                {user.mfaEnabled ? 'Two-factor authentication is currently ENABLED on your account.' : 'Add an extra layer of security to your account using Google Authenticator, 1Password, or similar TOTP apps.'}
              </p>
            </div>
            {user.mfaEnabled ? (
              <Badge className="bg-emerald-500 text-white">Enabled</Badge>
            ) : (
              <Badge variant="secondary">Disabled</Badge>
            )}
          </div>
          {user.mfaEnabled ? (
            <Button variant="outline" size="sm" onClick={disable2FA} disabled={twoFABusy}>{twoFABusy ? 'Working…' : 'Disable 2FA'}</Button>
          ) : (
            <Button size="sm" onClick={start2FAEnrollment} disabled={twoFABusy} style={{ backgroundColor: PRIMARY }}>
              <QrCode className="h-4 w-4 mr-1.5" />{twoFABusy ? 'Starting…' : 'Set up 2FA'}
            </Button>
          )}

          {/* 2FA enrollment dialog */}
          <Dialog open={twoFAOpen} onOpenChange={(open) => { setTwoFAOpen(open); if (!open) { setTwoFASecret(''); setTwoFACode(''); setTwoFAShowSecret(false); } }}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Set up Two-Factor Authentication</DialogTitle>
                <DialogDescription>Scan the secret below with your authenticator app, then enter the 6-digit code it generates.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="rounded-lg border bg-muted/40 p-4">
                  <p className="text-xs text-muted-foreground mb-1">Manual entry secret (base32):</p>
                  <div className="flex items-center gap-2">
                    <Input type={twoFAShowSecret ? 'text' : 'password'} value={twoFASecret} readOnly className="font-mono text-sm break-all" />
                    <Button type="button" variant="outline" size="sm" onClick={() => setTwoFAShowSecret(s => !s)}><Eye className="h-3 w-3 mr-1" />{twoFAShowSecret ? 'Hide' : 'Show'}</Button>
                  </div>
                  <p className="text-xs text-muted-foreground mt-2">Account: {user.email}</p>
                  <p className="text-xs text-muted-foreground">Issuer: DrugOS</p>
                </div>
                <div>
                  <Label>6-digit verification code</Label>
                  <Input value={twoFACode} onChange={e => setTwoFACode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="123456" inputMode="numeric" />
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setTwoFAOpen(false)}>Cancel</Button>
                <Button onClick={confirm2FA} disabled={twoFABusy || twoFACode.length !== 6} style={{ backgroundColor: PRIMARY }}>{twoFABusy ? 'Verifying…' : 'Verify & Enable'}</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </CardContent>
      </Card>

      {/* Recent activity — real audit logs */}
      <Card>
        <CardHeader><CardTitle className="text-base">Recent Account Activity</CardTitle></CardHeader>
        <CardContent>
          {logsLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : loginEvents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recent account activity.</p>
          ) : (
            <div className="space-y-2">
              {loginEvents.map(ev => (
                <div key={ev.id} className="flex items-center justify-between text-sm border-b border-border last:border-0 py-2">
                  <div className="flex items-center gap-3">
                    <Activity className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <p className="font-medium capitalize">{ev.action.replace(/_/g, ' ')}</p>
                      <p className="text-xs text-muted-foreground">{ev.actorName}{ev.ip ? ` · ${ev.ip}` : ''}</p>
                    </div>
                  </div>
                  <span className="text-xs text-muted-foreground">{new Date(ev.createdAt).toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">
            This is your current signed-in session. DrugOS does not maintain other long-lived sessions; if you signed in elsewhere, you will see those login events in the activity list above.
          </p>
        </CardContent>
      </Card>
    </div></FadeIn>
  );
}
