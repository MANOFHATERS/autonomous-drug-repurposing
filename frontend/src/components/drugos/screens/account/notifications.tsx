'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Bell } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY, safeLocalStorageGetJSON, safeLocalStorageSet } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 25. NOTIFICATIONS SCREEN — real notifications from /api/notifications + preferences
// ═══════════════════════════════════════════
export function NotificationsScreen() {
  const [notifications, setNotifications] = useState<Array<{ id: string; type: string; title: string; body: string; readAt: string | null; createdAt: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs] = useState({ emailQuery: true, emailReport: true, emailCollab: false, inlineQuery: true, inlineReport: true, inlineCollab: true, pushQuery: false, pushReport: true, pushCollab: false });
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const toggle = (key: keyof typeof prefs) => setPrefs(prev => ({ ...prev, [key]: !prev[key] }));

  useEffect(() => {
    let mounted = true;
    // Load real notifications + saved preferences in parallel.
    Promise.all([
      api.listNotifications().catch(() => ({ items: [] as typeof notifications })),
      new Promise<typeof prefs>((resolve) => {
        // FE-058 ROOT FIX (TM13): use safeLocalStorage helpers (SSR +
        // private-mode + malformed-JSON safe). No try/catch needed here.
        const saved = safeLocalStorageGetJSON<Partial<typeof prefs>>('drugos:notification-prefs', {});
        resolve({ ...prefs, ...saved });
      }),
    ]).then(([notifs, savedPrefs]) => {
      if (!mounted) return;
      setNotifications(notifs.items || []);
      setPrefs(savedPrefs);
      setLoading(false);
    });
    return () => { mounted = false };
  }, []);

  const handleMarkRead = async (id: string) => {
    try {
      await api.markNotificationRead(id);
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, readAt: new Date().toISOString() } : n));
    } catch { /* ignore */ }
  };

  const handleSavePrefs = () => {
    // FE-058 ROOT FIX (TM13): safeLocalStorageSet returns false on failure
    // (private mode, quota exceeded) — no try/catch needed at call site.
    if (safeLocalStorageSet('drugos:notification-prefs', JSON.stringify(prefs))) {
      setSavedMsg('Notification preferences saved.');
      setTimeout(() => setSavedMsg(null), 2500);
    } else {
      setSavedMsg('Failed to save preferences (storage unavailable).');
    }
  };

  const categories = [
    { name: 'Query Results', emailKey: 'emailQuery' as const, inlineKey: 'inlineQuery' as const, pushKey: 'pushQuery' as const },
    { name: 'Report Ready', emailKey: 'emailReport' as const, inlineKey: 'inlineReport' as const, pushKey: 'pushReport' as const },
    { name: 'Collaboration', emailKey: 'emailCollab' as const, inlineKey: 'inlineCollab' as const, pushKey: 'pushCollab' as const },
  ];

  const typeColor = (type: string) => {
    if (type === 'success') return 'bg-emerald-500';
    if (type === 'warning') return 'bg-amber-500';
    if (type === 'error') return 'bg-red-500';
    return 'bg-primary';
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Notifications" desc="Your recent notifications and how you want to be notified" />
      {savedMsg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{savedMsg}</div>}

      {/* Recent notifications — real data */}
      <Card>
        <CardHeader><CardTitle className="text-base">Recent Notifications</CardTitle></CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading notifications…</p>
          ) : notifications.length === 0 ? (
            <div className="text-center py-6">
              <Bell className="h-8 w-8 text-muted-foreground/50 mx-auto mb-2" />
              <p className="text-sm font-medium">No notifications yet</p>
              <p className="text-xs text-muted-foreground mt-1">You'll see system and research notifications here as they happen.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {notifications.map(n => (
                <div key={n.id} className={`flex items-start gap-3 p-3 rounded-lg border ${n.readAt ? 'opacity-60' : 'bg-muted/40'}`}>
                  <span className={`h-2 w-2 rounded-full mt-1.5 shrink-0 ${typeColor(n.type)}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-medium text-sm">{n.title}</p>
                      <span className="text-xs text-muted-foreground shrink-0">{new Date(n.createdAt).toLocaleString()}</span>
                    </div>
                    <p className="text-sm text-muted-foreground mt-0.5">{n.body}</p>
                  </div>
                  {!n.readAt && (
                    <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleMarkRead(n.id)}>Mark read</Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Notification channel preferences */}
      <Card><CardContent className="p-0"><Table>
        <TableHeader><TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="text-center">Email</TableHead>
          <TableHead className="text-center">In-App</TableHead>
          <TableHead className="text-center">Push</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {categories.map(c => (
            <TableRow key={c.name}>
              <TableCell className="font-medium">{c.name}</TableCell>
              <TableCell className="text-center"><Switch checked={prefs[c.emailKey]} onCheckedChange={() => toggle(c.emailKey)} /></TableCell>
              <TableCell className="text-center"><Switch checked={prefs[c.inlineKey]} onCheckedChange={() => toggle(c.inlineKey)} /></TableCell>
              <TableCell className="text-center"><Switch checked={prefs[c.pushKey]} onCheckedChange={() => toggle(c.pushKey)} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table></CardContent></Card>

      <Card><CardContent className="p-6 space-y-4">
        <div><Label>Digest Frequency</Label>
          <Select defaultValue="daily">
            <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="realtime">Real-time</SelectItem>
              <SelectItem value="hourly">Hourly</SelectItem>
              <SelectItem value="daily">Daily</SelectItem>
              <SelectItem value="weekly">Weekly</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div><Label>Quiet Hours</Label>
          <div className="flex items-center gap-2">
            <Input type="time" defaultValue="22:00" className="w-28" />
            <span className="text-sm">to</span>
            <Input type="time" defaultValue="08:00" className="w-28" />
          </div>
        </div>
        <Button style={{ backgroundColor: PRIMARY }} onClick={handleSavePrefs}>Save Preferences</Button>
      </CardContent></Card>
    </div></FadeIn>
  );
}
