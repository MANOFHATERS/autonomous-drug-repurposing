'use client';

import { useState, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sun, Moon, MonitorSmartphone } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY, safeLocalStorageGetJSON, safeLocalStorageSet } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 26. PREFERENCES SCREEN — applies theme via next-themes useTheme()
// ═══════════════════════════════════════════
export function PreferencesScreen() {
  const { theme, setTheme, systemTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [autoSave, setAutoSave] = useState(true);
  const [resultsPerPage, setResultsPerPage] = useState('20');
  const [exportFormat, setExportFormat] = useState('csv');
  const [therapeuticArea, setTherapeuticArea] = useState('all');
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // next-themes returns theme=undefined on first SSR render; only show
  // the active highlight after mount to avoid hydration mismatch.
  useEffect(() => { setMounted(true); }, []);

  // Load saved preferences from localStorage so they persist across sessions.
  useEffect(() => {
    if (!mounted) return;
    // FE-058 ROOT FIX (TM13): safeLocalStorage helpers (no try/catch).
    const p = safeLocalStorageGetJSON<Record<string, unknown>>('drugos:preferences', {});
    if (p && typeof p === 'object') {
      if (p.autoSave !== undefined) setAutoSave(p.autoSave as boolean);
      if (p.resultsPerPage) setResultsPerPage(String(p.resultsPerPage));
      if (p.exportFormat) setExportFormat(p.exportFormat as string);
      if (p.therapeuticArea) setTherapeuticArea(p.therapeuticArea as string);
    }
  }, [mounted]);

  const handleSave = () => {
    // FE-058 ROOT FIX (TM13): safeLocalStorageSet returns false on failure.
    if (safeLocalStorageSet('drugos:preferences', JSON.stringify({
      autoSave, resultsPerPage, exportFormat, therapeuticArea,
    }))) {
      setSavedMsg('Preferences saved.');
      setTimeout(() => setSavedMsg(null), 2500);
    } else {
      setSavedMsg('Failed to save preferences (storage unavailable).');
    }
  };

  const themes: { id: 'light' | 'dark' | 'system'; label: string; icon: React.ElementType }[] = [
    { id: 'light', label: 'Light', icon: Sun },
    { id: 'dark', label: 'Dark', icon: Moon },
    { id: 'system', label: 'System', icon: MonitorSmartphone },
  ];

  const activeTheme = mounted ? (theme || 'light') : 'light';

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Preferences" desc="Customize your DrugOS experience" />
      {savedMsg && (
        <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{savedMsg}</div>
      )}
      <Card><CardContent className="p-6 space-y-6">
        <div>
          <Label>Theme</Label>
          <p className="text-xs text-muted-foreground mb-3">Choose how DrugOS looks. System follows your operating system preference.</p>
          <div className="flex gap-3">
            {themes.map(t => {
              const Icon = t.icon;
              const isActive = activeTheme === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setTheme(t.id)}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg border text-sm transition-colors ${isActive ? 'border-primary bg-primary/5 text-primary' : 'hover:bg-accent'}`}
                >
                  <Icon className="h-4 w-4" />
                  {t.label}
                  {t.id === 'system' && mounted && systemTheme && (
                    <span className="text-xs text-muted-foreground">({systemTheme})</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <Separator />

        <div>
          <Label>Default Therapeutic Area</Label>
          <p className="text-xs text-muted-foreground mb-2">Pre-filter disease searches to a therapeutic area.</p>
          <Select value={therapeuticArea} onValueChange={setTherapeuticArea}>
            <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Areas</SelectItem>
              <SelectItem value="Neurology">Neurology</SelectItem>
              <SelectItem value="Oncology">Oncology</SelectItem>
              <SelectItem value="Rare Disease">Rare Disease</SelectItem>
              <SelectItem value="Cardiology">Cardiology</SelectItem>
              <SelectItem value="Infectious Disease">Infectious Disease</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label>Results Per Page</Label>
          <p className="text-xs text-muted-foreground mb-2">Default number of results shown in tables.</p>
          <Select value={resultsPerPage} onValueChange={setResultsPerPage}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="10">10</SelectItem>
              <SelectItem value="20">20</SelectItem>
              <SelectItem value="50">50</SelectItem>
              <SelectItem value="100">100</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Separator />

        <div className="flex items-center justify-between">
          <div>
            <Label>Auto-save Queries</Label>
            <p className="text-xs text-muted-foreground">Automatically save search queries to history</p>
          </div>
          <Switch checked={autoSave} onCheckedChange={setAutoSave} />
        </div>

        <div className="flex items-center justify-between">
          <div><Label>Default Export Format</Label></div>
          <Select value={exportFormat} onValueChange={setExportFormat}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="csv">CSV</SelectItem>
              <SelectItem value="json">JSON</SelectItem>
              <SelectItem value="xlsx">Excel</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Button onClick={handleSave} style={{ backgroundColor: PRIMARY }}>Save Preferences</Button>
      </CardContent></Card>
    </div></FadeIn>
  );
}
