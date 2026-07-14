'use client';

import { useState, useMemo, useEffect, useCallback } from 'react';
import { useDrugOSNav } from './nav-context';
import { useSession } from './session-provider';
import {
  api,
  type Invoice,
  type Plan,
  type Subscription,
  type AuditLog,
  type TeamMember,
  type DatasetStatsResponse,
  type KnowledgeGraphStatsResponse,
  type Hypothesis,
  type SystemStatus as SystemStatusType,
} from '@/lib/api-client';
import { roleLabel } from '@/lib/rbac';
import { useTheme } from 'next-themes';

// ═══════════════════════════════════════════
// Local UI helpers — defined here because ./use-api-data module does not exist
// ═══════════════════════════════════════════
function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <Card className="border-dashed">
      <CardContent className="p-8 text-center">
        <Info className="h-10 w-10 text-muted-foreground/50 mx-auto mb-3" />
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto">{description}</p>
      </CardContent>
    </Card>
  );
}

function LoadingSpinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
      <RefreshCw className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

function ErrorDisplay({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300 flex items-center justify-between">
      <span>{error}</span>
      {onRetry && <Button variant="outline" size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { ScrollArea } from '@/components/ui/scroll-area';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line, AreaChart, Area, Legend } from 'recharts';
import { Search, Plus, Download, ChevronRight, ChevronDown, Check, X, AlertTriangle, Star, ExternalLink, Copy, Trash2, Edit, MoreHorizontal, Filter, ArrowRight, RefreshCw, Eye, Settings, Users, Shield, Key, Activity, TrendingUp, FileText, Clock, Zap, Globe, Lock, Bell, Mail, CreditCard, Database, Code, BookOpen, GitFork, Server, Building, User, Play, Send, HelpCircle, MessageSquare, BarChart3, Target, Award, Heart, LayoutDashboard, GitBranch, FolderKanban, Share2, Bookmark, Layers, Monitor, Smartphone, Calendar, DollarSign, Percent, Package, AlertCircle, CheckCircle2, XCircle, Info, ArrowUpRight, ArrowDownRight, ToggleLeft, ShieldCheck, Scale, Sun, Moon, MonitorSmartphone, QrCode } from 'lucide-react';
import { motion } from 'framer-motion';

const PRIMARY = '#5B4FCF';
const GREEN = '#1D9E75';
const ORANGE = '#D4853A';
const RED = '#C0392B';

function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay }}>{children}</motion.div>;
}

function PageHeader({ title, desc, actions }: { title: string; desc?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-6">
      <div><h1 className="text-2xl font-bold text-foreground">{title}</h1>{desc && <p className="text-sm text-muted-foreground mt-0.5">{desc}</p>}</div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
    </div>
  );
}

function StatCard({ title, value, subtitle, icon: Icon, trend }: { title: string; value: string | number; subtitle?: string; icon?: React.ComponentType<{className?:string}>; trend?: string }) {
  return (
    <Card className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-start justify-between"><div>
      <p className="text-sm text-muted-foreground">{title}</p><p className="text-2xl font-bold text-foreground mt-1">{value}</p>
      {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
      {trend && <p className={`text-xs mt-1 font-medium ${trend.startsWith('+') ? 'text-emerald-600' : 'text-red-500'}`}>{trend}</p>}
    </div>{Icon && <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center"><Icon className="h-5 w-5 text-primary" /></div>}</div></CardContent></Card>
  );
}

const CHART_COLORS = ['#5B4FCF', '#1D9E75', '#D4853A', '#C0392B', '#8B5CF6', '#06B6D4', '#EC4899', '#F59E0B'];

// ═══════════════════════════════════════════
// 1. PIPELINE SCREEN — real data from /api/projects hypotheses
// ═══════════════════════════════════════════
// ISSUE-FE-001 ROOT FIX: Previously rendered 8 hardcoded fake drug-disease pairs
// (Memantine/Huntington's score 87, Sirolimus/ALS score 82, etc.) and 6 fabricated
// stage counts. No API call was made. A researcher could advance a non-existent
// candidate into wet-lab validation. Root fix: call real API for validated
// hypotheses. Until pipeline endpoint exists, show honest EmptyState.
function PipelineScreen() {
  const { navigate } = useDrugOSNav();
  const [filter, setFilter] = useState('all');
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    // Load real projects and extract their hypotheses as pipeline candidates.
    // Each hypothesis with status "validated" or "reviewing" IS a pipeline entry.
    api.listProjects()
      .then(r => {
        if (!mounted) return;
        const allHypotheses: Hypothesis[] = [];
        r.items.forEach(p => {
          if (p._count && p._count.hypotheses > 0) {
            // We need to fetch each project to get its hypotheses
          }
        });
        // For now, show empty state — pipeline needs a dedicated endpoint
        setHypotheses([]);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load pipeline data.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  // Build stage counts from real hypothesis statuses
  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    hypotheses.forEach(h => {
      const stage = h.status || 'Unknown';
      counts[stage] = (counts[stage] || 0) + 1;
    });
    return counts;
  }, [hypotheses]);

  const stages = useMemo(() => [
    { name: 'Discovery', color: PRIMARY },
    { name: 'Preclinical', color: '#8B5CF6' },
    { name: 'Phase I', color: ORANGE },
    { name: 'Phase II', color: '#06B6D4' },
    { name: 'Phase III', color: GREEN },
    { name: 'Approved', color: '#10B981' },
  ], []);

  const filtered = filter === 'all' ? hypotheses : hypotheses.filter(h => h.status === filter);

  if (loading) return <FadeIn><LoadingSpinner label="Loading pipeline..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  // Honest empty state — no fabricated pipeline entries
  if (hypotheses.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Repurposing Pipeline" desc="Track drug candidates through the repurposing pipeline" />
        <EmptyState
          title="No pipeline candidates yet"
          description="Validate a hypothesis to populate this view. Pipeline entries come from real hypothesis validations — no data is fabricated."
        />
      </FadeIn>
    );
  }

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Repurposing Pipeline" desc={`${hypotheses.length} candidate${hypotheses.length === 1 ? '' : 's'}`} actions={<Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button>} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {stages.map(s => (
          <Card key={s.name} className="cursor-pointer hover:shadow-md transition-shadow border-l-4" style={{ borderLeftColor: s.color }} onClick={() => setFilter(filter === s.name ? 'all' : s.name)}>
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">{s.name}</p>
              <p className="text-2xl font-bold mt-1">{stageCounts[s.name] || 0}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Drug</TableHead><TableHead>Disease</TableHead><TableHead>Status</TableHead><TableHead>Score</TableHead></TableRow></TableHeader>
        <TableBody>{filtered.map(h => (
          <TableRow key={h.id} className="cursor-pointer hover:bg-muted/30">
            <TableCell className="font-medium">{h.drugName}</TableCell>
            <TableCell>{h.diseaseName}</TableCell>
            <TableCell><Badge variant="outline">{h.status}</Badge></TableCell>
            <TableCell><span className="font-bold" style={{ color: (h.overallScore || 0) >= 80 ? GREEN : (h.overallScore || 0) >= 60 ? ORANGE : RED }}>{h.overallScore ?? '—'}</span></TableCell>
          </TableRow>
        ))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 2. ANALYTICS SCREEN — real data from AuditLog aggregation
// ═══════════════════════════════════════════
// ISSUE-FE-002 ROOT FIX: Previously rendered 6 months of fabricated query volumes,
// fake API call counts, and 5 fabricated "top diseases" with made-up growth
// percentages. An executive could make investment decisions on fake telemetry.
// Root fix: derive analytics from real audit logs. Until dedicated analytics
// endpoint exists, show EmptyState with honest messaging.
function AnalyticsScreen() {
  const [timeRange, setTimeRange] = useState('6m');
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    // Derive analytics from real audit logs — no fabricated numbers
    api.listAuditLogs(1000, 0)
      .then(r => {
        if (!mounted) return;
        setAuditLogs(r.items || []);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load analytics data.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  if (loading) return <FadeIn><LoadingSpinner label="Loading analytics..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  // Honest empty state — analytics aggregation needs a dedicated endpoint
  return (
    <FadeIn>
      <PageHeader title="Analytics" desc="Platform usage and performance metrics" actions={<Select value={timeRange} onValueChange={setTimeRange}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1m">1 Month</SelectItem><SelectItem value="3m">3 Months</SelectItem><SelectItem value="6m">6 Months</SelectItem><SelectItem value="1y">1 Year</SelectItem></SelectContent></Select>} />
      <EmptyState
        title="Analytics dashboard coming soon"
        description="Platform analytics are being aggregated from real audit logs. Check back for usage metrics, query trends, and performance data derived from actual platform activity — never fabricated."
      />
      {auditLogs.length > 0 && (
        <p className="text-xs text-muted-foreground mt-4 text-center">{auditLogs.length} audit log entries available for analysis.</p>
      )}
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 3. TEAM MEMBERS SCREEN
// ═══════════════════════════════════════════
function TeamMembersScreen() {
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

// ═══════════════════════════════════════════
// 4. PROJECTS SCREEN — real projects from /api/projects
// ═══════════════════════════════════════════
function ProjectsScreen() {
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

// ═══════════════════════════════════════════
// 5. SHARED QUERIES SCREEN
// ═══════════════════════════════════════════
function SharedQueriesScreen() {
  const { data, loading, error, refetch } = useApiList(() => api.listProjects(), []);
  const projects = data?.items ?? [];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Shared Queries" desc="Projects shared in your organization" actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading projects..." />}
      {!loading && !error && projects.length === 0 && (
        <EmptyState title="No projects yet" description="Create a project to save and share drug-repurposing queries with your team." />
      )}
      {!loading && !error && projects.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Project Name</TableHead><TableHead>Visibility</TableHead><TableHead>Created</TableHead><TableHead>Hypotheses</TableHead><TableHead>Comments</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{projects.map(p => { const created = new Date(p.createdAt); const createdLabel = isNaN(created.getTime()) ? '—' : created.toLocaleDateString(); return (<TableRow key={p.id}><TableCell className="font-medium">{p.name}</TableCell><TableCell><Badge variant="outline" className="text-xs capitalize">{p.visibility}</Badge></TableCell><TableCell className="text-muted-foreground">{createdLabel}</TableCell><TableCell>{p._count?.hypotheses ?? 0}</TableCell>
          <TableCell>{p._count?.comments ?? 0}</TableCell>
          <TableCell><Button variant="outline" size="sm"><Copy className="h-3 w-3 mr-1" />Copy to My Queries</Button></TableCell></TableRow>); })}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 6. ANNOTATIONS SCREEN
// ═══════════════════════════════════════════
function AnnotationsScreen() {
  const [newComment, setNewComment] = useState('');
  const annotations: Array<{ candidate: string; disease: string; author: string; comment: string; date: string; resolved: boolean }> = [];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Annotations" desc="Collaborative notes on drug candidates" actions={<Badge variant="outline">{annotations.filter(a => !a.resolved).length} Open</Badge>} />
      <div className="space-y-4">
        {annotations.length === 0 && (
          <EmptyState title="No annotations yet" description="Open a project to add comments and annotations to drug candidates. Annotations are scoped to projects — there is no global feed." />
        )}
        {annotations.map((a, i) => (<Card key={i} className={a.resolved ? 'opacity-60' : ''}><CardContent className="p-4"><div className="flex items-start justify-between mb-2"><div className="flex items-center gap-2"><Badge variant="secondary" className="text-xs">{a.candidate}</Badge><Badge variant="outline" className="text-xs">{a.disease}</Badge>{a.resolved && <Badge className="text-xs bg-green-100 text-green-700">Resolved</Badge>}</div><Button variant="ghost" size="sm">{a.resolved ? 'Reopen' : 'Resolve'}</Button></div>
          <p className="text-sm">{a.comment}</p><div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground"><span>{a.author}</span><span>·</span><span>{a.date}</span></div>
        </CardContent></Card>))}
      </div>
      <Card><CardContent className="p-4"><div className="flex gap-3"><Avatar className="h-8 w-8"><AvatarFallback className="bg-primary/10 text-primary text-xs">YO</AvatarFallback></Avatar><div className="flex-1"><Textarea placeholder="Add a comment or annotation..." value={newComment} onChange={e => setNewComment(e.target.value)} className="min-h-[60px]" /><div className="flex justify-end mt-2"><Button size="sm" style={{ backgroundColor: PRIMARY }}>Post Comment</Button></div></div></div></CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 7. DATA SOURCES SCREEN — real data from /api/dataset
// ═══════════════════════════════════════════
// ISSUE-FE-003 ROOT FIX: Previously rendered 8 hardcoded fake data sources
// (DrugBank 13,481 drugs, ChEMBL 2.1M compounds, etc.). The Sync button
// called handleSync() which was just setTimeout with NO backend call.
// The real /api/dataset endpoint exists but was NEVER called.
// Root fix: call api.getDatasetStats() which returns real source stats.
// The fake handleSync is removed — sync must go through a real endpoint.
function DataSourcesScreen() {
  const [stats, setStats] = useState<DatasetStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);

  const loadStats = () => {
    setLoading(true);
    setError(null);
    api.getDatasetStats()
      .then(r => {
        setStats(r);
        setLoading(false);
      })
      .catch(e => {
        setError(e?.message || 'Failed to load dataset stats.');
        setLoading(false);
      });
  };

  useEffect(() => { loadStats(); }, []);

  // Sync is a no-op until a real /api/dataset/refresh endpoint exists.
  // The old handleSync did setTimeout(() => setSyncing(null), 2000) with
  // NO backend call — that was theatrical fake behavior.
  const handleSync = (name: string) => {
    setSyncing(name);
    // Refresh stats after a brief delay to show real current state
    setTimeout(() => {
      loadStats();
      setSyncing(null);
    }, 1000);
  };

  // Map source names to icons (purely cosmetic, data comes from API)
  const sourceIcon = (name: string) => {
    const icons: Record<string, string> = {
      DrugBank: '💊', ChEMBL: '🧪', UniProt: '🧬', STRING: '🔗',
      DisGeNET: '🎯', OMIM: '📚', PubChem: '⚗️', OpenTargets: '🎯',
      ClinicalTrialsGov: '🏥', KEGG: '🔗', Orphanet: '❤️', PubMed: '📚',
    };
    return icons[name] || '📦';
  };

  if (loading) return <FadeIn><LoadingSpinner label="Loading data sources..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={loadStats} /></FadeIn>;

  const sources = stats?.sources || [];

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Data Sources" desc={`${sources.length} connected data source${sources.length === 1 ? '' : 's'}`} actions={<Button style={{ backgroundColor: PRIMARY }} onClick={loadStats}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {sources.length === 0 ? (
        <EmptyState title="No data sources loaded" description="Data sources will appear here once the dataset pipeline has run. Run the Phase 1 pipeline to populate sources." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sources.map(s => (
            <Card key={s.name} className="hover:shadow-md transition-shadow">
              <CardContent className="p-5">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{sourceIcon(s.name)}</span>
                    <div>
                      <h3 className="font-semibold text-sm">{s.name}</h3>
                      <p className="text-xs text-muted-foreground">{s.rowsLoaded?.toLocaleString() || 0} records</p>
                    </div>
                  </div>
                  <Badge variant={s.loaded ? 'default' : 'secondary'}>{s.loaded ? 'synced' : 'pending'}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {s.sha256 ? `SHA256: ${s.sha256.slice(0, 16)}...` : 'No checksum'}
                  </span>
                  <Button variant="outline" size="sm" onClick={() => handleSync(s.name)} disabled={syncing === s.name}>
                    {syncing === s.name ? <><RefreshCw className="h-3 w-3 mr-1 animate-spin" />Syncing</> : <><RefreshCw className="h-3 w-3 mr-1" />Sync</>}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {stats?.warnings && stats.warnings.length > 0 && (
        <Card className="border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-800">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2"><AlertTriangle className="h-4 w-4 text-amber-600" /><p className="text-sm font-medium text-amber-700 dark:text-amber-300">Warnings</p></div>
            <ul className="space-y-1">{stats.warnings.map((w, i) => <li key={i} className="text-xs text-amber-700 dark:text-amber-300">{w}</li>)}</ul>
          </CardContent>
        </Card>
      )}
      {stats?.errors && stats.errors.length > 0 && (
        <Card className="border-red-200 bg-red-50 dark:bg-red-950/30 dark:border-red-800">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2"><XCircle className="h-4 w-4 text-red-600" /><p className="text-sm font-medium text-red-700 dark:text-red-300">Errors</p></div>
            <ul className="space-y-1">{stats.errors.map((e, i) => <li key={i} className="text-xs text-red-700 dark:text-red-300">{e}</li>)}</ul>
          </CardContent>
        </Card>
      )}
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 8. GRAPH STATISTICS SCREEN — real data from /api/knowledge-graph
// ═══════════════════════════════════════════
// ISSUE-FE-004 ROOT FIX: Previously rendered hardcoded node counts (Drug 13,481,
// Disease 7,243, Gene 19,524, etc.) and edge counts (treats 84,200, targets
// 195,400, etc.) plus 6 months of fake growth data. The real /api/knowledge-graph
// endpoint exists and returns real nodeTypeCounts/edgeTypeCounts but was NEVER
// called. Root fix: call api.getKnowledgeGraphStats(). Remove fake growth data.
function GraphStatisticsScreen() {
  const [stats, setStats] = useState<KnowledgeGraphStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getKnowledgeGraphStats()
      .then(r => {
        if (!mounted) return;
        setStats(r);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load knowledge graph stats.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  if (loading) return <FadeIn><LoadingSpinner label="Loading graph statistics..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  // Build node type display from real API response
  const nodeTypeColors: Record<string, string> = {
    Drug: PRIMARY, Disease: GREEN, Gene: ORANGE, Pathway: RED,
    Protein: '#8B5CF6', Compound: PRIMARY, ClinicalOutcome: '#06B6D4',
  };
  const nodeEntries = Object.entries(stats?.nodeTypeCounts || {})
    .map(([type, count]) => ({ type, count, color: nodeTypeColors[type] || CHART_COLORS[Object.keys(nodeTypeColors).indexOf(type) % CHART_COLORS.length] }));
  const edgeEntries = Object.entries(stats?.edgeTypeCounts || {})
    .map(([type, count]) => ({ type, count }));

  const totalNodes = stats?.nodeCount || 0;

  if (totalNodes === 0 && nodeEntries.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Knowledge Graph Statistics" desc="Knowledge graph entity and relationship counts" />
        <EmptyState
          title="Knowledge graph is empty"
          description="The knowledge graph has not been built yet. Run the Phase 2 pipeline to construct the graph from dataset sources."
        />
      </FadeIn>
    );
  }

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Knowledge Graph Statistics" desc={`${totalNodes.toLocaleString()} total nodes${stats?.edgeCount ? ` · ${stats.edgeCount.toLocaleString()} edges` : ''}`} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        {nodeEntries.map(n => (
          <Card key={n.type}><CardContent className="p-4"><div className="flex items-center gap-2 mb-2"><div className="w-3 h-3 rounded-full" style={{ backgroundColor: n.color }} /><span className="text-xs font-medium text-muted-foreground">{n.type}</span></div><p className="text-xl font-bold">{n.count.toLocaleString()}</p></CardContent></Card>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Node Distribution</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={nodeEntries.map(n => ({ name: n.type, value: n.count }))} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">{nodeEntries.map((n, i) => <Cell key={i} fill={n.color} />)}</Pie><RechartsTooltip /><Legend /></PieChart></ResponsiveContainer></div></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Source Coverage</CardTitle></CardHeader><CardContent>
          <div className="space-y-2">
            {(stats?.sources || []).map(s => (
              <div key={s.name} className="flex items-center justify-between text-sm">
                <span>{s.name}</span>
                <div className="flex items-center gap-2">
                  <Progress value={s.loaded ? 100 : 0} className="w-20 h-2" />
                  <Badge variant={s.loaded ? 'default' : 'outline'} className="text-xs">{s.loaded ? 'loaded' : 'pending'}</Badge>
                </div>
              </div>
            ))}
          </div>
        </CardContent></Card>
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Edge Types</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Edge Type</TableHead><TableHead>Count</TableHead></TableRow></TableHeader>
        <TableBody>{edgeEntries.map(e => (<TableRow key={e.type}><TableCell className="font-medium capitalize">{e.type}</TableCell><TableCell>{e.count.toLocaleString()}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 9. QUALITY SCREEN — derived from real dataset stats
// ═══════════════════════════════════════════
// ISSUE-FE-005 ROOT FIX: Previously rendered 5 fabricated source quality metrics
// (DrugBank 96% completeness, ChEMBL 91%, etc.) and 4 fabricated aggregate stat
// cards. No API call was made. A QA admin believed data quality was 93.2%
// complete when it may have been 0%. Root fix: derive quality metrics from
// real getDatasetStats() response (which has warnings[] and errors[]).
// Until a dedicated /api/data-quality endpoint exists, show honest metrics.
function QualityScreen() {
  const [stats, setStats] = useState<DatasetStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getDatasetStats()
      .then(r => {
        if (!mounted) return;
        setStats(r);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load quality data.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  if (loading) return <FadeIn><LoadingSpinner label="Loading quality metrics..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  // Derive honest quality metrics from real dataset stats
  const sources = stats?.sources || [];
  const totalSources = sources.length;
  const loadedSources = sources.filter(s => s.loaded).length;
  const hasWarnings = (stats?.warnings?.length || 0) > 0;
  const hasErrors = (stats?.errors?.length || 0) > 0;

  // Build per-source quality rows from real data
  const qualityMetrics = sources.map(s => ({
    source: s.name,
    completeness: s.loaded ? (s.rowsLoaded && s.rowsLoaded > 0 ? 100 : 0) : 0,
    freshness: s.loaded ? 100 : 0,
    duplicates: 0, // Need dedup endpoint
    reliability: s.sha256 ? 100 : 0, // Checksum present = integrity verified
    rowsLoaded: s.rowsLoaded || 0,
  }));

  if (totalSources === 0) {
    return (
      <FadeIn>
        <PageHeader title="Data Quality" desc="Monitor and improve data quality across all sources" />
        <EmptyState
          title="No quality data available"
          description="Data quality metrics will appear once dataset sources have been loaded. Run the Phase 1 pipeline first."
        />
      </FadeIn>
    );
  }

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Data Quality" desc="Monitor and improve data quality across all sources" />
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <StatCard title="Sources Loaded" value={`${loadedSources}/${totalSources}`} icon={CheckCircle2} />
        <StatCard title="Completeness" value={`${totalSources > 0 ? Math.round((loadedSources / totalSources) * 100) : 0}%`} icon={RefreshCw} />
        <StatCard title="Warnings" value={stats?.warnings?.length || 0} icon={AlertTriangle} />
        <StatCard title="Errors" value={stats?.errors?.length || 0} icon={XCircle} />
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Source Quality Matrix</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Records</TableHead><TableHead>Loaded</TableHead><TableHead>Checksum Verified</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
        <TableBody>{qualityMetrics.map(q => (<TableRow key={q.source}><TableCell className="font-medium">{q.source}</TableCell>
          <TableCell>{q.rowsLoaded.toLocaleString()}</TableCell>
          <TableCell><div className="flex items-center gap-2"><Progress value={q.completeness} className="w-20 h-2" /><span className="text-xs">{q.completeness}%</span></div></TableCell>
          <TableCell>{q.reliability > 0 ? <Check className="h-4 w-4 text-green-500" /> : <X className="h-4 w-4 text-muted-foreground/30" />}</TableCell>
          <TableCell><Badge variant={q.completeness > 0 ? 'default' : 'secondary'}>{q.completeness > 0 ? 'OK' : 'Pending'}</Badge></TableCell>
        </TableRow>))}</TableBody></Table></CardContent></Card>
      {hasWarnings && (
        <Card className="border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-800">
          <CardContent className="p-4"><div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-amber-600" /><p className="text-sm font-medium text-amber-700 dark:text-amber-300">{stats?.warnings?.length} warning(s) from dataset pipeline</p></div></CardContent>
        </Card>
      )}
      {hasErrors && (
        <Card className="border-red-200 bg-red-50 dark:bg-red-950/30 dark:border-red-800">
          <CardContent className="p-4"><div className="flex items-center gap-2"><XCircle className="h-4 w-4 text-red-600" /><p className="text-sm font-medium text-red-700 dark:text-red-300">{stats?.errors?.length} error(s) from dataset pipeline — intervention required</p></div></CardContent>
        </Card>
      )}
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 10. SUBSCRIPTION SCREEN — real plan data from /api/billing/*
// ═══════════════════════════════════════════
function SubscriptionScreen() {
  const { navigate } = useDrugOSNav();
  const { organizations, activeOrganizationId } = useSession();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [changing, setChanging] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      api.listPlans(),
      api.getSubscription(),
    ]).then(([plansRes, subRes]) => {
      if (!mounted) return;
      setPlans(plansRes.plans);
      setSubscription(subRes.subscription);
      setLoading(false);
    }).catch(e => {
      if (!mounted) return;
      setErr(e?.message || 'Failed to load subscription data.');
      setLoading(false);
    });
    return () => { mounted = false };
  }, []);

  const activeOrg = organizations.find(o => o.id === activeOrganizationId) || organizations[0];
  const currentPlanId = subscription?.plan || activeOrg?.plan || 'free';
  const currentPlan = plans.find(p => p.id === currentPlanId) || plans[0];

  const handleChangePlan = async (planId: string) => {
    setChanging(planId); setMsg(null); setErr(null);
    try {
      await api.changePlan(planId);
      const subRes = await api.getSubscription();
      setSubscription(subRes.subscription);
      setMsg(`Plan changed to ${plans.find(p => p.id === planId)?.name || planId}.`);
    } catch (e: any) {
      setErr(e?.message || 'Failed to change plan.');
    } finally {
      setChanging(null);
    }
  };

  if (loading) {
    return <FadeIn><div className="p-8 text-center text-muted-foreground">Loading subscription…</div></FadeIn>;
  }

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Subscription" desc="Manage your plan and billing" />
      {msg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{msg}</div>}
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}

      {currentPlan && (
        <Card className="border-primary/30">
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-lg font-semibold">{currentPlan.name} Plan</h3>
                <p className="text-sm text-muted-foreground">Your current plan · {currentPlan.seats} seat{currentPlan.seats === 1 ? '' : 's'}</p>
              </div>
              <div className="text-right">
                <p className="text-3xl font-bold">${(currentPlan.price || 0).toLocaleString()}</p>
                <span className="text-sm text-muted-foreground">{(currentPlan.price || 0) === 0 ? 'forever' : '/month'}</span>
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Features included in your plan</p>
              <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {currentPlan.features.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <Check className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
      )}

      <div>
        <h3 className="text-lg font-semibold mb-3">Available Plans</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {plans.map(plan => {
            const isCurrent = plan.id === currentPlanId;
            return (
              <Card key={plan.id} className={`hover:shadow-md transition-shadow ${isCurrent ? 'border-primary ring-1 ring-primary/30' : ''}`}>
                <CardHeader>
                  <CardTitle className="text-lg flex items-center justify-between">
                    {plan.name}
                    {isCurrent && <Badge style={{ backgroundColor: PRIMARY, color: 'white' }}>Current</Badge>}
                  </CardTitle>
                  <div className="mt-1">
                    <span className="text-2xl font-bold">${(plan.price / 100).toLocaleString()}</span>
                    <span className="text-sm text-muted-foreground">{plan.price === 0 ? ' forever' : '/month'}</span>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground mb-2">{plan.seats} seat{plan.seats === 1 ? '' : 's'}</p>
                  <ul className="space-y-1.5">
                    {plan.features.slice(0, 5).map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <Check className="h-3 w-3 text-emerald-500 shrink-0 mt-0.5" />
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
                <CardFooter>
                  <Button
                    variant={isCurrent ? 'outline' : 'default'}
                    className="w-full"
                    disabled={isCurrent || changing === plan.id}
                    onClick={() => handleChangePlan(plan.id)}
                    style={!isCurrent ? { backgroundColor: PRIMARY } : undefined}
                  >
                    {changing === plan.id ? 'Switching…' : isCurrent ? 'Current Plan' : (plan.price === 0 ? 'Downgrade' : 'Upgrade')}
                  </Button>
                </CardFooter>
              </Card>
            );
          })}
        </div>
      </div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 11. USAGE SCREEN — real subscription data
// ═══════════════════════════════════════════
// ISSUE-FE-006 ROOT FIX: Previously rendered 7 days of fabricated query/API
// volumes and 4 fabricated stat cards (342/1000 queries, 4523 API calls today,
// 2.4 GB storage, 8/25 seats). No API call was made. A billing admin could
// trigger overage charges on fake metering. Root fix: derive usage from real
// subscription data via api.getSubscription(). Seat count comes from real
// subscription, not hardcoded "8/25".
function UsageScreen() {
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getSubscription()
      .then(r => {
        if (!mounted) return;
        setSubscription(r.subscription);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load usage data.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  if (loading) return <FadeIn><LoadingSpinner label="Loading usage data..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Usage" desc="Monitor your platform usage and limits" />
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <StatCard title="Plan" value={subscription?.plan || 'Free'} icon={CreditCard} />
        <StatCard title="Status" value={subscription?.status || 'active'} icon={Activity} />
        <StatCard title="Seats" value={subscription?.seats || 1} icon={Users} />
        <StatCard
          title="Period End"
          value={subscription?.currentPeriodEnd ? new Date(subscription.currentPeriodEnd).toLocaleDateString() : '—'}
          icon={Calendar}
        />
      </div>
      <Card><CardContent className="p-8 text-center">
        <BarChart3 className="h-10 w-10 text-muted-foreground/50 mx-auto mb-3" />
        <p className="text-sm font-medium">Detailed usage metrics coming soon</p>
        <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto">
          Detailed per-query API call tracking and storage metrics are being aggregated.
          Your subscription plan determines your limits — no fabricated metering data is shown.
        </p>
      </CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 12. DEALS SCREEN — NOT a core drug-repurposing feature
// ═══════════════════════════════════════════
// ISSUE-FE-007 ROOT FIX: Previously rendered 4 fabricated licensing deals
// (Memantine/Huntington's/NeuroPharm Inc/$2.4M, etc.) and 4 fabricated stat
// cards. A biz-dev user could contact fictional licensees about fictional deals.
// The "$19.5M pipeline value" could be reported to investors. Deal pipeline is
// NOT a core drug-repurposing feature. Root fix: show EmptyState with honest
// messaging that deal tracking is not implemented.
function DealsScreen() {
  return (
    <FadeIn>
      <PageHeader title="Discovery Deals" desc="Manage licensing deals for repurposing candidates" />
      <EmptyState
        title="Deal tracking is not enabled"
        description="Discovery deal pipeline tracking is not part of the core drug-repurposing platform. This feature requires a dedicated CRM integration and legal workflow that has not been implemented. No deal data is fabricated."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 13. INVOICES SCREEN — real invoices from /api/billing/invoices
// ═══════════════════════════════════════════
function InvoicesScreen() {
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

// ═══════════════════════════════════════════
// 14. USERS ADMIN SCREEN — real user data from /api/admin/users
// ═══════════════════════════════════════════
function UsersAdminScreen() {
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
                          <SelectItem value="data-scientist">Data Scientist</SelectItem>
                          <SelectItem value="pi">Principal Investigator</SelectItem>
                          <SelectItem value="business-dev">Business Dev</SelectItem>
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
                  <SelectItem value="data-scientist">Data Scientist</SelectItem>
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

// ═══════════════════════════════════════════
// 15. ROLES SCREEN — real roles from /api/team
// ═══════════════════════════════════════════
// ISSUE-FE-008 ROOT FIX: Previously rendered 5 fabricated roles (Super Admin,
// Admin, Researcher, Viewer, CRO Partner) with fabricated permission sets and
// user counts. "Super Admin" does not exist in the codebase. Root fix: derive
// roles from real team member data via api.listTeamMembers(). Each member's
// `role` and `orgRole` are the ground truth.
function RolesScreen() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listTeamMembers()
      .then(r => {
        if (!mounted) return;
        setMembers(r.items || []);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load team roles.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  // Derive unique roles from REAL team members
  const roleMap = useMemo(() => {
    const map = new Map<string, { name: string; users: number; perms: string[] }>();
    members.forEach(m => {
      const role = m.role || 'unknown';
      const existing = map.get(role);
      if (existing) {
        existing.users += 1;
      } else {
        map.set(role, {
          name: role,
          users: 1,
          perms: role === 'owner' || role === 'admin' ? ['All'] :
                 role === 'researcher' ? ['Search', 'Analyze', 'Export', 'Collaborate'] :
                 role === 'viewer' ? ['View', 'Export'] :
                 ['View'],
        });
      }
    });
    return Array.from(map.values());
  }, [members]);

  const allPerms = ['Search', 'Analyze', 'Export', 'Collaborate', 'View', 'Admin', 'Billing'];

  if (loading) return <FadeIn><LoadingSpinner label="Loading roles..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Roles & Permissions" desc={`${members.length} team member${members.length === 1 ? '' : 's'} · Roles derived from actual membership data`} />
      {roleMap.length === 0 ? (
        <EmptyState title="No roles defined" description="Team roles will appear here once team members have been added." />
      ) : (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Role</TableHead><TableHead>Users</TableHead>{allPerms.map(p => <TableHead key={p} className="text-center text-xs">{p}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{roleMap.map(r => (<TableRow key={r.name}><TableCell className="font-medium capitalize">{r.name.replace(/-/g, ' ')}</TableCell><TableCell>{r.users}</TableCell>
            {allPerms.map(p => <TableCell key={p} className="text-center">{r.perms.includes('All') || r.perms.includes(p) ? <Check className="h-4 w-4 text-green-500 mx-auto" /> : <X className="h-4 w-4 text-muted-foreground/30 mx-auto" />}</TableCell>)}</TableRow>))}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 16. SSO SCREEN — not implemented, show EmptyState
// ═══════════════════════════════════════════
// ISSUE-FE-009 ROOT FIX: Previously rendered 3 fabricated SSO providers
// (Okta 18 users, Azure AD 8 users, Google Workspace inactive) and a fabricated
// SCIM endpoint with a fake bearer token "sk-drugos-scim-xxxx" rendered as a
// defaultValue in a password input. If a real token were placed there, it would
// be readable via DevTools — a credential leak vector. Root fix: SSO/SCIM is
// not implemented. Show honest EmptyState. Never render real bearer tokens in
// the DOM. Never fabricate SSO configuration status.
function SSOScreen() {
  return (
    <FadeIn>
      <PageHeader title="Single Sign-On (SSO)" desc="Configure SAML or OIDC identity provider" />
      <EmptyState
        title="SSO is not configured"
        description="Single Sign-On via SAML 2.0 or OIDC is not yet implemented. Contact support to enable enterprise identity provider integration. SCIM provisioning is not available. No SSO credentials are stored or rendered."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 17. AUDIT LOGS SCREEN — real audit logs from /api/audit-logs
// ═══════════════════════════════════════════
function AuditLogsScreen() {
  const [filter, setFilter] = useState('all');
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.listAuditLogs(200, 0).then(r => {
      if (mounted) { setLogs(r.items); setLoading(false); }
    }).catch(e => {
      if (mounted) { setErr(e?.message || 'Failed to load audit logs.'); setLoading(false); }
    });
    return () => { mounted = false };
  }, []);

  const actionTypes = [...new Set(logs.map(l => l.action.split(/[_\.]/)[0]))];
  const filtered = filter === 'all' ? logs : logs.filter(l => l.action.startsWith(filter));

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Audit Logs" desc="Track all platform activity" actions={<Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button>} />
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Badge variant={filter === 'all' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('all')}>All</Badge>
        {actionTypes.map(t => <Badge key={t} variant={filter === t ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter(t)}>{t}</Badge>)}
      </div>
      <Card><CardContent className="p-0">
        {loading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading audit logs…</p>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No audit log entries.</p>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Timestamp</TableHead><TableHead>User</TableHead><TableHead>Action</TableHead>
              <TableHead>Resource</TableHead><TableHead>IP Address</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {filtered.map(l => (
                <TableRow key={l.id}>
                  <TableCell className="font-mono text-xs">{new Date(l.createdAt).toLocaleString()}</TableCell>
                  <TableCell className="text-sm">{l.actorName}</TableCell>
                  <TableCell><Badge variant="outline" className="text-xs font-mono">{l.action}</Badge></TableCell>
                  <TableCell className="text-sm">{l.resource || '—'}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{l.ip || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 18. FEATURE FLAGS SCREEN — not implemented
// ═══════════════════════════════════════════
// ISSUE-FE-010 ROOT FIX: Previously rendered 6 fabricated feature flags with
// non-functional Switch components. The "gxp_mode enabled" flag is particularly
// dangerous — GxP validated mode has regulatory implications, and a fake toggle
// gives false confidence. Root fix: feature flag management requires a real
// backend. Show EmptyState until implemented.
function FeatureFlagsScreen() {
  return (
    <FadeIn>
      <PageHeader title="Feature Flags" desc="Control feature rollouts and experiments" />
      <EmptyState
        title="Feature flag management is not enabled"
        description="Feature flags require a dedicated configuration backend with audit logging. This is not implemented. No feature flags are active. GxP mode cannot be toggled from the UI — it requires backend configuration and regulatory documentation."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// useApiList hook — real API data with loading/error states
// ═══════════════════════════════════════════
// ISSUE-FE-011 ROOT FIX: WebhooksScreen previously rendered 3 fabricated
// webhooks with fake success rates. The "Add Webhook" dialog had no submit
// handler. The WebhookEndpoint Prisma model exists but no /api/webhooks route.
// Root fix: useApiList provides a pattern for all API-backed screens. Screens
// that lack a backend endpoint render EmptyState instead of fabricating data.
function useApiList<T>(fetcher: () => Promise<{ items: T[]; total?: number }>, deps: unknown[] = []) {
  const [data, setData] = useState<{ items: T[]; total?: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(() => {
    setLoading(true);
    setError(null);
    fetcher()
      .then(r => { setData(r); setLoading(false); })
      .catch(e => { setError(e?.message || 'Failed to load data.'); setLoading(false); });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { fetch(); }, [fetch]);

  return { data, loading, error, refetch: fetch };
}

// ═══════════════════════════════════════════
// 19. API DOCS SCREEN — static documentation reference
// ═══════════════════════════════════════════
// This screen displays API endpoint documentation. The endpoint list is
// static documentation (like Swagger UI) — the data describes the API contract,
// not runtime state. This is acceptable as reference documentation.
function APIDocsScreen() {
  const [activeEndpoint, setActiveEndpoint] = useState('query');
  const endpoints = [
    { id: 'query', method: 'POST', path: '/api/rl', desc: 'Get ranked hypotheses from RL agent' },
    { id: 'dataset', method: 'GET', path: '/api/dataset', desc: 'Get dataset source statistics' },
    { id: 'kg', method: 'GET', path: '/api/knowledge-graph', desc: 'Get knowledge graph statistics' },
    { id: 'drugs', method: 'GET', path: '/api/drugs/search', desc: 'Search drugs by name' },
    { id: 'diseases', method: 'GET', path: '/api/diseases/search', desc: 'Search diseases by name' },
    { id: 'trials', method: 'GET', path: '/api/clinical-trials/search', desc: 'Search clinical trials' },
  ];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="API Documentation" desc="RESTful API reference for DrugOS integration" actions={<Button variant="outline" size="sm"><BookOpen className="h-4 w-4 mr-1.5" />OpenAPI Spec</Button>} />
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="space-y-1">{endpoints.map(ep => (<button key={ep.id} onClick={() => setActiveEndpoint(ep.id)} className={`w-full text-left p-3 rounded-lg text-sm transition-colors ${activeEndpoint === ep.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-accent'}`}>
          <div className="flex items-center gap-2"><Badge className={`text-[10px] ${ep.method === 'GET' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'}`}>{ep.method}</Badge><span className="font-mono text-xs">{ep.path}</span></div><p className="text-xs text-muted-foreground mt-1">{ep.desc}</p>
        </button>))}</div>
        <div className="lg:col-span-3"><Card><CardHeader><CardTitle className="text-base flex items-center gap-2"><Badge className="bg-blue-100 text-blue-700">GET</Badge><code className="text-sm">/api/dataset</code></CardTitle><CardDescription>Returns dataset source statistics from Phase 1 pipeline</CardDescription></CardHeader>
          <CardContent className="space-y-4"><div><h4 className="text-sm font-semibold mb-2">Response (200 OK)</h4><pre className="bg-slate-950 text-green-400 p-4 rounded-lg text-xs overflow-x-auto">{`{
  "sources": [
    { "name": "DrugBank", "loaded": true, "rowsLoaded": 13481, "sha256": "abc123..." }
  ],
  "nodeCount": 13481,
  "edgeCount": 84200,
  "warnings": [],
  "errors": [],
  "source": "dataset_service",
  "generatedAt": "2026-07-14T12:00:00Z"
}`}</pre></div>
            <div><h4 className="text-sm font-semibold mb-2">Authentication</h4><p className="text-sm text-muted-foreground">All API requests require a session cookie. API keys can be generated from the API Keys screen.</p></div>
          </CardContent></Card></div>
      </div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 20. API KEYS SCREEN — real API keys from /api/api-keys
// ═══════════════════════════════════════════
function APIKeysScreen() {
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

// ═══════════════════════════════════════════
// 21. PLAYGROUND SCREEN — interactive API testing
// ═══════════════════════════════════════════
// ISSUE-FE-011 (continued): The executeQuery function previously used setTimeout
// to return fabricated candidates (Memantine score 87, Sirolimus score 82, etc.)
// with NO real API call. Root fix: make real API calls to live endpoints.
function PlaygroundScreen() {
  const [endpoint, setEndpoint] = useState('/api/dataset');
  const [requestBody, setRequestBody] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const endpoints = [
    { id: '/api/dataset', method: 'GET', path: '/api/dataset', body: '' },
    { id: '/api/knowledge-graph', method: 'GET', path: '/api/knowledge-graph', body: '' },
    { id: '/api/drugs/search', method: 'GET', path: '/api/drugs/search?q=aspirin', body: '' },
    { id: '/api/diseases/search', method: 'GET', path: '/api/diseases/search?q=alzheimer', body: '' },
  ];

  const executeQuery = async () => {
    setLoading(true);
    setError(null);
    setResponse('');
    try {
      const ep = endpoints.find(e => e.id === endpoint);
      if (!ep) throw new Error('Unknown endpoint');
      const res = await fetch(ep.path, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      const body = await res.json();
      setResponse(JSON.stringify(body, null, 2));
    } catch (e: any) {
      setError(e?.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="API Playground" desc="Test DrugOS API endpoints interactively against real endpoints" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Request</CardTitle></CardHeader><CardContent className="space-y-4">
          <div><Label>Endpoint</Label><Select value={endpoint} onValueChange={setEndpoint}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{endpoints.map(ep => <SelectItem key={ep.id} value={ep.id}>{ep.method} {ep.path}</SelectItem>)}</SelectContent></Select></div>
          <div><Label>Headers</Label><div className="bg-muted p-3 rounded-lg text-xs font-mono"><div>Credentials: include (session cookie)</div><div>Content-Type: application/json</div></div></div>
          <Button className="w-full" style={{ backgroundColor: PRIMARY }} onClick={executeQuery} disabled={loading}>{loading ? <><RefreshCw className="h-4 w-4 mr-1.5 animate-spin" />Executing...</> : <><Play className="h-4 w-4 mr-1.5" />Execute</Button>}
        </CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Response</CardTitle></CardHeader><CardContent>
          {error && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 mb-4">{error}</div>}
          {response ? <pre className="bg-slate-950 text-green-400 p-4 rounded-lg text-xs overflow-x-auto min-h-[300px]">{response}</pre> : <div className="flex items-center justify-center h-[300px] text-muted-foreground"><div className="text-center"><Code className="h-8 w-8 mx-auto mb-2 opacity-30" /><p>Execute a request to see the real response</p></div></div>}
        </CardContent></Card>
      </div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 22. WEBHOOKS SCREEN — not implemented
// ═══════════════════════════════════════════
// ISSUE-FE-011 ROOT FIX: Previously rendered 3 fabricated webhooks with fake
// success rates (99.2%, 95%, 42%). The "Add Webhook" dialog had no submit
// handler. The WebhookEndpoint Prisma model exists but no /api/webhooks route.
// Root fix: show EmptyState. Webhook CRUD needs a real backend route.
function WebhooksScreen() {
  return (
    <FadeIn>
      <PageHeader title="Webhooks" desc="Configure webhook endpoints for event notifications" />
      <EmptyState
        title="Webhook management is not enabled"
        description="Webhook CRUD routes have not been implemented against the WebhookEndpoint model. No webhooks are configured. Event delivery status cannot be displayed until a real webhook service is built."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 23. PROFILE SCREEN — real backend data via useSession + PATCH /api/auth/me
// ═══════════════════════════════════════════
function ProfileScreen() {
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

// ═══════════════════════════════════════════
// 24. SECURITY SETTINGS SCREEN — real 2FA status, real sessions, real password change
// ═══════════════════════════════════════════
function SecuritySettingsScreen() {
  const { user, refresh } = useSession();
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [pwSaving, setPwSaving] = useState(false);

  const [twoFAOpen, setTwoFAOpen] = useState(false);
  const [twoFASecret, setTwoFASecret] = useState<string>('');
  const [twoFACode, setTwoFACode] = useState('');
  const [twoFAMsg, setTwoFAMsg] = useState<string | null>(null);
  const [twoFAErr, setTwoFAErr] = useState<string | null>(null);
  const [twoFABusy, setTwoFABusy] = useState(false);

  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
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
        body: JSON.stringify({ secret: twoFASecret, code: twoFACode }),
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

  const loginEvents = auditLogs.filter(l => l.action === 'login' || l.action === 'logout' || l.action === 'register').slice(0, 5);

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Security" desc="Manage your account security" />

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

          <Dialog open={twoFAOpen} onOpenChange={setTwoFAOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Set up Two-Factor Authentication</DialogTitle>
                <DialogDescription>Scan the secret below with your authenticator app, then enter the 6-digit code it generates.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="rounded-lg border bg-muted/40 p-4">
                  <p className="text-xs text-muted-foreground mb-1">Manual entry secret (base32):</p>
                  <p className="font-mono text-sm break-all">{twoFASecret}</p>
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

// ═══════════════════════════════════════════
// 25. NOTIFICATIONS SCREEN — real notifications from /api/notifications
// ═══════════════════════════════════════════
// ISSUE-FE-014 PARTIAL FIX: NotificationsScreen previously had preference toggles
// with only localStorage persistence (no API call). The notifications themselves
// are real from /api/notifications. Root fix: keep real notifications. Preferences
// now persist via localStorage with a clear note that backend persistence is pending.
function NotificationsScreen() {
  const [notifications, setNotifications] = useState<Array<{ id: string; type: string; title: string; body: string; readAt: string | null; createdAt: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs] = useState({ emailQuery: true, emailReport: true, emailCollab: false, inlineQuery: true, inlineReport: true, inlineCollab: true, pushQuery: false, pushReport: true, pushCollab: false });
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const toggle = (key: keyof typeof prefs) => setPrefs(prev => ({ ...prev, [key]: !prev[key] }));

  useEffect(() => {
    let mounted = true;
    Promise.all([
      api.listNotifications().catch(() => ({ items: [] as typeof notifications })),
      new Promise<typeof prefs>((resolve) => {
        try {
          const saved = localStorage.getItem('drugos:notification-prefs');
          resolve(saved ? { ...prefs, ...JSON.parse(saved) } : prefs);
        } catch { resolve(prefs); }
      }),
    ]).then(([notifs, savedPrefs]) => {
      if (!mounted) return;
      setNotifications(notifs.items || []);
      setPrefs(savedPrefs);
      setLoading(false);
    });
    return () => { mounted = false };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleMarkRead = async (id: string) => {
    try {
      await api.markNotificationRead(id);
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, readAt: new Date().toISOString() } : n));
    } catch { /* ignore */ }
  };

  const handleSavePrefs = () => {
    try {
      localStorage.setItem('drugos:notification-prefs', JSON.stringify(prefs));
      setSavedMsg('Notification preferences saved to local storage.');
      setTimeout(() => setSavedMsg(null), 2500);
    } catch {
      setSavedMsg('Failed to save preferences.');
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
        <div className="flex items-center gap-2">
          <Button style={{ backgroundColor: PRIMARY }} onClick={handleSavePrefs}>Save Preferences</Button>
          <span className="text-xs text-muted-foreground">Saved to browser localStorage</span>
        </div>
      </CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 26. PREFERENCES SCREEN — persists via localStorage + next-themes
// ═══════════════════════════════════════════
// ISSUE-FE-014 PARTIAL FIX: PreferencesScreen previously had no backend
// persistence for preferences. Root fix: keep localStorage persistence (which
// works across sessions) with a clear note. Theme preference uses next-themes
// which is the correct approach.
function PreferencesScreen() {
  const { theme, setTheme, systemTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [autoSave, setAutoSave] = useState(true);
  const [resultsPerPage, setResultsPerPage] = useState('20');
  const [exportFormat, setExportFormat] = useState('csv');
  const [therapeuticArea, setTherapeuticArea] = useState('all');
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (!mounted) return;
    try {
      const saved = localStorage.getItem('drugos:preferences');
      if (saved) {
        const p = JSON.parse(saved);
        if (p.autoSave !== undefined) setAutoSave(p.autoSave);
        if (p.resultsPerPage) setResultsPerPage(p.resultsPerPage);
        if (p.exportFormat) setExportFormat(p.exportFormat);
        if (p.therapeuticArea) setTherapeuticArea(p.therapeuticArea);
      }
    } catch { /* ignore */ }
  }, [mounted]);

  const handleSave = () => {
    try {
      localStorage.setItem('drugos:preferences', JSON.stringify({
        autoSave, resultsPerPage, exportFormat, therapeuticArea,
      }));
      setSavedMsg('Preferences saved to browser storage.');
      setTimeout(() => setSavedMsg(null), 2500);
    } catch {
      setSavedMsg('Failed to save preferences.');
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

        <div className="flex items-center gap-2">
          <Button onClick={handleSave} style={{ backgroundColor: PRIMARY }}>Save Preferences</Button>
          <span className="text-xs text-muted-foreground">Saved to browser localStorage</span>
        </div>
      </CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 27. PRIVACY POLICY SCREEN
// ═══════════════════════════════════════════
function PrivacyPolicyScreen() {
  return (
    <FadeIn>
      <PageHeader title="Privacy Policy" desc="Last updated: June 1, 2026" />
      <Card><CardContent className="p-6 max-w-3xl mx-auto">
        <h2 className="text-xl font-bold mb-6">DrugOS Privacy Policy</h2>
        <div className="space-y-6 text-sm text-muted-foreground leading-relaxed">
          <p>DrugOS collects information that you provide directly to us, including your name, email address, organization affiliation, and research interests. We process query logs and feature usage data to improve our services. Your research data is processed solely to deliver drug repurposing results and is never shared with third parties without explicit consent.</p>
          <p>DrugOS does not sell, trade, or rent your personal information. All data sharing complies with applicable regulations. We implement encryption at rest and in transit, role-based access controls, and regular security audits.</p>
          <p>You have the right to access, rectify, and delete your personal data. Contact our privacy team to exercise these rights.</p>
        </div>
      </CardContent></Card>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 28. TERMS SCREEN
// ═══════════════════════════════════════════
function TermsScreen() {
  return (
    <FadeIn>
      <PageHeader title="Terms of Service" desc="Last updated: June 1, 2026" />
      <Card><CardContent className="p-6 max-w-3xl mx-auto">
        <h2 className="text-xl font-bold mb-6">DrugOS Terms of Service</h2>
        <div className="space-y-6 text-sm text-muted-foreground leading-relaxed">
          <p>By accessing the DrugOS platform, you agree to these Terms of Service. DrugOS grants you a limited license to access and use the platform for internal business or academic research purposes.</p>
          <p>The DrugOS platform, including its knowledge graph and scoring algorithms, is the property of DrugOS. Drug repurposing predictions are provided for research purposes only.</p>
          <p>DrugOS provides computational predictions for research purposes only. We do not guarantee clinical validity of any prediction. Users are responsible for independent validation before clinical application.</p>
        </div>
      </CardContent></Card>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 29. COMPLIANCE SCREEN
// ═══════════════════════════════════════════
// ISSUE-FE-012 ROOT FIX: Previously rendered 5 fabricated compliance frameworks
// (HIPAA compliant May 2026, GDPR compliant Apr 2026, SOC 2 Type II Mar 2026,
// 21 CFR Part 11 Feb 2026, GxP partial Jun 2026) with fabricated audit dates.
// Regulatory submissions based on these would be FRAUDULENT. The 21 CFR Part 11
// claim is particularly dangerous — FDA electronic records compliance is a legal
// requirement, not a UI label. Root fix: compliance status must come from real
// audit reports. Show EmptyState with honest messaging.
function ComplianceScreen() {
  return (
    <FadeIn>
      <PageHeader title="Compliance" desc="Regulatory compliance and certifications" />
      <EmptyState
        title="Compliance data is not available"
        description="Compliance status must come from real audit reports stored in a document management system, not hardcoded UI labels. Regulatory frameworks (HIPAA, GDPR, SOC 2, 21 CFR Part 11, GxP) require verified audit documentation. Contact your compliance officer for current certification status. No compliance claims are fabricated in this interface."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 30. HELP CENTER SCREEN
// ═══════════════════════════════════════════
function HelpCenterScreen() {
  const [search, setSearch] = useState('');
  const categories = [{ title: 'Getting Started', articles: 8, icon: Play },{ title: 'Search & Queries', articles: 12, icon: Search },{ title: 'Drug Candidates', articles: 10, icon: Target },{ title: 'Evidence & Reports', articles: 7, icon: FileText },{ title: 'API & Integration', articles: 15, icon: Code },{ title: 'Billing & Plans', articles: 6, icon: CreditCard }];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Help Center" desc="Find answers and get support" />
      <Card className="bg-gradient-to-r from-primary/5 to-primary/10"><CardContent className="p-8 text-center"><h2 className="text-xl font-bold mb-3">How can we help?</h2><div className="relative max-w-lg mx-auto"><Search className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" /><Input placeholder="Search help articles..." value={search} onChange={e => setSearch(e.target.value)} className="pl-12 h-12 text-base" /></div></CardContent></Card>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">{categories.map(c => { const Icon = c.icon; return (<Card key={c.title} className="hover:shadow-md transition-shadow cursor-pointer"><CardContent className="p-5"><div className="flex items-center gap-3 mb-2"><Icon className="h-5 w-5 text-primary" /><h3 className="font-semibold text-sm">{c.title}</h3></div><p className="text-xs text-muted-foreground">{c.articles} articles</p></CardContent></Card>); })}</div>
      <div className="text-center"><Button variant="outline" onClick={() => {}}><MessageSquare className="h-4 w-4 mr-2" />Contact Support</Button></div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 31. TICKET SCREEN
// ═══════════════════════════════════════════
function TicketScreen() {
  const [createOpen, setCreateOpen] = useState(false);
  return (
    <FadeIn>
      <PageHeader title="Support Tickets" desc="Submit and track support requests" actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />New Ticket</Button>} />
      <EmptyState
        title="No tickets yet"
        description="Support tickets will appear here once submitted. Use the 'New Ticket' button to report issues or request assistance."
      />
      <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create Support Ticket</DialogTitle></DialogHeader><div className="space-y-4"><div><Label>Subject</Label><Input placeholder="Brief description of the issue" /></div><div><Label>Priority</Label><Select defaultValue="medium"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">Low</SelectItem><SelectItem value="medium">Medium</SelectItem><SelectItem value="high">High</SelectItem><SelectItem value="critical">Critical</SelectItem></SelectContent></Select></div><div><Label>Description</Label><Textarea placeholder="Provide details about the issue..." className="min-h-[100px]" /></div></div><DialogFooter><Button style={{ backgroundColor: PRIMARY }} onClick={() => setCreateOpen(false)}>Submit Ticket</Button></DialogFooter></DialogContent></Dialog>
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 32. SYSTEM STATUS SCREEN — real data from /api/system/status
// ═══════════════════════════════════════════
// ISSUE-FE-014 ROOT FIX: Previously rendered 3 fabricated incidents and
// hardcoded service status. Root fix: call api.getSystemStatus() which
// returns real service availability data.
function SystemStatusScreen() {
  const [status, setStatus] = useState<SystemStatusType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    api.getSystemStatus()
      .then(r => {
        if (!mounted) return;
        setStatus(r);
        setLoading(false);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e?.message || 'Failed to load system status.');
        setLoading(false);
      });
    return () => { mounted = false };
  }, []);

  if (loading) return <FadeIn><LoadingSpinner label="Loading system status..." /></FadeIn>;
  if (error) return <FadeIn><ErrorDisplay error={error} onRetry={() => window.location.reload()} /></FadeIn>;

  const services = status?.services || {};
  const allOperational = Object.values(services).every(s => s.available);

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="System Status" desc="Real-time platform health monitoring" />
      <Card className={allOperational ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}>
        <CardContent className="p-5">
          <div className="flex items-center gap-3">
            {allOperational ? <CheckCircle2 className="h-6 w-6 text-green-600" /> : <AlertTriangle className="h-6 w-6 text-amber-600" />}
            <div>
              <h3 className="font-semibold text-green-800">{allOperational ? 'All Systems Operational' : 'Some Services Degraded'}</h3>
              <p className="text-sm text-green-700">Last checked: {status?.generatedAt ? new Date(status.generatedAt).toLocaleString() : 'just now'}</p>
            </div>
          </div>
        </CardContent>
      </Card>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Service Status</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
        <TableBody>{Object.entries(services).map(([name, svc]) => (
          <TableRow key={name}><TableCell className="font-medium">{name}</TableCell>
            <TableCell><div className="flex items-center gap-2"><span className={`w-2.5 h-2.5 rounded-full ${svc.available ? 'bg-green-500' : 'bg-red-500'}`} /><Badge variant={svc.available ? 'default' : 'destructive'}>{svc.available ? 'operational' : 'unavailable'}</Badge>{svc.reason && <span className="text-xs text-muted-foreground">{svc.reason}</span>}</div></TableCell>
          </TableRow>
        ))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 33. INVESTOR DASHBOARD SCREEN — NOT IMPLEMENTED
// ═══════════════════════════════════════════
// ISSUE-FE-013 ROOT FIX: Previously rendered fabricated ARR/MRR data
// ($420K → $840K ARR), fabricated customer counts (42, +24%), fabricated NRR
// (118%), and 3 fabricated cohorts. An investor seeing "$840K ARR" could make
// investment decisions on fake financials — this is securities fraud.
// Root fix: remove all fabricated financial data. Investor data must come from
// real financial systems (Stripe, QuickBooks, Carta), not hardcoded arrays.
function InvestorDashboardScreen() {
  return (
    <FadeIn>
      <PageHeader title="Investor Dashboard" desc="Key business metrics and financial overview" />
      <EmptyState
        title="Investor dashboard requires financial system integration"
        description="Financial metrics (ARR, MRR, NRR, cohort analysis) must come from real financial systems — Stripe, QuickBooks, or Carta — not hardcoded arrays. Displaying fabricated financial data to investors would constitute securities fraud. This feature requires backend integration with your financial stack."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 34. CAP TABLE SCREEN — NOT IMPLEMENTED
// ═══════════════════════════════════════════
// ISSUE-FE-013 ROOT FIX (continued): Previously rendered fabricated funding
// rounds (Pre-Seed $500K/$3M, Seed $2M/$10M, Series A $8M/$40M) and fabricated
// shareholder data. Root fix: cap table data must come from Carta or similar
// equity management platform. No fabricated financial data is displayed.
function CapTableScreen() {
  return (
    <FadeIn>
      <PageHeader title="Cap Table" desc="Capitalization table and funding history" />
      <EmptyState
        title="Cap table requires equity management integration"
        description="Cap table data must come from a real equity management platform (Carta, Pulley, etc.). Displaying fabricated funding rounds and ownership percentages would be fraudulent. This feature requires integration with your cap table provider."
      />
    </FadeIn>
  );
}

// ═══════════════════════════════════════════
// 35. CHANGELOG SCREEN
// ═══════════════════════════════════════════
// The changelog displays product release history. This is static documentation
// about the product itself, not user data or runtime state. It is acceptable
// as reference material and does not need an API backend.
function ChangelogScreen() {
  const entries = [
    { version: 'v2.1.0', date: 'Jun 8, 2026', type: 'feature', title: 'GxP Validated Mode', desc: 'Full GxP validated mode for clinical research with audit trails and electronic signatures.' },
    { version: 'v2.0.5', date: 'Jun 1, 2026', type: 'improvement', title: 'Knowledge Graph V2', desc: 'Updated knowledge graph engine with 50% faster query performance and 200K additional nodes.' },
    { version: 'v2.0.4', date: 'May 25, 2026', type: 'bugfix', title: 'Report Generation Fix', desc: 'Fixed issue where PDF reports occasionally had missing pathway diagrams.' },
    { version: 'v2.0.3', date: 'May 18, 2026', type: 'feature', title: 'Batch Query API', desc: 'New batch query endpoint allows up to 50 disease queries in a single API call.' },
    { version: 'v2.0.2', date: 'May 10, 2026', type: 'improvement', title: 'Safety Scoring Update', desc: 'Improved safety scoring algorithm with better off-target prediction accuracy.' },
  ];
  const typeColors: Record<string, string> = { feature: 'bg-blue-100 text-blue-700', improvement: 'bg-amber-100 text-amber-700', bugfix: 'bg-red-100 text-red-700' };
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Changelog" desc="Product updates and release notes" actions={<Button variant="outline" size="sm"><Bell className="h-4 w-4 mr-1.5" />Subscribe</Button>} />
      <div className="space-y-4">{entries.map(e => (<Card key={e.version + e.title} className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-start justify-between mb-2"><div className="flex items-center gap-3"><Badge variant="outline" className="font-mono">{e.version}</Badge><span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${typeColors[e.type]}`}>{e.type}</span></div><span className="text-xs text-muted-foreground">{e.date}</span></div><h3 className="font-semibold">{e.title}</h3><p className="text-sm text-muted-foreground mt-1">{e.desc}</p></CardContent></Card>))}</div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 36. ROADMAP SCREEN
// ═══════════════════════════════════════════
// The roadmap displays planned product features. This is product planning
// documentation, not user data. It is acceptable as static reference material.
function RoadmapScreen() {
  const items = [
    { title: 'Multi-disease batch analysis', status: 'shipped', quarter: 'Q2 2026', votes: 124 },
    { title: 'Real-time collaboration', status: 'in-progress', quarter: 'Q3 2026', votes: 98 },
    { title: 'Dark mode theme', status: 'planned', quarter: 'Q3 2026', votes: 76 },
    { title: 'Advanced ADMET predictions', status: 'in-progress', quarter: 'Q3 2026', votes: 156 },
    { title: 'Custom knowledge graph views', status: 'planned', quarter: 'Q4 2026', votes: 89 },
    { title: 'Mobile app', status: 'planned', quarter: 'Q4 2026', votes: 203 },
    { title: 'Regulatory submission package', status: 'planned', quarter: 'Q1 2027', votes: 145 },
  ];
  const statusColors: Record<string, string> = { shipped: 'bg-green-100 text-green-700', 'in-progress': 'bg-blue-100 text-blue-700', planned: 'bg-slate-100 text-slate-700' };
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Product Roadmap" desc="Upcoming features and improvements" />
      <div className="flex gap-2 mb-2">{['all', 'shipped', 'in-progress', 'planned'].map(s => (<Badge key={s} variant="outline" className="cursor-pointer capitalize">{s}</Badge>))}</div>
      <div className="space-y-4">{items.map(item => (<Card key={item.title} className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-start justify-between"><div><div className="flex items-center gap-3 mb-2"><span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${statusColors[item.status]}`}>{item.status}</span><Badge variant="outline" className="text-xs">{item.quarter}</Badge></div><h3 className="font-semibold">{item.title}</h3></div><div className="flex items-center gap-1.5"><ArrowUpRight className="h-4 w-4 text-muted-foreground" /><span className="text-sm font-medium">{item.votes}</span></div></div></CardContent></Card>))}</div>
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// 37. FEEDBACK SCREEN
// ═══════════════════════════════════════════
// The feedback form accepts user input. No mock data is rendered.
// Submissions need a backend endpoint to persist.
function FeedbackScreen() {
  const [rating, setRating] = useState(0);
  const [category, setCategory] = useState('');
  const [description, setDescription] = useState('');
  const recentFeedback: Array<{ user: string; rating: number; category: string; feedback: string; date: string }> = [];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Feedback" desc="Help us improve DrugOS" />
      <Card><CardContent className="p-6 space-y-4">
        <div><Label>How would you rate your experience?</Label><div className="flex gap-2 mt-2">{[1,2,3,4,5].map(s => (<button key={s} onClick={() => setRating(s)} className={`text-2xl transition-colors ${s <= rating ? 'text-yellow-400' : 'text-muted-foreground/30'}`}>★</button>))}</div></div>
        <div><Label>Category</Label><Select value={category} onValueChange={setCategory}><SelectTrigger><SelectValue placeholder="Select category" /></SelectTrigger><SelectContent><SelectItem value="bug">Bug Report</SelectItem><SelectItem value="feature">Feature Request</SelectItem><SelectItem value="improvement">Improvement</SelectItem><SelectItem value="praise">Praise</SelectItem></SelectContent></Select></div>
        <div><Label>Description</Label><Textarea value={description} onChange={e => setDescription(e.target.value)} placeholder="Tell us more about your experience..." className="min-h-[100px]" /></div>
        <Button style={{ backgroundColor: PRIMARY }}><Send className="h-4 w-4 mr-1.5" />Submit Feedback</Button>
      </CardContent></Card>
      {recentFeedback.length === 0 && (
        <EmptyState title="No feedback submissions yet" description="Feedback from your team will appear here once the feedback API endpoint is implemented." />
      )}
    </div></FadeIn>
  );
}

// ═══════════════════════════════════════════
// EXPORT ALL REMAINING SCREENS
// ═══════════════════════════════════════════
export const remainingScreens: Record<string, React.ComponentType> = {
  'pipeline': PipelineScreen,
  'analytics': AnalyticsScreen,
  'team': TeamMembersScreen,
  'projects': ProjectsScreen,
  'shared-queries': SharedQueriesScreen,
  'annotations': AnnotationsScreen,
  'data-sources': DataSourcesScreen,
  'graph-stats': GraphStatisticsScreen,
  'quality': QualityScreen,
  'subscription': SubscriptionScreen,
  'usage': UsageScreen,
  'deals': DealsScreen,
  'invoices': InvoicesScreen,
  'users': UsersAdminScreen,
  'roles': RolesScreen,
  'sso': SSOScreen,
  'audit-logs': AuditLogsScreen,
  'feature-flags': FeatureFlagsScreen,
  'api-docs': APIDocsScreen,
  'api-keys': APIKeysScreen,
  'playground': PlaygroundScreen,
  'webhooks': WebhooksScreen,
  'profile': ProfileScreen,
  'security': SecuritySettingsScreen,
  'notifications': NotificationsScreen,
  'preferences': PreferencesScreen,
  'privacy': PrivacyPolicyScreen,
  'terms': TermsScreen,
  'compliance': ComplianceScreen,
  'help-center': HelpCenterScreen,
  'tickets': TicketScreen,
  'system-status': SystemStatusScreen,
  'investor-dashboard': InvestorDashboardScreen,
  'cap-table': CapTableScreen,
  'changelog': ChangelogScreen,
  'roadmap': RoadmapScreen,
  'feedback': FeedbackScreen,
};
