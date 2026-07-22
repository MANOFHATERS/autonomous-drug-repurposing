'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Plus, Target, MessageSquare, FolderKanban } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 4. PROJECTS SCREEN — real projects from /api/projects
// ═══════════════════════════════════════════
export function ProjectsScreen() {
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [projects, setProjects] = useState<Array<{ id: string; name: string; description: string | null; status: string; updatedAt: string; _count?: { hypotheses: number; comments: number } }>>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const loadProjects = () => {
    setLoading(true);
    api.listProjects().then(r => {
      setProjects(r.items);
      setLoading(false);
    }).catch(e => {
      setErr(e?.message || 'Failed to load projects.');
      setLoading(false);
    });
  };

  useEffect(() => { loadProjects(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true); setErr(null);
    try {
      await api.createProject({ name: newName.trim(), description: newDesc.trim() || undefined });
      setNewName(''); setNewDesc('');
      setCreateOpen(false);
      loadProjects();
    } catch (e: any) {
      setErr(e?.message || 'Failed to create project.');
    } finally {
      setCreating(false);
    }
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader
        title="Projects"
        desc={loading ? 'Loading projects…' : `${projects.length} research project${projects.length === 1 ? '' : 's'}`}
        actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />New Project</Button>}
      />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading projects…</p>
      ) : projects.length === 0 ? (
        <Card><CardContent className="p-8 text-center">
          <FolderKanban className="h-10 w-10 text-muted-foreground/50 mx-auto mb-3" />
          <p className="text-sm font-medium">No projects yet</p>
          <p className="text-xs text-muted-foreground mt-1">Create a project to organize your research and collaborate with your team.</p>
        </CardContent></Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map(p => (
            <Card key={p.id} className="hover:shadow-md transition-shadow cursor-pointer">
              <CardContent className="p-5">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h3 className="font-semibold text-sm">{p.name}</h3>
                    <p className="text-xs text-muted-foreground mt-1">{p.description || 'No description'}</p>
                  </div>
                  <Badge variant={p.status === 'active' ? 'default' : 'secondary'} className="capitalize">{p.status}</Badge>
                </div>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <div className="flex items-center gap-3">
                    <span className="flex items-center gap-1"><Target className="h-3 w-3" />{p._count?.hypotheses || 0} hypotheses</span>
                    <span className="flex items-center gap-1"><MessageSquare className="h-3 w-3" />{p._count?.comments || 0} comments</span>
                  </div>
                  <span>Updated {new Date(p.updatedAt).toLocaleDateString()}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Create New Project</DialogTitle>
          <DialogDescription>Set up a new research project workspace</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div><Label>Project Name</Label><Input placeholder="e.g. Parkinson's Repurposing" value={newName} onChange={e => setNewName(e.target.value)} /></div>
            <div><Label>Description</Label><Textarea placeholder="Describe the research goal..." value={newDesc} onChange={e => setNewDesc(e.target.value)} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button style={{ backgroundColor: PRIMARY }} onClick={handleCreate} disabled={creating || !newName.trim()}>{creating ? 'Creating…' : 'Create Project'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div></FadeIn>
  );
}
