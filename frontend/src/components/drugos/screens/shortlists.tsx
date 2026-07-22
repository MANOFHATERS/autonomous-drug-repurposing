'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, BarChart3 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { useDrugOSNav } from '../nav-context';
import { EmptyState } from '../use-api-data';
import { ScoreBar } from '../score-bar';
import { drugCandidates } from '@/lib/empty-defaults';
import {
  PRIMARY, PageHeader, FadeIn, EmptyDataState,
} from './_core-shared';

interface Shortlist { id: string; name: string; drugs: string[]; created: string; }
const SHORTLISTS_STORAGE_KEY = 'drugos:shortlists-v2';
function useShortlists() {
  const [shortlists, setShortlists] = useState<Shortlist[]>(() => {
    if (typeof window === 'undefined') return [];
    try { const raw = window.localStorage.getItem(SHORTLISTS_STORAGE_KEY); return raw ? (JSON.parse(raw) as Shortlist[]) : []; } catch { return []; }
  });
  useEffect(() => { if (typeof window === 'undefined') return; try { window.localStorage.setItem(SHORTLISTS_STORAGE_KEY, JSON.stringify(shortlists)); } catch {} }, [shortlists]);
  const createShortlist = useCallback((name: string) => { const sl: Shortlist = { id: `sl-${Date.now()}`, name: name || `Shortlist ${Date.now()}`, drugs: [], created: new Date().toISOString().slice(0, 10) }; setShortlists(prev => [...prev, sl]); return sl.id; }, []);
  const deleteShortlist = useCallback((id: string) => { setShortlists(prev => prev.filter(sl => sl.id !== id)); }, []);
  return { shortlists, createShortlist, deleteShortlist };
}

export function ShortlistsScreen() {
  const { shortlists, createShortlist, deleteShortlist } = useShortlists();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const { navigate } = useDrugOSNav();
  if (shortlists.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Shortlists & Collections" description="Manage your candidate shortlists" actions={<Button style={{ backgroundColor: PRIMARY }}><Plus className="h-4 w-4 mr-2" />New Shortlist</Button>} />
        <EmptyDataState title="No shortlists yet" hint="Create a shortlist from the search results to save and compare candidate collections." />
      </FadeIn>
    );
  }
  return (
    <FadeIn>
      <PageHeader title="Shortlists & Collections" description="Manage your candidate shortlists" actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setShowCreate(s => !s)}><Plus className="h-4 w-4 mr-2" />New Shortlist</Button>} />
      {showCreate && (
        <Card className="mb-4">
          <CardContent className="p-4 flex items-center gap-2">
            <Input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Shortlist name (e.g. HD Top Picks)" className="flex-1" />
            <Button style={{ backgroundColor: PRIMARY }} onClick={() => { if (newName.trim()) { createShortlist(newName.trim()); setNewName(''); setShowCreate(false); } }}>Create</Button>
            <Button variant="outline" onClick={() => { setShowCreate(false); setNewName(''); }}>Cancel</Button>
          </CardContent>
        </Card>
      )}
      {shortlists.length === 0 ? (
        <EmptyState title="No shortlists yet" description="Create a shortlist to save and organize drug candidates for review. Shortlists are persisted to your browser's local storage." />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {shortlists.map(sl => (
            <Card key={sl.id}>
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="text-base">{sl.name}</CardTitle>
                    <CardDescription>{sl.drugs.length} drugs · Created {sl.created}</CardDescription>
                  </div>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => deleteShortlist(sl.id)} title="Delete shortlist"><Trash2 className="h-3.5 w-3.5 text-muted-foreground" /></Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {sl.drugs.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-2">No drugs in this shortlist yet.</p>
                ) : sl.drugs.map(d => {
                  const cand = drugCandidates.find(c => c.drugName === d);
                  return (
                    <div key={d} className="flex items-center justify-between p-2 rounded-lg hover:bg-accent cursor-pointer" onClick={() => cand && navigate({ page: 'app', section: 'candidate', id: cand.id })}>
                      <span className="text-sm">{d}</span>
                      {cand && <ScoreBar score={cand.compositeScore} size="sm" />}
                    </div>
                  );
                })}
                <Button variant="outline" size="sm" className="w-full mt-2" onClick={() => navigate({ page: 'app', section: 'comparison' })}><BarChart3 className="h-4 w-4 mr-1.5" />Compare</Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </FadeIn>
  );
}
