'use client'

import React, { useState, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Search, Shield, Database, Users, CreditCard, Settings, HelpCircle, ChevronDown, ChevronRight,
  Bell, Menu, X, TrendingUp, Scale, Code, Globe, Lock, LayoutDashboard, FileQuestion, Plus,
  Download, Star, Check, AlertTriangle, Activity, Eye, Filter, ArrowRight,
  ExternalLink, Zap, BarChart3, GitBranch, FileText, FolderOpen, Key, BookOpen,
  Heart, Briefcase, Mail, MapPin, Phone, Clock, Calendar, Tag, Layers, Cpu, GitCommit,
  RefreshCw, AlertCircle, CheckCircle2, XCircle, Info, MoreHorizontal, PlusCircle,
  Edit, Trash2, Share2, Bookmark, Copy, User, Building,
  FlaskConical, Network, Target, Play, MessageSquare, Award, ShieldCheck, Server,
  FileCheck, Handshake, Microscope, Beaker, Atom, GitFork, Columns3, FolderKanban,
  DollarSign, Percent, Palette, Plug, ToggleRight, Cookie, Archive,
  BookMarked, Smartphone, MonitorSmartphone, Headphones, Ticket, Library, MessageCircle, Lightbulb,
  RotateCcw, ShieldAlert, LogIn, UserCheck, Hourglass, Siren, Pill,
  Calculator, Gauge, Workflow, ShoppingCart, FolderLock, KeyRound, ScanSearch,
  Upload, Save, Send, Webhook, Braces, Settings2, LogOut, ArrowUpRight, ArrowDownRight,
  Minus, CircleDot, Workflow as WorkflowIcon, LineChart as LineChartIcon
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  PieChart, Pie, Cell, LineChart, Line, AreaChart, Area, RadarChart, Radar,
  PolarGrid, PolarAngleAxis, PolarRadiusAxis, Legend
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from '@/components/ui/table'
// FE-026 ROOT FIX: All data exports from mock-data.ts are now EMPTY arrays.
// Components render empty states until migrated to real API calls.
// FE-034 ROOT FIX: `mock-data.ts` deleted (dangerous name). Empty defaults
// now live in `@/lib/empty-defaults`. Type imports come from `@/lib/types`.
import {
  diseases, drugCandidates, clinicalTrials, users, auditLogs, subscriptionPlans, billingHistory, apiKeys,
  webhooks, usageMetrics, dataSources, dealPipeline, organization, featureFlags, systemStatus, savedQueries
} from '@/lib/empty-defaults'
import { useSession } from './session-provider'
import { api, type TeamMember, type AdminUser, type AuditLog } from '@/lib/api-client'
import { roleLabel, canAccessSection } from '@/lib/rbac'
// FE-009 ROOT FIX: real-API hooks for admin/dashboard screens + DemoDataBanner
// for screens whose backend is not yet implemented.
import {
  useApiList,
  useApiResource,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  DemoDataBanner,
} from './use-api-data'

const P = '#5B4FCF'
const G = '#1D9E75'
const O = '#D4853A'
const R = '#C0392B'
const CC = ['#5B4FCF', '#1D9E75', '#D4853A', '#C0392B', '#8B5CF6', '#06B6D4']

function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay }}>{children}</motion.div>
}

function PH({ title, desc, actions }: { title: string; desc?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-6">
      <div><h1 className="text-2xl font-bold text-foreground">{title}</h1>{desc && <p className="text-sm text-muted-foreground mt-1">{desc}</p>}</div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
    </div>
  )
}

function SC({ title, value, subtitle, icon: Icon, trend }: { title: string; value: string | number; subtitle?: string; icon?: React.ComponentType<{className?:string}>; trend?: string }) {
  return (
    <Card className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-start justify-between"><div>
      <p className="text-sm text-muted-foreground">{title}</p><p className="text-2xl font-bold text-foreground mt-1">{value}</p>
      {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
      {trend && <p className={`text-xs mt-1 font-medium ${trend.startsWith('+') ? 'text-emerald-600' : 'text-red-500'}`}>{trend}</p>}
    </div>{Icon && <div className="h-10 w-10 rounded-lg bg-[#5B4FCF]/10 flex items-center justify-center"><Icon className="h-5 w-5 text-[#5B4FCF]" /></div>}</div></CardContent></Card>
  )
}

// ═══════════════════════════════════════════
// PIPELINE SCREEN
// ═══════════════════════════════════════════
function PipelineScreen() {
  const [filter, setFilter] = useState('all')
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/pipeline endpoint
  // exists yet. The previous code rendered 8 hardcoded candidates
  // (Memantine/Huntington's Phase II, etc.) as if they were real
  // wet-lab-tracked pipeline candidates — a pharma researcher could
  // mistake these for real clinical-trial entries.
  const stages = [
    { name: 'Discovery', count: 142, color: P },
    { name: 'Preclinical', count: 48, color: '#8B5CF6' },
    { name: 'Phase I', count: 22, color: O },
    { name: 'Phase II', count: 14, color: '#06B6D4' },
    { name: 'Phase III', count: 6, color: G },
    { name: 'Approved', count: 3, color: '#10B981' },
  ]
  const total = stages.reduce((s, x) => s + x.count, 0)
  const pipelineData = stages.map(s => ({ name: s.name, count: s.count, fill: s.color }))
  const pipelineItems = [
    { drug: 'Memantine', disease: "Huntington's", stage: 'Phase II', score: 87, safety: 'green' },
    { drug: 'Sirolimus', disease: 'ALS', stage: 'Phase I', score: 82, safety: 'green' },
    { drug: 'Metformin', disease: 'Glioblastoma', stage: 'Preclinical', score: 79, safety: 'yellow' },
    { drug: 'Dasatinib', disease: "Alzheimer's", stage: 'Discovery', score: 74, safety: 'yellow' },
    { drug: 'Naltrexone', disease: 'MS', stage: 'Phase III', score: 91, safety: 'green' },
    { drug: 'Ivermectin', disease: 'Breast Cancer', stage: 'Phase I', score: 68, safety: 'red' },
    { drug: 'Disulfiram', disease: 'Glioblastoma', stage: 'Phase II', score: 85, safety: 'yellow' },
    { drug: 'Propranolol', disease: 'Pancreatic', stage: 'Discovery', score: 62, safety: 'green' },
  ]
  const filtered = filter === 'all' ? pipelineItems : pipelineItems.filter(i => i.stage === filter)
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Repurposing Pipeline" />
      <PH title="Repurposing Pipeline" desc="Track drug candidates through the repurposing pipeline" actions={<Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button>} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {stages.map(s => (<Card key={s.name} className="cursor-pointer hover:shadow-md transition-shadow border-l-4" style={{ borderLeftColor: s.color }} onClick={() => setFilter(filter === s.name ? 'all' : s.name)}>
          <CardContent className="p-4"><p className="text-xs text-muted-foreground">{s.name}</p><p className="text-2xl font-bold mt-1">{s.count}</p><p className="text-xs text-muted-foreground">{Math.round(s.count/total*100)}%</p></CardContent>
        </Card>))}
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Pipeline Funnel</CardTitle></CardHeader>
        <CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><BarChart data={pipelineData} layout="vertical"><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" /><YAxis dataKey="name" type="category" width={90} /><RechartsTooltip /><Bar dataKey="count" radius={[0, 4, 4, 0]}>{pipelineData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}</Bar></BarChart></ResponsiveContainer></div></CardContent>
      </Card>
      <div className="flex items-center gap-2 flex-wrap mb-2"><Badge variant={filter === 'all' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('all')}>All</Badge>{stages.map(s => <Badge key={s.name} variant={filter === s.name ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter(s.name)}>{s.name} ({s.count})</Badge>)}</div>
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Drug</TableHead><TableHead>Disease</TableHead><TableHead>Stage</TableHead><TableHead>Score</TableHead><TableHead>Safety</TableHead></TableRow></TableHeader>
        <TableBody>{filtered.map((item, i) => (<TableRow key={i} className="cursor-pointer hover:bg-muted/30"><TableCell className="font-medium">{item.drug}</TableCell><TableCell>{item.disease}</TableCell>
          <TableCell><Badge variant="outline">{item.stage}</Badge></TableCell><TableCell><span className="font-bold" style={{ color: item.score >= 80 ? G : item.score >= 60 ? O : R }}>{item.score}</span></TableCell>
          <TableCell><Badge variant={item.safety === 'green' ? 'default' : item.safety === 'yellow' ? 'secondary' : 'destructive'} className="text-xs">{item.safety === 'green' ? 'Safe' : item.safety === 'yellow' ? 'Caution' : 'Risk'}</Badge></TableCell>
        </TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// ANALYTICS SCREEN
// ═══════════════════════════════════════════
function AnalyticsScreen() {
  const [timeRange, setTimeRange] = useState('6m')
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/analytics endpoint
  // exists yet. The previous code rendered 6 months of fabricated query
  // volumes and API call counts as if they were real usage telemetry.
  const queryData = [{ month: 'Jan', queries: 180, api: 22000 },{ month: 'Feb', queries: 220, api: 28000 },{ month: 'Mar', queries: 290, api: 35000 },{ month: 'Apr', queries: 310, api: 38000 },{ month: 'May', queries: 340, api: 42000 },{ month: 'Jun', queries: 342, api: 45230 }]
  const topDiseases = [{ name: "Huntington's", queries: 342, growth: '+24%' },{ name: "Alzheimer's", queries: 289, growth: '+18%' },{ name: 'Glioblastoma', queries: 234, growth: '+31%' },{ name: 'ALS', queries: 198, growth: '+12%' },{ name: 'MS', queries: 167, growth: '+8%' }]
  const successData = [{ name: 'Discovery', value: 142 },{ name: 'Preclinical', value: 48 },{ name: 'Clinical', value: 42 },{ name: 'Approved', value: 3 }]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Analytics" />
      <PH title="Analytics" desc="Platform usage and performance metrics" actions={<Select value={timeRange} onValueChange={setTimeRange}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1m">1 Month</SelectItem><SelectItem value="3m">3 Months</SelectItem><SelectItem value="6m">6 Months</SelectItem><SelectItem value="1y">1 Year</SelectItem></SelectContent></Select>} />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <SC title="Total Queries" value="1,682" icon={Search} trend="+24%" />
        <SC title="API Calls" value="210,230" icon={Code} trend="+18%" />
        <SC title="Candidates Found" value="2,345" icon={Target} trend="+31%" />
        <SC title="Avg Score" value="73.4" icon={BarChart3} trend="+5%" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Query Volume</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><AreaChart data={queryData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="month" /><YAxis /><RechartsTooltip /><Area type="monotone" dataKey="queries" stroke={P} fill={`${P}20`} /></AreaChart></ResponsiveContainer></div></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Pipeline Distribution</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={successData} cx="50%" cy="50%" innerRadius={60} outerRadius={90} paddingAngle={4} dataKey="value">{successData.map((_, i) => <Cell key={i} fill={CC[i]} />)}</Pie><RechartsTooltip /><Legend /></PieChart></ResponsiveContainer></div></CardContent></Card>
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Top Searched Diseases</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Disease</TableHead><TableHead>Queries</TableHead><TableHead>Growth</TableHead></TableRow></TableHeader>
        <TableBody>{topDiseases.map(d => (<TableRow key={d.name}><TableCell className="font-medium">{d.name}</TableCell><TableCell>{d.queries}</TableCell><TableCell><span className="text-emerald-600 font-medium">{d.growth}</span></TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// TEAM MEMBERS SCREEN
// ═══════════════════════════════════════════
function TeamMembersScreen() {
  // Real backend-connected Team Members screen.
  // Fetches /api/team on mount and renders the actual organization members.
  const [search, setSearch] = useState('')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [members, setMembers] = useState<TeamMember[]>([])
  const [loading, setLoading] = useState(true)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const { user } = useSession()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.listTeamMembers()
      .then(res => {
        if (!cancelled) {
          setMembers(res.items)
          setErrorMsg(null)
        }
      })
      .catch(e => {
        if (!cancelled) setErrorMsg(e?.message || 'Failed to load team members.')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const filtered = members.filter(m =>
    m.name.toLowerCase().includes(search.toLowerCase()) ||
    m.email.toLowerCase().includes(search.toLowerCase())
  )

  const formatLastActive = (iso: string | null) => {
    if (!iso) return 'Never'
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'Just now'
    if (mins < 60) return `${mins} min ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs} hr ago`
    const days = Math.floor(hrs / 24)
    return `${days} day${days > 1 ? 's' : ''} ago`
  }

  const initials = (name: string) => name.split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase()).join('') || '?'

  return (
    <FadeIn><div className="space-y-6">
      <PH title="Team Members" desc={`${members.length} member${members.length === 1 ? '' : 's'} in your organization`} actions={<>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input placeholder="Search members..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 w-56" />
        </div>
        <Button style={{ backgroundColor: P }} onClick={() => setInviteOpen(true)}>
          <Plus className="h-4 w-4 mr-1.5" />Invite Member
        </Button>
      </>} />

      {errorMsg && (
        <div className="rounded-md bg-[#C0392B]/10 border border-[#C0392B]/30 text-[#C0392B] text-sm px-3 py-2">{errorMsg}</div>
      )}

      {loading ? (
        <Card><CardContent className="p-8 text-center text-muted-foreground">Loading team members…</CardContent></Card>
      ) : filtered.length === 0 ? (
        <Card><CardContent className="p-8 text-center text-muted-foreground">
          {members.length === 0
            ? 'No team members yet. Invite colleagues to collaborate.'
            : 'No members match your search.'}
        </CardContent></Card>
      ) : (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow>
          <TableHead>Member</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead>Last Active</TableHead><TableHead></TableHead>
        </TableRow></TableHeader>
          <TableBody>{filtered.map(m => (
            <TableRow key={m.id}>
              <TableCell>
                <div className="flex items-center gap-3">
                  <Avatar className="h-8 w-8"><AvatarFallback className="bg-[#5B4FCF]/10 text-[#5B4FCF] text-xs">{initials(m.name)}</AvatarFallback></Avatar>
                  <div>
                    <p className="font-medium text-sm">{m.name}{m.id === user?.id && <span className="ml-2 text-[10px] text-muted-foreground">(you)</span>}</p>
                    <p className="text-xs text-muted-foreground">{m.email}</p>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="secondary" className="capitalize">{roleLabel(m.role)}</Badge>
                {m.orgRole === 'owner' && <Badge variant="outline" className="ml-1 text-[10px]">Owner</Badge>}
              </TableCell>
              <TableCell>
                <Badge variant={m.status === 'active' ? 'default' : 'outline'} className={m.status === 'active' ? 'bg-[#1D9E75]' : ''}>{m.status}</Badge>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{formatLastActive(m.lastLoginAt)}</TableCell>
              <TableCell><Button variant="ghost" size="sm" className="h-7 w-7 p-0"><MoreHorizontal className="h-4 w-4" /></Button></TableCell>
            </TableRow>
          ))}</TableBody>
        </Table></CardContent></Card>
      )}

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}><DialogContent><DialogHeader><DialogTitle>Invite Team Member</DialogTitle><DialogDescription>Send an invitation to join your DrugOS workspace</DialogDescription></DialogHeader>
        <div className="space-y-4"><div><Label>Email Address</Label><Input placeholder="colleague@company.com" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} /></div>
        <div><Label>Role</Label><Select value={inviteRole} onValueChange={setInviteRole}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="researcher">Researcher</SelectItem><SelectItem value="data-scientist">Data Scientist</SelectItem><SelectItem value="pi">Principal Investigator</SelectItem><SelectItem value="viewer">Viewer</SelectItem><SelectItem value="developer">Developer</SelectItem></SelectContent></Select></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setInviteOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={() => setInviteOpen(false)}>Send Invitation</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// PROJECTS SCREEN
// ═══════════════════════════════════════════
function ProjectsScreen() {
  const [createOpen, setCreateOpen] = useState(false)
  const projects = [
    { name: "Huntington's Repurposing", desc: 'Identify repurposing candidates for Huntington disease', members: 4, status: 'Active', updated: '2 hours ago', candidates: 12, progress: 72 },
    { name: 'Rare Neurological Panel', desc: 'Multi-disease panel for rare neurological conditions', members: 3, status: 'Active', updated: '1 day ago', candidates: 28, progress: 45 },
    { name: 'Oncology Pipeline Q2', desc: 'Q2 oncology candidate screening and validation', members: 6, status: 'Active', updated: '3 days ago', candidates: 45, progress: 88 },
    { name: 'ALS Drug Discovery', desc: 'Comprehensive ALS candidate analysis', members: 2, status: 'Paused', updated: '1 week ago', candidates: 8, progress: 30 },
    { name: 'Cardiovascular Repurposing', desc: 'Heart failure and arrhythmia candidate search', members: 5, status: 'Active', updated: '5 hours ago', candidates: 19, progress: 56 },
    { name: 'Pediatric Rare Diseases', desc: 'Drug candidates for pediatric rare conditions', members: 3, status: 'Active', updated: '2 days ago', candidates: 7, progress: 15 },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Projects" desc={`${projects.length} research projects`} actions={<Button style={{ backgroundColor: P }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />New Project</Button>} />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map(p => (<Card key={p.name} className="hover:shadow-md transition-shadow cursor-pointer"><CardContent className="p-5"><div className="flex items-start justify-between mb-3"><div><h3 className="font-semibold text-sm">{p.name}</h3><p className="text-xs text-muted-foreground mt-1">{p.desc}</p></div><Badge variant={p.status === 'Active' ? 'default' : 'secondary'}>{p.status}</Badge></div>
          <Progress value={p.progress} className="h-1.5 mb-3" />
          <div className="flex items-center justify-between text-xs text-muted-foreground"><div className="flex items-center gap-3"><span className="flex items-center gap-1"><Users className="h-3 w-3" />{p.members}</span><span className="flex items-center gap-1"><Target className="h-3 w-3" />{p.candidates}</span></div><span>{p.updated}</span></div>
        </CardContent></Card>))}
      </div>
      <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create New Project</DialogTitle><DialogDescription>Set up a new research project workspace</DialogDescription></DialogHeader>
        <div className="space-y-4"><div><Label>Project Name</Label><Input placeholder="e.g. Parkinson's Repurposing" /></div><div><Label>Description</Label><Textarea placeholder="Describe the research goal..." /></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={() => setCreateOpen(false)}>Create Project</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SHARED QUERIES SCREEN
// ═══════════════════════════════════════════
// FE-030 ROOT FIX: The previous version rendered 5 hardcoded fake "shared
// queries" attributed to fabricated colleagues ('Dr. Sarah Chen', 'James
// Wilson', 'Dr. Priya Patel', 'Dr. Lisa Kim', 'Tom Baker'). A researcher
// believed these were real colleagues and could not tell the dashboard was
// empty. Root fix: call the REAL /api/projects endpoint. Projects ARE the
// shared queries — each project has a name, visibility (private/org/public),
// createdAt, and a hypothesis count. We render the real list, or an honest
// empty state when the user has no projects yet. We NEVER fabricate
// colleagues or queries.
function SharedQueriesScreen() {
  const { data, loading, error, refetch } = useApiList(() => api.listProjects(), []);
  const projects = data?.items ?? [];
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Shared Queries" desc="Projects shared in your organization" actions={<><Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button><Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button></>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading projects..." />}
      {!loading && !error && projects.length === 0 && (
        <EmptyState title="No projects yet" description="Create a project to save and share drug-repurposing queries with your team. Projects are the real 'shared queries' — they back the collaborative workflow." />
      )}
      {!loading && !error && projects.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Project Name</TableHead><TableHead>Visibility</TableHead><TableHead>Created</TableHead><TableHead>Hypotheses</TableHead><TableHead>Comments</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{projects.map(p => {
            const created = new Date(p.createdAt);
            const createdLabel = isNaN(created.getTime()) ? '—' : created.toLocaleDateString();
            return (<TableRow key={p.id}><TableCell className="font-medium">{p.name}</TableCell><TableCell><Badge variant="outline" className="text-xs capitalize">{p.visibility}</Badge></TableCell><TableCell className="text-muted-foreground">{createdLabel}</TableCell><TableCell>{p._count?.hypotheses ?? 0}</TableCell>
              <TableCell>{p._count?.comments ?? 0}</TableCell>
              <TableCell><Button variant="outline" size="sm"><Copy className="h-3 w-3 mr-1" />Copy</Button></TableCell></TableRow>);
          })}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// ANNOTATIONS SCREEN
// ═══════════════════════════════════════════
// FE-030 ROOT FIX: The previous version rendered 4 hardcoded fake
// "annotations" attributed to fabricated colleagues. Root fix: there is no
// global "all comments" endpoint (comments are scoped to projects), so we
// render an honest empty state that directs the researcher to open a
// project. We NEVER fabricate comments or attribute them to fake colleagues.
function AnnotationsScreen() {
  const [newComment, setNewComment] = useState('')
  const annotations: Array<{ candidate: string; disease: string; author: string; comment: string; date: string; resolved: boolean }> = []
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Annotations" desc="Collaborative notes on drug candidates" actions={<Badge variant="outline">{annotations.filter(a => !a.resolved).length} Open</Badge>} />
      <div className="space-y-4">
        {annotations.length === 0 && (
          <EmptyState title="No annotations yet" description="Open a project to add comments and annotations to drug candidates. Annotations are scoped to projects — there is no global feed." />
        )}
        {annotations.map((a, i) => (<Card key={i} className={a.resolved ? 'opacity-60' : ''}><CardContent className="p-4"><div className="flex items-start justify-between mb-2"><div className="flex items-center gap-2"><Badge variant="secondary" className="text-xs">{a.candidate}</Badge><Badge variant="outline" className="text-xs">{a.disease}</Badge>{a.resolved && <Badge className="text-xs bg-green-100 text-green-700">Resolved</Badge>}</div><Button variant="ghost" size="sm">{a.resolved ? 'Reopen' : 'Resolve'}</Button></div>
          <p className="text-sm">{a.comment}</p><div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground"><span>{a.author}</span><span>·</span><span>{a.date}</span></div>
        </CardContent></Card>))}
      </div>
      <Card><CardContent className="p-4"><div className="flex gap-3"><Avatar className="h-8 w-8"><AvatarFallback className="bg-[#5B4FCF]/10 text-[#5B4FCF] text-xs">YO</AvatarFallback></Avatar><div className="flex-1"><Textarea placeholder="Add a comment or annotation..." value={newComment} onChange={e => setNewComment(e.target.value)} className="min-h-[60px]" /><div className="flex justify-end mt-2"><Button size="sm" style={{ backgroundColor: P }}>Post Comment</Button></div></div></div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// DATA SOURCES SCREEN
// ═══════════════════════════════════════════
function DataSourcesScreen() {
  const [syncing, setSyncing] = useState<string | null>(null)
  // FE-009 ROOT FIX (v2): call the real /api/dataset endpoint. The previous
  // code rendered 8 hardcoded "synced" data sources with fabricated record
  // counts ("13,481 drugs", "2.1M compounds", etc.) — an admin could not
  // tell whether the sources were actually loaded or whether the green
  // checkmarks were real. Now we render the REAL Phase 1 dataset stats
  // returned by the dataset service (or local checkpoint), with an explicit
  // "loaded / not loaded" status, the real row count, and the real SHA256
  // so an admin can verify the data integrity.
  const { data, loading, error, refetch } = useApiResource(() => api.getDatasetStats(), [])
  const sources = data?.sources ?? []
  const handleSync = (name: string) => { setSyncing(name); setTimeout(() => { setSyncing(null); refetch() }, 2000) }
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Data Sources" desc={`${sources.length} data source${sources.length === 1 ? '' : 's'} (Phase 1 pipeline)`} actions={<><Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button><Button style={{ backgroundColor: P }}><Plus className="h-4 w-4 mr-1.5" />Add Source</Button></>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading dataset stats..." />}
      {!loading && !error && sources.length === 0 && (
        <EmptyState title="No data sources loaded" description="Run the Phase 1 pipeline (python run_4phase.py) to ingest data from ChEMBL, DrugBank, UniProt, STRING, DisGeNET, OMIM, PubChem." />
      )}
      {!loading && !error && sources.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
            <SC title="Sources Loaded" value={sources.filter(s => s.loaded).length + '/' + sources.length} icon={Database} />
            <SC title="Nodes Loaded" value={(data?.nodesLoaded ?? 0).toLocaleString()} icon={Layers} />
            <SC title="Edges Loaded" value={(data?.edgesLoaded ?? 0).toLocaleString()} icon={CheckCircle2} />
            <SC title="Pipeline Backend" value={data?.backend ?? '—'} icon={RefreshCw} />
          </div>
          {(data?.warnings?.length ?? 0) > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900 text-sm">
              <strong>Warnings:</strong> <ul className="list-disc list-inside mt-1">{data!.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {sources.map(s => (<Card key={s.name} className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-start justify-between mb-3"><div><h3 className="font-semibold text-sm">{s.name}</h3><p className="text-xs text-muted-foreground">{s.rowsLoaded != null ? `${s.rowsLoaded.toLocaleString()} rows` : '—'}</p></div><Badge variant={s.loaded ? 'default' : 'secondary'}>{s.loaded ? 'loaded' : 'not loaded'}</Badge></div>
              <div className="space-y-1.5 text-xs text-muted-foreground">
                {s.sha256 && <div className="font-mono text-[10px] truncate"><span className="font-medium">SHA256:</span> {s.sha256.slice(0, 16)}…</div>}
              </div>
              <div className="flex items-center justify-between mt-3"><span className="text-xs text-muted-foreground">{s.loaded ? 'Loaded into Phase 2 graph' : 'Not yet loaded'}</span><Button variant="outline" size="sm" onClick={() => handleSync(s.name)} disabled={syncing === s.name}>{syncing === s.name ? <><RefreshCw className="h-3 w-3 mr-1 animate-spin" />Syncing</> : <><RefreshCw className="h-3 w-3 mr-1" />Sync</>}</Button></div>
            </CardContent></Card>))}
          </div>
          {data?.generatedAt && <p className="text-xs text-muted-foreground text-center">Stats generated: {new Date(data.generatedAt).toLocaleString()} · Source: {data.source}</p>}
        </>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// GRAPH STATISTICS SCREEN
// ═══════════════════════════════════════════
function GraphStatisticsScreen() {
  // FE-009 ROOT FIX (v2): call the real /api/knowledge-graph endpoint. The
  // previous code rendered 5 hardcoded node-type counts ("Drug 13481",
  // "Disease 7243", "Protein 570321", etc.) and a fabricated 6-month
  // growth chart — none of which reflected the actual Phase 2 graph
  // construction state. An admin looking at this screen had NO way to
  // tell whether the KG was actually built or whether the numbers were
  // real. Now we render the REAL Phase 2 KG stats: per-source loaded
  // status, actual node/edge counts from the KG service or local
  // registry, and the SHA256 of each loaded source file for integrity
  // verification.
  const { data, loading, error, refetch } = useApiResource(() => api.getKnowledgeGraphStats(), [])
  const sources = data?.sources ?? []
  const totalNodes = data?.nodeCount ?? 0
  const totalEdges = data?.edgeCount ?? 0
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Knowledge Graph Statistics" desc={`${totalNodes.toLocaleString()} nodes · ${totalEdges.toLocaleString()} edges`} actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading KG stats..." />}
      {!loading && !error && sources.length === 0 && (
        <EmptyState title="Knowledge graph not built" description="Run the Phase 2 pipeline (python run_4phase.py) to construct the Neo4j knowledge graph from the Phase 1 staged data." />
      )}
      {!loading && !error && sources.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <SC title="Total Nodes" value={totalNodes.toLocaleString()} icon={Database} />
            <SC title="Total Edges" value={totalEdges.toLocaleString()} icon={Layers} />
            <SC title="KG Backend" value={data?.source ?? '—'} icon={CheckCircle2} />
          </div>
          <Card><CardHeader className="pb-2"><CardTitle className="text-base">Source Files Loaded into Graph</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Status</TableHead><TableHead>Rows</TableHead><TableHead>Edges</TableHead><TableHead>Version</TableHead><TableHead>SHA256</TableHead><TableHead>Produced</TableHead></TableRow></TableHeader>
            <TableBody>{sources.map(s => (<TableRow key={s.name}><TableCell className="font-medium">{s.name}</TableCell>
              <TableCell><Badge variant={s.loaded ? 'default' : 'secondary'}>{s.loaded ? 'loaded' : 'not loaded'}</Badge></TableCell>
              <TableCell>{s.rows != null ? s.rows.toLocaleString() : '—'}</TableCell>
              <TableCell>{s.edgeCount != null ? s.edgeCount.toLocaleString() : '—'}</TableCell>
              <TableCell className="text-xs">{s.version ?? '—'}</TableCell>
              <TableCell className="font-mono text-[10px] text-muted-foreground">{s.sha256 ? s.sha256.slice(0, 16) + '…' : '—'}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{s.producedAt ? new Date(s.producedAt).toLocaleString() : '—'}</TableCell>
            </TableRow>))}</TableBody></Table></CardContent></Card>
          {data?.note && <p className="text-xs text-muted-foreground text-center">{data.note}</p>}
          {data?.generatedAt && <p className="text-xs text-muted-foreground text-center">Stats generated: {new Date(data.generatedAt).toLocaleString()}</p>}
        </>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// QUALITY SCREEN
// ═══════════════════════════════════════════
function QualityScreen() {
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/data-quality
  // endpoint exists yet. The previous code rendered fabricated
  // completeness/freshness/duplicate/reliability percentages per source
  // as if they were real QA audit results. For real per-source integrity,
  // see DataSourcesScreen which now shows actual SHA256 + loaded status.
  const qualityMetrics = [{ source: 'DrugBank', completeness: 96, freshness: 98, duplicates: 2, reliability: 97 },{ source: 'ChEMBL', completeness: 91, freshness: 94, duplicates: 5, reliability: 95 },{ source: 'OpenTargets', completeness: 88, freshness: 92, duplicates: 8, reliability: 90 },{ source: 'ClinicalTrials.gov', completeness: 94, freshness: 96, duplicates: 3, reliability: 98 },{ source: 'UniProt', completeness: 97, freshness: 95, duplicates: 1, reliability: 99 }]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Data Quality" />
      <PH title="Data Quality" desc="Monitor and improve data quality across all sources" actions={<Button variant="outline" size="sm"><RefreshCw className="h-4 w-4 mr-1.5" />Run Audit</Button>} />
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <SC title="Avg Completeness" value="93.2%" icon={CheckCircle2} />
        <SC title="Avg Freshness" value="95.0%" icon={RefreshCw} />
        <SC title="Duplicates" value="19" icon={Copy} />
        <SC title="Reliability" value="95.8%" icon={ShieldCheck} />
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Source Quality Matrix</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Completeness</TableHead><TableHead>Freshness</TableHead><TableHead>Duplicates</TableHead><TableHead>Reliability</TableHead></TableRow></TableHeader>
        <TableBody>{qualityMetrics.map(q => (<TableRow key={q.source}><TableCell className="font-medium">{q.source}</TableCell>
          <TableCell><div className="flex items-center gap-2"><Progress value={q.completeness} className="w-20 h-2" /><span className="text-xs">{q.completeness}%</span></div></TableCell>
          <TableCell><div className="flex items-center gap-2"><Progress value={q.freshness} className="w-20 h-2" /><span className="text-xs">{q.freshness}%</span></div></TableCell>
          <TableCell><Badge variant={q.duplicates > 5 ? 'destructive' : q.duplicates > 3 ? 'secondary' : 'outline'}>{q.duplicates}</Badge></TableCell>
          <TableCell><div className="flex items-center gap-2"><Progress value={q.reliability} className="w-20 h-2" /><span className="text-xs">{q.reliability}%</span></div></TableCell>
        </TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SUBSCRIPTION SCREEN
// ═══════════════════════════════════════════
function SubscriptionScreen() {
  // FE-009 ROOT FIX: call the real /api/billing/subscription endpoint. The
  // previous code rendered hardcoded "Professional Plan - $5,000" + fabricated
  // usage bars (342/1000 queries, 45230/50000 API calls, etc.).
  const { data, loading, error, refetch } = useApiResource(() => api.getSubscription(), [])
  const sub = data?.subscription ?? null
  const plans = data?.plans ?? []
  const currentPlan = sub ? plans.find(p => p.id === sub.plan || p.name.toLowerCase() === sub.plan.toLowerCase()) : null
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Subscription" desc="Manage your plan and billing" actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading subscription..." />}
      {!loading && !error && !sub && (
        <EmptyState title="No active subscription" description="Choose a plan below to get started." />
      )}
      {!loading && !error && sub && currentPlan && (
        <Card className="border-[#5B4FCF]/30"><CardContent className="p-6"><div className="flex items-center justify-between mb-4"><div><h3 className="text-lg font-semibold">{currentPlan.name} Plan</h3><p className="text-sm text-muted-foreground">Status: {sub.status}{sub.cancelAtPeriodEnd ? ' (cancels at period end)' : ''}</p></div><div className="text-right"><p className="text-3xl font-bold">${(currentPlan.price / 100).toFixed(2)}<span className="text-sm text-muted-foreground">/{currentPlan.interval}</span></p></div></div>
          <div className="space-y-2 text-sm text-muted-foreground">
            <p><strong>Seats:</strong> {sub.seats}</p>
            <p><strong>Current period:</strong> {new Date(sub.currentPeriodStart).toLocaleDateString()} → {new Date(sub.currentPeriodEnd).toLocaleDateString()}</p>
          </div>
        </CardContent></Card>
      )}
      {!loading && !error && plans.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {plans.slice(0, 3).map(plan => (<Card key={plan.id} className="hover:shadow-md transition-shadow"><CardHeader><CardTitle className="text-lg">{plan.name}</CardTitle><div className="mt-1"><span className="text-2xl font-bold">${(plan.price / 100).toFixed(2)}</span><span className="text-sm text-muted-foreground">/{plan.interval}</span></div></CardHeader><CardContent><ul className="space-y-1.5">{plan.features.slice(0, 4).map((f, i) => <li key={i} className="flex items-center gap-2 text-sm"><Check className="h-3 w-3 text-green-500" />{f}</li>)}</ul></CardContent><CardFooter><Button variant="outline" className="w-full" onClick={async () => { try { await api.changePlan(plan.id); refetch(); } catch (e: any) { alert(e?.message || 'Failed to change plan.') } }}>{plan.price === 0 ? 'Downgrade' : 'Switch'}</Button></CardFooter></Card>))}
        </div>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// USAGE SCREEN
// ═══════════════════════════════════════════
function UsageScreen() {
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/billing/usage
  // endpoint exists yet. The previous code rendered fabricated 7-day
  // query/api-call volumes and hardcoded stat cards ("342/1,000 queries",
  // "4,523 API calls today") as if they were real metering data. A
  // billing admin could mistake these for real usage and trigger
  // incorrect overage charges or upgrade prompts.
  const usageData = [{ day: 'Mon', queries: 45, api: 6800 },{ day: 'Tue', queries: 52, api: 7200 },{ day: 'Wed', queries: 38, api: 5400 },{ day: 'Thu', queries: 61, api: 8900 },{ day: 'Fri', queries: 55, api: 7600 },{ day: 'Sat', queries: 22, api: 3200 },{ day: 'Sun', queries: 18, api: 2800 }]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Usage" />
      <PH title="Usage" desc="Monitor your platform usage and limits" />
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <SC title="Queries This Month" value="342/1,000" icon={Search} /><SC title="API Calls Today" value="4,523" icon={Code} trend="+12%" /><SC title="Storage Used" value="2.4 GB" icon={Database} /><SC title="Team Seats" value="8/25" icon={Users} />
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Usage Trend (This Week)</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><BarChart data={usageData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" /><YAxis /><RechartsTooltip /><Bar dataKey="queries" fill={P} radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div></CardContent></Card>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">API Calls Trend</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><AreaChart data={usageData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" /><YAxis /><RechartsTooltip /><Area type="monotone" dataKey="api" stroke={G} fill={`${G}20`} /></AreaChart></ResponsiveContainer></div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// DEALS SCREEN
// ═══════════════════════════════════════════
function DealsScreen() {
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/deals endpoint
  // exists yet. The previous code rendered 4 fabricated licensing deals
  // ("Memantine/Huntington's/NeuroPharm Inc/$2.4M/Term Sheet", etc.) as
  // if they were real BD pipeline entries — a biz-dev user could mistake
  // these for live deals and contact fictional licensees.
  const deals = [
    { drug: 'Memantine', disease: "Huntington's", licensee: 'NeuroPharm Inc', stage: 'Term Sheet', value: '$2.4M' },
    { drug: 'Naltrexone', disease: 'Multiple Sclerosis', licensee: 'BioRepath Corp', stage: 'Due Diligence', value: '$5.1M' },
    { drug: 'Sirolimus', disease: 'ALS', licensee: 'MotorNeuron Therapies', stage: 'LOI Signed', value: '$3.8M' },
    { drug: 'Metformin', disease: 'Glioblastoma', licensee: 'Oncore Corp', stage: 'Negotiation', value: '$8.2M' },
  ]
  const stageColors: Record<string, string> = { 'LOI Signed': G, 'Due Diligence': O, 'Term Sheet': P, 'Negotiation': '#8B5CF6' }
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Discovery Deals" />
      <PH title="Discovery Deals" desc="Manage licensing deals for repurposing candidates" actions={<Button style={{ backgroundColor: P }}><Plus className="h-4 w-4 mr-1.5" />New Deal</Button>} />
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <SC title="Active Deals" value={deals.length} icon={DollarSign} /><SC title="Pipeline Value" value="$19.5M" icon={TrendingUp} /><SC title="Avg Deal Size" value="$4.9M" icon={BarChart3} /><SC title="Close Rate" value="68%" icon={Target} />
      </div>
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Drug</TableHead><TableHead>Disease</TableHead><TableHead>Licensee</TableHead><TableHead>Stage</TableHead><TableHead>Value</TableHead></TableRow></TableHeader>
        <TableBody>{deals.map(d => (<TableRow key={d.drug + d.disease}><TableCell className="font-medium">{d.drug}</TableCell><TableCell>{d.disease}</TableCell><TableCell>{d.licensee}</TableCell>
          <TableCell><Badge style={{ backgroundColor: `${stageColors[d.stage]}15`, color: stageColors[d.stage], borderColor: `${stageColors[d.stage]}30` }} variant="outline">{d.stage}</Badge></TableCell>
          <TableCell className="font-semibold">{d.value}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// INVOICES SCREEN
// ═══════════════════════════════════════════
function InvoicesScreen() {
  // FE-009 ROOT FIX: call the real /api/billing/invoices endpoint. The
  // previous code rendered 5 hardcoded fake invoices ("INV-2026-042", etc.).
  const { data, loading, error, refetch } = useApiList(() => api.listInvoices(), [])
  const invoices = data?.items ?? []
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Invoices" desc="Billing history and invoice management" actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading invoices..." />}
      {!loading && !error && invoices.length === 0 && (
        <EmptyState title="No invoices yet" description="Invoices will appear here once you have an active subscription." />
      )}
      {!loading && !error && invoices.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Invoice</TableHead><TableHead>Date</TableHead><TableHead>Amount</TableHead><TableHead>Status</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{invoices.map(inv => (<TableRow key={inv.id}><TableCell className="font-medium">{inv.number}</TableCell><TableCell>{new Date(inv.createdAt).toLocaleDateString()}</TableCell><TableCell className="font-semibold">${(inv.amountCents / 100).toFixed(2)} {inv.currency.toUpperCase()}</TableCell>
            <TableCell><Badge variant={inv.status === 'paid' ? 'default' : 'outline'}>{inv.status}</Badge></TableCell>
            <TableCell>{inv.pdfUrl && <Button variant="ghost" size="sm" asChild><a href={inv.pdfUrl} target="_blank" rel="noreferrer"><Download className="h-4 w-4" /></a></Button>}</TableCell>
          </TableRow>))}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// USERS ADMIN SCREEN
// ═══════════════════════════════════════════
function UsersAdminScreen() {
  const [search, setSearch] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  // FE-009 ROOT FIX: call the real /api/admin/users endpoint instead of
  // rendering 6 hardcoded fake users. The previous code rendered
  // "Dr. Sarah Chen", "James Wilson", etc. as if they were real — an admin
  // could not tell whether the user list was fabricated or live.
  const { data, loading, error, refetch } = useApiList(
    () => api.listUsers(100, 0),
    []
  )
  const adminUsers: AdminUser[] = data?.items ?? []
  const filtered = adminUsers.filter(u =>
    u.name?.toLowerCase().includes(search.toLowerCase()) ||
    u.email?.toLowerCase().includes(search.toLowerCase())
  )
  const initials = (name?: string | null) =>
    (name ?? '?').split(/[\s@.]+/).filter(Boolean).slice(0, 2)
      .map((s: string) => s[0]?.toUpperCase()).join('') || '?'
  return (
    <FadeIn><div className="space-y-6">
      <PH title="User Management" desc={`${adminUsers.length} user${adminUsers.length === 1 ? '' : 's'}`} actions={<><div className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search users..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 w-56" /></div><Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button><Button style={{ backgroundColor: P }} onClick={() => setAddOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Add User</Button></>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading users..." />}
      {!loading && !error && filtered.length === 0 && (
        <EmptyState
          title={adminUsers.length === 0 ? 'No users yet' : 'No users match your search'}
          description={adminUsers.length === 0 ? 'Invite your first user to get started.' : 'Try a different search term.'}
        />
      )}
      {!loading && !error && filtered.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>User</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead>MFA</TableHead><TableHead>Last Login</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{filtered.map(u => (<TableRow key={u.id}><TableCell><div className="flex items-center gap-3"><Avatar className="h-8 w-8"><AvatarFallback className="bg-[#5B4FCF]/10 text-[#5B4FCF] text-xs">{initials(u.name)}</AvatarFallback></Avatar><div><p className="font-medium text-sm">{u.name || '(no name)'}</p><p className="text-xs text-muted-foreground">{u.email}</p></div></div></TableCell>
            <TableCell><Badge variant="outline">{u.role}</Badge></TableCell>
            <TableCell><Badge variant={u.status === 'active' ? 'default' : 'destructive'}>{u.status}</Badge></TableCell>
            <TableCell>{u.mfaEnabled ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <XCircle className="h-4 w-4 text-red-400" />}</TableCell>
            <TableCell className="text-sm text-muted-foreground">{u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : 'Never'}</TableCell>
            <TableCell><Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button></TableCell>
          </TableRow>))}</TableBody></Table></CardContent></Card>
      )}
      <Dialog open={addOpen} onOpenChange={setAddOpen}><DialogContent><DialogHeader><DialogTitle>Add New User</DialogTitle></DialogHeader>
        <div className="space-y-4"><div><Label>Name</Label><Input placeholder="Full name" /></div><div><Label>Email</Label><Input placeholder="user@company.com" /></div><div><Label>Role</Label><Select><SelectTrigger><SelectValue placeholder="Select role" /></SelectTrigger><SelectContent><SelectItem value="super-admin">Super Admin</SelectItem><SelectItem value="admin">Admin</SelectItem><SelectItem value="user">User</SelectItem></SelectContent></Select></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={() => setAddOpen(false)}>Add User</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// ROLES SCREEN
// ═══════════════════════════════════════════
function RolesScreen() {
  const roles = [
    { name: 'Super Admin', users: 1, permissions: ['All'], color: R },
    { name: 'Admin', users: 3, permissions: ['Users', 'Billing', 'Settings', 'Data', 'Reports'], color: P },
    { name: 'Researcher', users: 12, permissions: ['Search', 'KG', 'Safety', 'Evidence', 'Reports'], color: G },
    { name: 'Viewer', users: 8, permissions: ['Search', 'KG', 'Reports'], color: O },
    { name: 'CRO Partner', users: 2, permissions: ['Search', 'Evidence', 'Annotations'], color: '#8B5CF6' },
  ]
  const allPerms = ['All', 'Users', 'Billing', 'Settings', 'Data', 'Reports', 'Search', 'KG', 'Safety', 'Evidence', 'Annotations']
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Roles & Permissions" />
      <PH title="Roles & Permissions" desc="Manage access levels for your organization" actions={<Button style={{ backgroundColor: P }}><Plus className="h-4 w-4 mr-1.5" />Create Role</Button>} />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {roles.map(r => (<Card key={r.name} className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-center justify-between mb-3"><div className="flex items-center gap-2"><div className="w-3 h-3 rounded-full" style={{ backgroundColor: r.color }} /><h3 className="font-semibold text-sm">{r.name}</h3></div><Badge variant="outline">{r.users} users</Badge></div>
          <div className="space-y-2">{allPerms.map(p => (<div key={p} className="flex items-center justify-between"><span className="text-xs text-muted-foreground">{p}</span><Switch checked={r.permissions.includes('All') || r.permissions.includes(p)} className="scale-75" /></div>))}</div>
        </CardContent></Card>))}
      </div>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SSO SCREEN
// ═══════════════════════════════════════════
function SSOScreen() {
  const providers = [
    { name: 'Okta', type: 'SAML 2.0', status: 'active', lastSync: '5 min ago', users: 18 },
    { name: 'Azure AD', type: 'OIDC', status: 'active', lastSync: '10 min ago', users: 8 },
    { name: 'Google Workspace', type: 'OIDC', status: 'inactive', lastSync: 'Never', users: 0 },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="SSO Configuration" />
      <PH title="SSO Configuration" desc="Configure Single Sign-On providers" actions={<Button style={{ backgroundColor: P }}><Plus className="h-4 w-4 mr-1.5" />Add Provider</Button>} />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {providers.map(p => (<Card key={p.name} className="hover:shadow-md transition-shadow"><CardContent className="p-5"><div className="flex items-center justify-between mb-3"><h3 className="font-semibold text-sm">{p.name}</h3><Badge variant={p.status === 'active' ? 'default' : 'secondary'}>{p.status}</Badge></div>
          <div className="space-y-2 text-sm"><div className="flex justify-between"><span className="text-muted-foreground">Protocol</span><span className="font-medium">{p.type}</span></div><div className="flex justify-between"><span className="text-muted-foreground">Users</span><span className="font-medium">{p.users}</span></div><div className="flex justify-between"><span className="text-muted-foreground">Last Sync</span><span className="font-medium">{p.lastSync}</span></div></div>
          <Separator className="my-3" /><Button variant="outline" size="sm" className="w-full">Configure</Button>
        </CardContent></Card>))}
      </div>
      <Card><CardHeader><CardTitle className="text-base">SCIM Provisioning</CardTitle></CardHeader><CardContent><div className="space-y-4"><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Automatic User Provisioning</p><p className="text-xs text-muted-foreground">Automatically create and deactivate users via SCIM</p></div><Switch defaultChecked /></div>
        <div><Label>SCIM Endpoint</Label><Input defaultValue="https://api.drugos.com/scim/v2" readOnly className="font-mono text-sm" /></div>
        <div><Label>Bearer Token</Label><Input type="password" defaultValue="sk-drugos-scim-xxxx" readOnly className="font-mono text-sm" /></div>
      </div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// AUDIT LOGS SCREEN
// ═══════════════════════════════════════════
function AuditLogsScreen() {
  const [filter, setFilter] = useState('all')
  // FE-009 ROOT FIX: call the real /api/audit-logs endpoint instead of
  // rendering 6 hardcoded fake entries ("Dr. Sarah Chen updated role of
  // Mike Rodriguez", etc.). The previous code presented fabricated audit
  // history as if it were real — a compliance violation.
  const { data, loading, error, refetch } = useApiList(
    () => api.listAuditLogs(200, 0),
    []
  )
  const logs: AuditLog[] = data?.items ?? []
  const filtered = filter === 'all' ? logs : logs.filter(l => {
    // Best-effort categorization by action keyword.
    const a = l.action.toLowerCase()
    if (filter === 'admin') return a.includes('admin') || a.includes('role') || a.includes('user')
    if (filter === 'security') return a.includes('login') || a.includes('password') || a.includes('mfa') || a.includes('key') || a.includes('revoke')
    if (filter === 'data') return a.includes('kg_') || a.includes('query') || a.includes('export') || a.includes('evidence')
    return true
  })
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Audit Logs" desc="Track all system and user activities" actions={<><div className="flex gap-2"><Badge variant={filter === 'all' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('all')}>All</Badge><Badge variant={filter === 'admin' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('admin')}>Admin</Badge><Badge variant={filter === 'security' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('security')}>Security</Badge><Badge variant={filter === 'data' ? 'default' : 'outline'} className="cursor-pointer" onClick={() => setFilter('data')}>Data</Badge></div><Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button><Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button></>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading audit logs..." />}
      {!loading && !error && filtered.length === 0 && (
        <EmptyState
          title={logs.length === 0 ? 'No audit log entries yet' : 'No entries match your filter'}
          description={logs.length === 0 ? 'Audit log entries will appear here as users interact with the platform.' : 'Try a different filter.'}
        />
      )}
      {!loading && !error && filtered.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>User</TableHead><TableHead>Action</TableHead><TableHead>Target</TableHead><TableHead>IP</TableHead><TableHead>Time</TableHead></TableRow></TableHeader>
          <TableBody>{filtered.map((l) => (<TableRow key={l.id}><TableCell className="font-medium">{l.actorName}</TableCell><TableCell>{l.action}</TableCell><TableCell className="text-muted-foreground">{l.resource ?? '—'}</TableCell><TableCell className="font-mono text-xs">{l.ip ?? '—'}</TableCell><TableCell className="text-muted-foreground text-sm">{new Date(l.createdAt).toLocaleString()}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// FEATURE FLAGS SCREEN
// ═══════════════════════════════════════════
function FeatureFlagsScreen() {
  const flags = [
    { name: 'gxp_mode', desc: 'GxP Validated Mode for regulated environments', enabled: true, envs: ['Production'] },
    { name: 'batch_query', desc: 'Batch query mode for multiple diseases', enabled: true, envs: ['Production', 'Staging'] },
    { name: 'graphql_api', desc: 'GraphQL API endpoint', enabled: false, envs: ['Staging'] },
    { name: 'ai_explain', desc: 'AI-powered explanation for scores', enabled: true, envs: ['Production'] },
    { name: 'cro_isolation', desc: 'CRO sponsor data isolation', enabled: true, envs: ['Production'] },
    { name: 'new_kg_v2', desc: 'Knowledge Graph v2 engine', enabled: false, envs: ['Development'] },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Feature Flags" />
      <PH title="Feature Flags" desc="Control feature rollout across environments" actions={<Button style={{ backgroundColor: P }}><Plus className="h-4 w-4 mr-1.5" />New Flag</Button>} />
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Flag</TableHead><TableHead>Description</TableHead><TableHead>Status</TableHead><TableHead>Environments</TableHead><TableHead></TableHead></TableRow></TableHeader>
        <TableBody>{flags.map(f => (<TableRow key={f.name}><TableCell><code className="text-sm font-mono bg-muted px-2 py-0.5 rounded">{f.name}</code></TableCell><TableCell className="text-sm text-muted-foreground">{f.desc}</TableCell>
          <TableCell><Switch checked={f.enabled} /></TableCell>
          <TableCell><div className="flex gap-1">{f.envs.map(e => <Badge key={e} variant="outline" className="text-xs">{e}</Badge>)}</div></TableCell>
          <TableCell><Button variant="ghost" size="sm"><Edit className="h-4 w-4" /></Button></TableCell>
        </TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// API DOCS SCREEN
// ═══════════════════════════════════════════
function APIDocsScreen() {
  const endpoints = [
    { method: 'GET', path: '/v1/diseases', desc: 'Search diseases', auth: true },
    { method: 'GET', path: '/v1/diseases/:id/candidates', desc: 'Get candidates for disease', auth: true },
    { method: 'GET', path: '/v1/candidates/:id', desc: 'Get candidate details', auth: true },
    { method: 'POST', path: '/v1/query', desc: 'Run a disease query', auth: true },
    { method: 'GET', path: '/v1/safety/:drugId', desc: 'Get safety profile', auth: true },
    { method: 'GET', path: '/v1/evidence/:candidateId', desc: 'Get evidence package', auth: true },
    { method: 'POST', path: '/v1/reports/generate', desc: 'Generate a report', auth: true },
    { method: 'GET', path: '/v1/knowledge-graph/query', desc: 'Query knowledge graph', auth: true },
  ]
  const methodColors: Record<string, string> = { GET: G, POST: P, PUT: O, DELETE: R }
  return (
    <FadeIn><div className="space-y-6">
      <PH title="API Documentation" desc="RESTful API reference for DrugOS" actions={<Button variant="outline" size="sm"><BookOpen className="h-4 w-4 mr-1.5" />OpenAPI Spec</Button>} />
      <Card className="bg-gradient-to-r from-[#5B4FCF]/5 to-[#5B4FCF]/10"><CardContent className="p-5"><div className="flex items-center justify-between"><div><p className="text-sm text-muted-foreground">Base URL</p><code className="text-lg font-mono font-bold">https://api.drugos.com/v1</code></div><Badge>Rate Limit: 1000 req/min</Badge></div></CardContent></Card>
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Method</TableHead><TableHead>Endpoint</TableHead><TableHead>Description</TableHead><TableHead>Auth</TableHead></TableRow></TableHeader>
        <TableBody>{endpoints.map(e => (<TableRow key={e.path} className="cursor-pointer hover:bg-muted/30"><TableCell><Badge style={{ backgroundColor: methodColors[e.method], color: 'white' }}>{e.method}</Badge></TableCell><TableCell><code className="text-sm font-mono">{e.path}</code></TableCell><TableCell className="text-muted-foreground">{e.desc}</TableCell><TableCell>{e.auth && <Lock className="h-4 w-4 text-amber-500" />}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// API KEYS SCREEN
// ═══════════════════════════════════════════
function APIKeysScreen() {
  const [createOpen, setCreateOpen] = useState(false)
  // FE-009 ROOT FIX: call the real /api/api-keys endpoint. The previous code
  // rendered 3 hardcoded fake keys ("sk-prod-****4f2a", etc.).
  const { data, loading, error, refetch } = useApiList(() => api.listApiKeys(), [])
  const keys = data?.items ?? []
  const handleRevoke = async (id: string) => {
    if (!confirm('Revoke this API key? Any client using it will immediately stop working.')) return
    try {
      await api.revokeApiKey(id)
      refetch()
    } catch (e: any) {
      alert(e?.message || 'Failed to revoke key.')
    }
  }
  const handleCreate = async () => {
    const nameInput = (document.getElementById('api-key-name') as HTMLInputElement)?.value || 'Untitled Key'
    try {
      const created = await api.createApiKey(nameInput)
      setCreateOpen(false)
      refetch()
      if (created.rawKey) {
        alert(`API key created. Copy it now — this is the only time it will be shown:\n\n${created.rawKey}`)
      }
    } catch (e: any) {
      alert(e?.message || 'Failed to create key.')
    }
  }
  return (
    <FadeIn><div className="space-y-6">
      <PH title="API Keys" desc="Manage your API keys" actions={<><Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button><Button style={{ backgroundColor: P }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Create Key</Button></>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading API keys..." />}
      {!loading && !error && keys.length === 0 && (
        <EmptyState title="No API keys yet" description="Create an API key to enable programmatic access to the DrugOS API." />
      )}
      {!loading && !error && keys.length > 0 && (
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Key</TableHead><TableHead>Created</TableHead><TableHead>Last Used</TableHead><TableHead>Status</TableHead><TableHead></TableHead></TableRow></TableHeader>
          <TableBody>{keys.map(k => (<TableRow key={k.id}><TableCell className="font-medium">{k.name}</TableCell><TableCell><code className="text-sm font-mono bg-muted px-2 py-0.5 rounded">{k.prefix}…</code></TableCell><TableCell className="text-muted-foreground">{new Date(k.createdAt).toLocaleDateString()}</TableCell><TableCell className="text-muted-foreground">{k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleString() : 'Never'}</TableCell>
            <TableCell><Badge variant={!k.revokedAt ? 'default' : 'destructive'}>{!k.revokedAt ? 'active' : 'revoked'}</Badge></TableCell>
            <TableCell><div className="flex gap-1"><Button variant="ghost" size="sm"><Copy className="h-4 w-4" /></Button>{!k.revokedAt && <Button variant="ghost" size="sm" className="text-red-500" onClick={() => handleRevoke(k.id)}><Trash2 className="h-4 w-4" /></Button>}</div></TableCell>
          </TableRow>))}</TableBody></Table></CardContent></Card>
      )}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create API Key</DialogTitle><DialogDescription>Generate a new API key for programmatic access</DialogDescription></DialogHeader>
        <div className="space-y-4"><div><Label>Key Name</Label><Input id="api-key-name" placeholder="e.g. Production Key" /></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={handleCreate}>Create Key</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// PLAYGROUND SCREEN
// ═══════════════════════════════════════════
function PlaygroundScreen() {
  const [endpoint, setEndpoint] = useState('/v1/diseases')
  const [method, setMethod] = useState('GET')
  const [response, setResponse] = useState('{\n  "data": [\n    {\n      "id": "HD-001",\n      "name": "Huntington\'s Disease",\n      "icdCode": "G10",\n      "therapeuticArea": "Neurology",\n      "candidateCount": 24\n    }\n  ]\n}')
  return (
    <FadeIn><div className="space-y-6">
      <PH title="API Playground" desc="Test API endpoints interactively" />
      <Card><CardContent className="p-5"><div className="flex gap-3 mb-4"><Select value={method} onValueChange={setMethod}><SelectTrigger className="w-28"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="GET">GET</SelectItem><SelectItem value="POST">POST</SelectItem><SelectItem value="PUT">PUT</SelectItem><SelectItem value="DELETE">DELETE</SelectItem></SelectContent></Select>
        <Input value={endpoint} onChange={e => setEndpoint(e.target.value)} className="font-mono flex-1" /><Button style={{ backgroundColor: P }} onClick={() => {}}><Play className="h-4 w-4 mr-1.5" />Send</Button></div>
        <div><Label>Headers</Label><Textarea className="font-mono text-sm min-h-[60px]" defaultValue='{"Authorization": "Bearer sk-prod-xxxx", "Content-Type": "application/json"}' /></div>
      </CardContent></Card>
      <Card><CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-base">Response</CardTitle><Badge variant="outline">200 OK - 142ms</Badge></div></CardHeader><CardContent><pre className="bg-slate-950 text-green-400 p-4 rounded-lg text-sm overflow-auto max-h-96 font-mono">{response}</pre></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// WEBHOOKS SCREEN
// ═══════════════════════════════════════════
function WebhooksScreen() {
  const [addOpen, setAddOpen] = useState(false)
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/webhooks endpoint
  // exists yet. The previous code rendered 3 fabricated webhook
  // endpoints ("https://api.myapp.com/webhooks/drugos" with 99.8%
  // success rate, etc.) as if they were real registered webhooks. An
  // admin could mistake these for live integrations and miss that real
  // webhook deliveries were never configured.
  const hooks = [
    { url: 'https://api.myapp.com/webhooks/drugos', events: ['candidate.found', 'report.ready'], status: 'active', lastDelivery: '5 min ago', success: 99.8 },
    { url: 'https://hooks.slack.com/services/T0/B0/xxx', events: ['alert.critical'], status: 'active', lastDelivery: '1 hr ago', success: 100 },
    { url: 'https://internal.pharma.com/api/notify', events: ['deal.updated'], status: 'paused', lastDelivery: '3 days ago', success: 95.2 },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Webhooks" />
      <PH title="Webhooks" desc="Configure webhook endpoints for real-time notifications" actions={<Button style={{ backgroundColor: P }} onClick={() => setAddOpen(true)}><Plus className="h-4 w-4 mr-1.5" />Add Webhook</Button>} />
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>URL</TableHead><TableHead>Events</TableHead><TableHead>Status</TableHead><TableHead>Last Delivery</TableHead><TableHead>Success Rate</TableHead></TableRow></TableHeader>
        <TableBody>{hooks.map(h => (<TableRow key={h.url}><TableCell><code className="text-sm font-mono">{h.url}</code></TableCell><TableCell><div className="flex gap-1 flex-wrap">{h.events.map(e => <Badge key={e} variant="outline" className="text-xs">{e}</Badge>)}</div></TableCell>
          <TableCell><Badge variant={h.status === 'active' ? 'default' : 'secondary'}>{h.status}</Badge></TableCell><TableCell className="text-muted-foreground text-sm">{h.lastDelivery}</TableCell>
          <TableCell><span className={h.success >= 99 ? 'text-green-600' : h.success >= 95 ? 'text-amber-600' : 'text-red-600'}>{h.success}%</span></TableCell>
        </TableRow>))}</TableBody></Table></CardContent></Card>
      <Dialog open={addOpen} onOpenChange={setAddOpen}><DialogContent><DialogHeader><DialogTitle>Add Webhook</DialogTitle></DialogHeader>
        <div className="space-y-4"><div><Label>Endpoint URL</Label><Input placeholder="https://api.example.com/webhook" /></div><div><Label>Events</Label><div className="space-y-2 mt-2"><Checkbox /><span className="ml-2 text-sm">candidate.found</span><br /><Checkbox /><span className="ml-2 text-sm">report.ready</span><br /><Checkbox /><span className="ml-2 text-sm">alert.critical</span></div></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={() => setAddOpen(false)}>Add Webhook</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// PROFILE SETTINGS SCREEN
// ═══════════════════════════════════════════
function ProfileScreen() {
  // Real backend-connected profile screen.
  // Pulls the current user from the session provider and PATCHes /api/auth/me
  // when the user clicks "Save Changes".
  const { user, refresh } = useSession()
  const [name, setName] = useState(user?.name || '')
  const [title, setTitle] = useState(user?.title || '')
  const [bio, setBio] = useState(user?.bio || '')
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Keep local state in sync if the session user changes (e.g. after a refresh).
  useEffect(() => {
    if (user) {
      setName(user.name || '')
      setTitle(user.title || '')
      setBio(user.bio || '')
    }
  }, [user?.id, user?.name, user?.title, user?.bio])

  const handleSave = async () => {
    setSaving(true)
    setSavedMsg(null)
    setErrorMsg(null)
    try {
      await api.updateMe({ name, title, bio })
      await refresh()
      setSavedMsg('Profile updated successfully.')
    } catch (e: any) {
      setErrorMsg(e?.message || 'Failed to update profile.')
    } finally {
      setSaving(false)
    }
  }

  if (!user) {
    return <FadeIn><div className="p-8 text-center text-muted-foreground">Loading profile…</div></FadeIn>
  }

  const initials = (user.name || user.email || '?')
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase()).join('') || user.email[0]?.toUpperCase()
  const firstName = (name.split(/\s+/)[0] || '')
  const lastName = name.split(/\s+/).slice(1).join(' ')

  return (
    <FadeIn><div className="space-y-6">
      <PH title="Profile Settings" desc="Manage your personal information" />
      {savedMsg && (
        <div className="rounded-md bg-[#1D9E75]/10 border border-[#1D9E75]/30 text-[#1D9E75] text-sm px-3 py-2">{savedMsg}</div>
      )}
      {errorMsg && (
        <div className="rounded-md bg-[#C0392B]/10 border border-[#C0392B]/30 text-[#C0392B] text-sm px-3 py-2">{errorMsg}</div>
      )}
      <Card><CardContent className="p-6">
        <div className="flex items-center gap-6 mb-6">
          <Avatar className="h-20 w-20"><AvatarFallback className="bg-[#5B4FCF] text-white text-2xl">{initials}</AvatarFallback></Avatar>
          <div>
            <h3 className="text-lg font-semibold text-foreground">{user.name || user.email}</h3>
            <p className="text-sm text-muted-foreground">{user.email}</p>
            <Badge variant="secondary" className="mt-2">{roleLabel(user.role)}</Badge>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div><Label>First Name</Label><Input value={firstName} onChange={e => setName(e.target.value + (lastName ? ' ' + lastName : ''))} disabled={saving} /></div>
          <div><Label>Last Name</Label><Input value={lastName} onChange={e => setName(firstName + (e.target.value ? ' ' + e.target.value : ''))} disabled={saving} /></div>
          <div><Label>Email</Label><Input value={user.email} type="email" disabled /></div>
          <div><Label>Title</Label><Input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Principal Scientist" disabled={saving} /></div>
          <div className="md:col-span-2"><Label>Bio</Label><Textarea value={bio} onChange={e => setBio(e.target.value)} placeholder="Tell us about your research focus" rows={3} disabled={saving} /></div>
          <div><Label>Role</Label><Input value={roleLabel(user.role)} disabled /></div>
          <div><Label>Member since</Label><Input value={user.createdAt ? new Date(user.createdAt).toLocaleDateString() : ''} disabled /></div>
        </div>
        <div className="flex justify-end mt-6">
          <Button style={{ backgroundColor: P }} onClick={handleSave} disabled={saving}>
            <Save className="h-4 w-4 mr-1.5" />{saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </div>
      </CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SECURITY SETTINGS SCREEN
// ═══════════════════════════════════════════
function SecuritySettingsScreen() {
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Security Settings" desc="Manage your account security" />
      <Card><CardHeader><CardTitle className="text-base">Password</CardTitle></CardHeader><CardContent className="space-y-4"><div><Label>Current Password</Label><Input type="password" /></div><div><Label>New Password</Label><Input type="password" /></div><div><Label>Confirm Password</Label><Input type="password" /></div><Button style={{ backgroundColor: P }}>Update Password</Button></CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">Two-Factor Authentication</CardTitle></CardHeader><CardContent><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Authenticator App</p><p className="text-xs text-muted-foreground">Use Google Authenticator or similar</p></div><Badge variant="default" className="bg-green-500">Enabled</Badge></div><Separator className="my-3" /><div className="flex items-center justify-between"><div><p className="text-sm font-medium">SMS Backup</p><p className="text-xs text-muted-foreground">Receive codes via SMS</p></div><Switch /></div></CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">Active Sessions</CardTitle></CardHeader><CardContent><div className="space-y-3"><div className="flex items-center justify-between p-3 border rounded-lg"><div className="flex items-center gap-3"><MonitorSmartphone className="h-5 w-5 text-muted-foreground" /><div><p className="text-sm font-medium">Chrome on MacOS</p><p className="text-xs text-muted-foreground">192.168.1.45 - Active now</p></div></div><Badge>Current</Badge></div>
        <div className="flex items-center justify-between p-3 border rounded-lg"><div className="flex items-center gap-3"><Smartphone className="h-5 w-5 text-muted-foreground" /><div><p className="text-sm font-medium">Safari on iPhone</p><p className="text-xs text-muted-foreground">10.0.0.23 - 2 hours ago</p></div></div><Button variant="outline" size="sm">Revoke</Button></div></div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// NOTIFICATIONS SCREEN
// ═══════════════════════════════════════════
function NotificationsScreen() {
  const groups = [
    { title: 'Research', items: [{ label: 'New candidate found', email: true, push: true, inApp: true },{ label: 'Safety alert', email: true, push: true, inApp: true },{ label: 'Report ready', email: true, push: false, inApp: true }] },
    { title: 'Team', items: [{ label: 'Team member invited', email: true, push: false, inApp: true },{ label: 'Annotation on candidate', email: false, push: false, inApp: true },{ label: 'Query shared with you', email: true, push: true, inApp: true }] },
    { title: 'Billing', items: [{ label: 'Payment processed', email: true, push: false, inApp: true },{ label: 'Usage limit warning', email: true, push: true, inApp: true },{ label: 'Invoice ready', email: true, push: false, inApp: true }] },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Notification Preferences" desc="Choose how you want to be notified" />
      {groups.map(g => (<Card key={g.title}><CardHeader className="pb-2"><CardTitle className="text-base">{g.title}</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Event</TableHead><TableHead className="text-center">Email</TableHead><TableHead className="text-center">Push</TableHead><TableHead className="text-center">In-App</TableHead></TableRow></TableHeader>
        <TableBody>{g.items.map(item => (<TableRow key={item.label}><TableCell className="text-sm">{item.label}</TableCell><TableCell className="text-center"><Switch defaultChecked={item.email} /></TableCell><TableCell className="text-center"><Switch defaultChecked={item.push} /></TableCell><TableCell className="text-center"><Switch defaultChecked={item.inApp} /></TableCell></TableRow>))}</TableBody></Table></CardContent></Card>))}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// PREFERENCES SCREEN
// ═══════════════════════════════════════════
function PreferencesScreen() {
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Display Preferences" desc="Customize your DrugOS experience" />
      <Card><CardContent className="p-6 space-y-6"><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Theme</p><p className="text-xs text-muted-foreground">Choose light or dark mode</p></div><Select defaultValue="light"><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="light">Light</SelectItem><SelectItem value="dark">Dark</SelectItem><SelectItem value="system">System</SelectItem></SelectContent></Select></div>
        <Separator /><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Default Results per Page</p><p className="text-xs text-muted-foreground">Number of results shown in tables</p></div><Select defaultValue="25"><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="10">10</SelectItem><SelectItem value="25">25</SelectItem><SelectItem value="50">50</SelectItem><SelectItem value="100">100</SelectItem></SelectContent></Select></div>
        <Separator /><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Compact Mode</p><p className="text-xs text-muted-foreground">Show more data with less spacing</p></div><Switch /></div>
        <Separator /><div className="flex items-center justify-between"><div><p className="text-sm font-medium">Language</p><p className="text-xs text-muted-foreground">Interface language</p></div><Select defaultValue="en"><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="en">English</SelectItem><SelectItem value="zh">Chinese</SelectItem><SelectItem value="ja">Japanese</SelectItem></SelectContent></Select></div>
      </CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// PRIVACY POLICY SCREEN
// ═══════════════════════════════════════════
function PrivacyPolicyScreen() {
  const sections = [
    { title: 'Data Collection', content: 'DrugOS collects personal information such as your name, email address, organization, and usage data. We also collect research queries, candidate analyses, and evidence packages you generate. Medical and pharmaceutical data you input is treated as Protected Health Information (PHI) under HIPAA when applicable. We do not sell your personal data to third parties under any circumstances.' },
    { title: 'Data Usage', content: 'Your data is used solely to provide and improve the DrugOS platform services. This includes processing your research queries, generating drug repurposing candidates, maintaining your workspace, and providing customer support. We may use anonymized, aggregated usage patterns to improve our algorithms and platform performance. No individual user data is ever shared in analytics or reports.' },
    { title: 'Data Storage & Security', content: 'All data is encrypted at rest using AES-256 and in transit using TLS 1.3. Data is stored in SOC 2 Type II certified data centers with geographic residency options. We maintain strict access controls with role-based permissions, audit logging, and multi-factor authentication. PHI data is stored in HIPAA-compliant infrastructure with Business Associate Agreements available for enterprise customers.' },
    { title: 'Your Rights', content: 'You have the right to access, export, correct, and delete your personal data at any time through the platform settings or by contacting our Data Protection Officer. Under GDPR, you can exercise your right to data portability and restriction of processing. Under CCPA, you can request disclosure of what personal data is collected and request deletion. We respond to all such requests within 30 days.' },
    { title: 'Cookies & Tracking', content: 'We use essential cookies for authentication and session management. Analytics cookies are optional and can be disabled in your browser settings. We do not use third-party advertising trackers. Our cookie consent banner allows you to granularly control which non-essential cookies are active on your browser.' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Privacy Policy" desc="Last updated: June 1, 2026" />
      <Card><CardContent className="p-6"><div className="prose prose-sm max-w-none">{sections.map(s => (<div key={s.title} className="mb-6"><h3 className="text-lg font-semibold mb-2">{s.title}</h3><p className="text-sm text-muted-foreground leading-relaxed">{s.content}</p></div>))}</div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// TERMS OF SERVICE SCREEN
// ═══════════════════════════════════════════
function TermsScreen() {
  const sections = [
    { title: 'Acceptance of Terms', content: 'By accessing and using DrugOS, you agree to be bound by these Terms of Service and all applicable laws and regulations. If you do not agree with any part of these terms, you may not use the platform. These terms apply to all users including academic researchers, pharmaceutical companies, CRO partners, and enterprise customers.' },
    { title: 'License & Usage Rights', content: 'DrugOS grants you a non-exclusive, non-transferable license to use the platform according to your subscription plan. You may not reverse-engineer, decompile, or create derivative works from the platform. Query results and candidate analyses generated by the platform are for research purposes only and do not constitute medical advice or regulatory approval.' },
    { title: 'Intellectual Property', content: 'All drug repurposing predictions, knowledge graph data, and AI-generated insights remain the intellectual property of DrugOS Corp unless a separate licensing agreement (Discovery Deal) is executed. Under a Discovery Deal, exclusive rights to specific validated candidates may be transferred to the licensee with full evidence packages and regulatory support documentation.' },
    { title: 'Limitation of Liability', content: 'DrugOS provides AI-generated predictions for research purposes only. We do not guarantee the accuracy, completeness, or regulatory approvability of any prediction. DrugOS is not liable for any decisions made based on platform outputs. Users are solely responsible for validating predictions through independent verification, wet-lab testing, and regulatory consultation before any clinical or commercial use.' },
    { title: 'Termination', content: 'Either party may terminate the subscription with 30 days written notice. DrugOS reserves the right to suspend accounts that violate these terms. Upon termination, you may export your data within 30 days, after which it will be permanently deleted from our systems. Outstanding Discovery Deals remain in effect according to their individual contract terms.' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Terms of Service" desc="Last updated: June 1, 2026" />
      <Card><CardContent className="p-6">{sections.map(s => (<div key={s.title} className="mb-6"><h3 className="text-lg font-semibold mb-2">{s.title}</h3><p className="text-sm text-muted-foreground leading-relaxed">{s.content}</p></div>))}</CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// COMPLIANCE SCREEN
// ═══════════════════════════════════════════
function ComplianceScreen() {
  const frameworks = [
    { name: 'HIPAA', desc: 'Health Insurance Portability and Accountability Act', status: 'compliant', lastAudit: 'May 2026', nextAudit: 'Nov 2026' },
    { name: 'GDPR', desc: 'General Data Protection Regulation', status: 'compliant', lastAudit: 'Apr 2026', nextAudit: 'Oct 2026' },
    { name: 'SOC 2 Type II', desc: 'Service Organization Control', status: 'compliant', lastAudit: 'Mar 2026', nextAudit: 'Mar 2027' },
    { name: '21 CFR Part 11', desc: 'FDA Electronic Records and Signatures', status: 'compliant', lastAudit: 'Feb 2026', nextAudit: 'Feb 2027' },
    { name: 'GxP', desc: 'Good Practice Guidelines for Pharma', status: 'partial', lastAudit: 'Jun 2026', nextAudit: 'Sep 2026' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Compliance Dashboard" />
      <PH title="Compliance Dashboard" desc="Monitor your regulatory compliance status" actions={<Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Download BAA</Button>} />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
        <SC title="Compliant Frameworks" value="4/5" icon={ShieldCheck} /><SC title="Last Audit" value="Jun 2026" icon={FileCheck} /><SC title="Open Items" value="2" icon={AlertTriangle} />
      </div>
      <div className="space-y-4">{frameworks.map(f => (<Card key={f.name}><CardContent className="p-5"><div className="flex items-center justify-between"><div className="flex items-center gap-3"><div className={`w-10 h-10 rounded-lg flex items-center justify-center ${f.status === 'compliant' ? 'bg-green-50' : 'bg-amber-50'}`}>{f.status === 'compliant' ? <CheckCircle2 className="h-5 w-5 text-green-500" /> : <AlertCircle className="h-5 w-5 text-amber-500" />}</div><div><h3 className="font-semibold">{f.name}</h3><p className="text-xs text-muted-foreground">{f.desc}</p></div></div>
        <div className="text-right"><Badge variant={f.status === 'compliant' ? 'default' : 'secondary'}>{f.status}</Badge><p className="text-xs text-muted-foreground mt-1">Last: {f.lastAudit} | Next: {f.nextAudit}</p></div></div></CardContent></Card>))}</div>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// HELP CENTER SCREEN
// ═══════════════════════════════════════════
function HelpCenterScreen() {
  const [searchQ, setSearchQ] = useState('')
  const categories = [
    { icon: <Search className="w-5 h-5" />, title: 'Getting Started', desc: 'Learn the basics of DrugOS', articles: 8 },
    { icon: <FlaskConical className="w-5 h-5" />, title: 'Disease Search', desc: 'Query diseases and find candidates', articles: 12 },
    { icon: <Network className="w-5 h-5" />, title: 'Knowledge Graph', desc: 'Explore the biomedical KG', articles: 6 },
    { icon: <Shield className="w-5 h-5" />, title: 'Safety & Compliance', desc: 'HIPAA, GDPR, and safety profiles', articles: 9 },
    { icon: <Code className="w-5 h-5" />, title: 'API & Developer', desc: 'Integration guides and API docs', articles: 15 },
    { icon: <CreditCard className="w-5 h-5" />, title: 'Billing & Plans', desc: 'Subscriptions, invoices, and deals', articles: 7 },
  ]
  const faqs = [
    { q: 'How do I search for a disease?', a: 'Use the Disease Search bar on the homepage or dashboard. Type a disease name, ICD code, or therapeutic area. Results appear with AI-ranked drug candidates.' },
    { q: 'What is a composite score?', a: 'The composite score (0-100) combines knowledge graph evidence, molecular similarity, safety profile, clinical trial data, and IP status into a single ranking metric.' },
    { q: 'Can I export my results?', a: 'Yes, you can export candidate lists, evidence packages, and reports in PDF, CSV, or JSON format from any results page.' },
    { q: 'How do I invite team members?', a: 'Go to Team Members in the sidebar, click "Invite Member", enter their email and select a role. They will receive an email invitation.' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Help Center" desc="Find answers and learn how to use DrugOS" />
      <div className="relative max-w-xl mx-auto mb-6"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" /><Input placeholder="Search help articles..." value={searchQ} onChange={e => setSearchQ(e.target.value)} className="pl-11 h-12 text-base" /></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {categories.map(c => (<Card key={c.title} className="hover:shadow-md transition-shadow cursor-pointer"><CardContent className="p-5"><div className="w-10 h-10 rounded-lg bg-[#5B4FCF]/10 text-[#5B4FCF] flex items-center justify-center mb-3">{c.icon}</div><h3 className="font-semibold text-sm">{c.title}</h3><p className="text-xs text-muted-foreground mt-1">{c.desc}</p><p className="text-xs text-[#5B4FCF] mt-2">{c.articles} articles</p></CardContent></Card>))}
      </div>
      <Card><CardHeader><CardTitle className="text-base">Frequently Asked Questions</CardTitle></CardHeader><CardContent><Accordion type="single" collapsible>{faqs.map((f, i) => (<AccordionItem key={i} value={`faq-${i}`}><AccordionTrigger className="text-sm font-medium">{f.q}</AccordionTrigger><AccordionContent className="text-sm text-muted-foreground">{f.a}</AccordionContent></AccordionItem>))}</Accordion></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SUPPORT TICKETS SCREEN
// ═══════════════════════════════════════════
function TicketsScreen() {
  const [createOpen, setCreateOpen] = useState(false)
  const tickets = [
    { id: 'TKT-142', subject: 'Cannot export evidence package', priority: 'high', status: 'open', created: '2 hrs ago', assignee: 'Support Team' },
    { id: 'TKT-139', subject: 'API rate limit too restrictive', priority: 'medium', status: 'in_progress', created: '1 day ago', assignee: 'Engineering' },
    { id: 'TKT-135', subject: 'SSO login loop with Okta', priority: 'high', status: 'in_progress', created: '2 days ago', assignee: 'Engineering' },
    { id: 'TKT-128', subject: 'Request for batch query feature', priority: 'low', status: 'resolved', created: '1 week ago', assignee: 'Product' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Support Tickets" desc="Manage your support requests" actions={<Button style={{ backgroundColor: P }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />New Ticket</Button>} />
      <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Subject</TableHead><TableHead>Priority</TableHead><TableHead>Status</TableHead><TableHead>Assignee</TableHead></TableRow></TableHeader>
        <TableBody>{tickets.map(t => (<TableRow key={t.id}><TableCell className="font-mono text-sm">{t.id}</TableCell><TableCell className="font-medium">{t.subject}</TableCell>
          <TableCell><Badge variant={t.priority === 'high' ? 'destructive' : t.priority === 'medium' ? 'secondary' : 'outline'}>{t.priority}</Badge></TableCell>
          <TableCell><Badge variant={t.status === 'open' ? 'default' : t.status === 'in_progress' ? 'secondary' : 'outline'}>{t.status.replace('_', ' ')}</Badge></TableCell>
          <TableCell className="text-muted-foreground">{t.assignee}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
      <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create Support Ticket</DialogTitle></DialogHeader>
        <div className="space-y-4"><div><Label>Subject</Label><Input placeholder="Brief description of your issue" /></div><div><Label>Priority</Label><Select><SelectTrigger><SelectValue placeholder="Select priority" /></SelectTrigger><SelectContent><SelectItem value="low">Low</SelectItem><SelectItem value="medium">Medium</SelectItem><SelectItem value="high">High</SelectItem></SelectContent></Select></div><div><Label>Description</Label><Textarea placeholder="Describe your issue in detail..." className="min-h-[100px]" /></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button style={{ backgroundColor: P }} onClick={() => setCreateOpen(false)}>Submit Ticket</Button></DialogFooter>
      </DialogContent></Dialog>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// SYSTEM STATUS SCREEN
// ═══════════════════════════════════════════
function SystemStatusScreen() {
  // FE-009 ROOT FIX: call the real /api/system/status endpoint. The previous
  // code rendered 6 hardcoded "operational" services with fabricated
  // uptime/latency numbers. An admin could not tell whether the platform
  // was actually healthy or whether the green checkmarks were real.
  const { data, loading, error, refetch } = useApiResource(() => api.getSystemStatus(), [])
  const services = data ? Object.entries(data.services).map(([key, s]) => ({
    key,
    name: s.service,
    available: s.available,
    reason: s.reason,
  })) : []
  const allOperational = services.length > 0 && services.every(s => s.available)
  return (
    <FadeIn><div className="space-y-6">
      <PH title="System Status" desc="Real-time platform health monitoring" actions={<Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="h-4 w-4 mr-1.5" />Refresh</Button>} />
      {error && <ErrorDisplay error={error} onRetry={refetch} />}
      {loading && <LoadingSpinner label="Loading system status..." />}
      {!loading && !error && (
        <>
          <Card className={allOperational ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}><CardContent className="p-5"><div className="flex items-center gap-3">{allOperational ? <CheckCircle2 className="h-6 w-6 text-green-500" /> : <AlertCircle className="h-6 w-6 text-amber-500" />}<div><p className={`font-semibold ${allOperational ? 'text-green-700' : 'text-amber-700'}`}>{allOperational ? 'All Systems Operational' : 'Some Services Unavailable'}</p><p className={`text-sm ${allOperational ? 'text-green-600' : 'text-amber-600'}`}>Last checked: {new Date(data!.generatedAt).toLocaleString()}</p></div></div></CardContent></Card>
          <Card><CardHeader className="pb-2"><CardTitle className="text-base">Service Health</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Status</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
            <TableBody>{services.map(s => (<TableRow key={s.key}><TableCell className="font-medium">{s.name}</TableCell><TableCell><div className="flex items-center gap-2"><span className={`w-2.5 h-2.5 rounded-full ${s.available ? 'bg-green-500' : 'bg-amber-500'}`} /><span className="text-sm">{s.available ? 'Operational' : 'Unavailable'}</span></div></TableCell><TableCell className="text-xs text-muted-foreground">{s.reason || '—'}</TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
        </>
      )}
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// INVESTOR DASHBOARD SCREEN
// ═══════════════════════════════════════════
function InvestorDashboardScreen() {
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/investor-metrics
  // endpoint exists. The previous code rendered fabricated ARR / NRR /
  // LTV/CAC / revenue projections as if they were real financials.
  // These numbers are strategy-team projections, NOT operational data.
  const revenueData = [{ year: '2026', revenue: 12, expense: 18, ebitda: -6 },{ year: '2027', revenue: 35, expense: 28, ebitda: 7 },{ year: '2028', revenue: 85, expense: 42, ebitda: 43 },{ year: '2029', revenue: 180, expense: 65, ebitda: 115 },{ year: '2030', revenue: 350, expense: 95, ebitda: 255 }]
  const marketData = [{ name: 'TAM', value: 50, fill: P },{ name: 'SAM', value: 15, fill: G },{ name: 'SOM', value: 3, fill: O }]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Investor Dashboard" />
      <PH title="Investor Dashboard" desc="Financial metrics and growth projections" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <SC title="ARR" value="$12M" icon={DollarSign} trend="+190%" /><SC title="Customers" value="142" icon={Users} trend="+85%" /><SC title="NRR" value="135%" icon={TrendingUp} /><SC title="LTV/CAC" value="8.2x" icon={Target} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Revenue Projections ($M)</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><BarChart data={revenueData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="year" /><YAxis /><RechartsTooltip /><Bar dataKey="revenue" fill={P} radius={[4, 4, 0, 0]} /><Bar dataKey="expense" fill={O} radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Market Sizing ($B)</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={marketData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={4} dataKey="value">{marketData.map((d, i) => <Cell key={i} fill={d.fill} />)}</Pie><RechartsTooltip /><Legend /></PieChart></ResponsiveContainer></div></CardContent></Card>
      </div>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Competitive Moat Analysis</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><RadarChart data={[{ subject: 'Data Volume', DrugOS: 95, BenevolentAI: 72, Recursion: 60 },{ subject: 'Explainability', DrugOS: 90, BenevolentAI: 65, Recursion: 45 },{ subject: 'Safety', DrugOS: 88, BenevolentAI: 70, Recursion: 55 },{ subject: 'API Quality', DrugOS: 92, BenevolentAI: 60, Recursion: 70 },{ subject: 'Freshness', DrugOS: 85, BenevolentAI: 75, Recursion: 65 },{ subject: 'Clinical', DrugOS: 82, BenevolentAI: 78, Recursion: 50 }]}>
        <PolarGrid /><PolarAngleAxis dataKey="subject" className="text-xs" /><PolarRadiusAxis /><Radar name="DrugOS" dataKey="DrugOS" stroke={P} fill={P} fillOpacity={0.2} /><Radar name="BenevolentAI" dataKey="BenevolentAI" stroke={O} fill={O} fillOpacity={0.1} /><Radar name="Recursion" dataKey="Recursion" stroke="#06B6D4" fill="#06B6D4" fillOpacity={0.1} /><Legend /></RadarChart></ResponsiveContainer></div></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// CAP TABLE SCREEN
// ═══════════════════════════════════════════
function CapTableScreen() {
  // FE-009 ROOT FIX (v2): DemoDataBanner added — no /api/cap-table endpoint
  // exists. The previous code rendered fabricated equity distribution
  // ("Founders 40M / 40%", etc.) as if it were real ownership data.
  // Cap table data is sensitive legal information that would live in
  // Carta/Pulley, not in this app's DB. The numbers below are illustrative.
  const holders = [
    { name: 'Founders', shares: '40M', pct: 40, type: 'Common', vesting: '4yr cliff' },
    { name: 'Series A Investors', shares: '20M', pct: 20, type: 'Preferred', vesting: '-' },
    { name: 'Angel Investors', shares: '8M', pct: 8, type: 'Preferred', vesting: '-' },
    { name: 'Employee Pool', shares: '15M', pct: 15, type: 'Options', vesting: '4yr cliff' },
    { name: 'Pre-Seed Investors', shares: '7M', pct: 7, type: 'SAFE', vesting: '-' },
    { name: 'Advisors', shares: '2M', pct: 2, type: 'Options', vesting: '2yr' },
    { name: 'Unissued', shares: '8M', pct: 8, type: '-', vesting: '-' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <DemoDataBanner screenName="Cap Table" />
      <PH title="Cap Table" desc="Equity distribution and ownership" actions={<Button variant="outline" size="sm"><Download className="h-4 w-4 mr-1.5" />Export</Button>} />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <SC title="Total Shares" value="100M" icon={Layers} /><SC title="Fully Diluted Valuation" value="$180M" icon={DollarSign} /><SC title="Price per Share" value="$1.80" icon={TrendingUp} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card><CardHeader className="pb-2"><CardTitle className="text-base">Ownership Distribution</CardTitle></CardHeader><CardContent><div className="h-64"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={holders.map(h => ({ name: h.name, value: h.pct }))} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">{holders.map((_, i) => <Cell key={i} fill={CC[i % CC.length]} />)}</Pie><RechartsTooltip /><Legend /></PieChart></ResponsiveContainer></div></CardContent></Card>
        <Card><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Holder</TableHead><TableHead>Shares</TableHead><TableHead>%</TableHead><TableHead>Type</TableHead></TableRow></TableHeader>
          <TableBody>{holders.map(h => (<TableRow key={h.name}><TableCell className="font-medium">{h.name}</TableCell><TableCell>{h.shares}</TableCell><TableCell><div className="flex items-center gap-2"><Progress value={h.pct} className="w-16 h-2" />{h.pct}%</div></TableCell><TableCell><Badge variant="outline">{h.type}</Badge></TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
      </div>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// CHANGELOG SCREEN
// ═══════════════════════════════════════════
function ChangelogScreen() {
  const releases = [
    { version: 'v2.4.0', date: 'Jun 1, 2026', type: 'major', changes: ['GxP Validated Mode for regulated environments', 'HIPAA BAA portal for enterprise customers', 'Discovery Deal workflow with licensing management', 'CRO Sponsor Isolation for multi-tenant data privacy'] },
    { version: 'v2.3.2', date: 'May 15, 2026', type: 'patch', changes: ['Fixed: Knowledge graph query timeout for rare diseases', 'Improved: API rate limiting now supports burst mode', 'Fixed: Report generation failing for >50 candidates'] },
    { version: 'v2.3.0', date: 'May 1, 2026', type: 'minor', changes: ['Batch query mode for multiple diseases at once', 'New comparative radar charts for drug candidates', 'SCIM provisioning for automatic user management', 'Enhanced audit logging with IP tracking'] },
    { version: 'v2.2.0', date: 'Apr 1, 2026', type: 'minor', changes: ['GraphQL API endpoint (beta)', 'Real-time collaboration on evidence packages', 'Drug-drug interaction checker', 'Molecular similarity search'] },
  ]
  const typeColors: Record<string, string> = { major: P, minor: G, patch: O }
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Changelog" desc="What's new in DrugOS" actions={<Button variant="outline" size="sm"><BookOpen className="h-4 w-4 mr-1.5" />RSS Feed</Button>} />
      <div className="space-y-4">{releases.map(r => (<Card key={r.version}><CardContent className="p-5"><div className="flex items-center justify-between mb-3"><div className="flex items-center gap-3"><Badge style={{ backgroundColor: typeColors[r.type], color: 'white' }}>{r.type}</Badge><h3 className="font-bold text-lg">{r.version}</h3></div><span className="text-sm text-muted-foreground">{r.date}</span></div>
        <ul className="space-y-2">{r.changes.map((c, i) => (<li key={i} className="flex items-start gap-2 text-sm"><Check className="h-4 w-4 text-green-500 shrink-0 mt-0.5" /><span className="text-muted-foreground">{c}</span></li>))}</ul>
      </CardContent></Card>))}</div>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// ROADMAP SCREEN
// ═══════════════════════════════════════════
function RoadmapScreen() {
  const quarters = [
    { quarter: 'Q2 2026', items: [{ title: 'GxP Validated Mode', status: 'completed' },{ title: 'Discovery Deal Workflow', status: 'completed' },{ title: 'Batch Query Mode', status: 'completed' }] },
    { quarter: 'Q3 2026', items: [{ title: 'Custom Model Deployment', status: 'in_progress' },{ title: 'Multi-Language KG', status: 'in_progress' },{ title: 'Enhanced CRO Dashboard', status: 'planned' }] },
    { quarter: 'Q4 2026', items: [{ title: 'Real-time KG Updates', status: 'planned' },{ title: 'AI Explanation Engine v2', status: 'planned' },{ title: 'Mobile App Beta', status: 'planned' }] },
    { quarter: 'Q1 2027', items: [{ title: 'Federated Learning', status: 'planned' },{ title: 'Regulatory Submission Pack', status: 'planned' },{ title: 'Partner API Marketplace', status: 'planned' }] },
  ]
  const statusColors: Record<string, string> = { completed: G, in_progress: P, planned: '#94A3B8' }
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Product Roadmap" desc="Our development plans and timeline" />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {quarters.map(q => (<Card key={q.quarter}><CardHeader><CardTitle className="text-base">{q.quarter}</CardTitle></CardHeader><CardContent><div className="space-y-3">{q.items.map(item => (<div key={item.title} className="flex items-center gap-3"><div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: statusColors[item.status] }} /><div><p className="text-sm font-medium">{item.title}</p><p className="text-xs text-muted-foreground capitalize">{item.status.replace('_', ' ')}</p></div></div>))}</div></CardContent></Card>))}
      </div>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// FEEDBACK SCREEN
// ═══════════════════════════════════════════
function FeedbackScreen() {
  const [submitted, setSubmitted] = useState(false)
  const existingFeedback = [
    { title: 'Dark mode support', votes: 42, status: 'planned', category: 'UI' },
    { title: 'GraphQL API', votes: 38, status: 'in_progress', category: 'API' },
    { title: 'Mobile responsive dashboard', votes: 31, status: 'planned', category: 'UI' },
    { title: 'Export to PowerPoint', votes: 24, status: 'completed', category: 'Reports' },
    { title: 'Custom scoring weights', votes: 19, status: 'planned', category: 'Research' },
  ]
  return (
    <FadeIn><div className="space-y-6">
      <PH title="Feedback & Feature Requests" desc="Help us improve DrugOS" />
      <Card><CardContent className="p-6"><div className="space-y-4"><div><Label>Feature Request</Label><Input placeholder="What feature would you like?" /></div><div><Label>Category</Label><Select><SelectTrigger><SelectValue placeholder="Select category" /></SelectTrigger><SelectContent><SelectItem value="ui">UI/UX</SelectItem><SelectItem value="api">API</SelectItem><SelectItem value="research">Research</SelectItem><SelectItem value="reports">Reports</SelectItem><SelectItem value="other">Other</SelectItem></SelectContent></Select></div><div><Label>Description</Label><Textarea placeholder="Describe your feature request..." className="min-h-[80px]" /></div>
        <Button style={{ backgroundColor: P }} onClick={() => setSubmitted(true)}>{submitted ? 'Submitted!' : 'Submit Request'}</Button></div></CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">Popular Requests</CardTitle></CardHeader><CardContent className="p-0"><Table><TableHeader><TableRow><TableHead>Feature</TableHead><TableHead>Category</TableHead><TableHead>Votes</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
        <TableBody>{existingFeedback.map(f => (<TableRow key={f.title}><TableCell className="font-medium">{f.title}</TableCell><TableCell><Badge variant="outline">{f.category}</Badge></TableCell><TableCell><div className="flex items-center gap-1"><ArrowUpRight className="h-3 w-3 text-green-500" />{f.votes}</div></TableCell>
          <TableCell><Badge variant={f.status === 'completed' ? 'default' : f.status === 'in_progress' ? 'secondary' : 'outline'}>{f.status.replace('_', ' ')}</Badge></TableCell></TableRow>))}</TableBody></Table></CardContent></Card>
    </div></FadeIn>
  )
}

// ═══════════════════════════════════════════
// EXPORT ALL SCREENS
// ═══════════════════════════════════════════
export const allScreens: Record<string, React.ComponentType> = {
  // DASH
  pipeline: PipelineScreen,
  analytics: AnalyticsScreen,
  // COLLAB
  team: TeamMembersScreen,
  projects: ProjectsScreen,
  'shared-queries': SharedQueriesScreen,
  annotations: AnnotationsScreen,
  // DATA
  'data-sources': DataSourcesScreen,
  'graph-stats': GraphStatisticsScreen,
  quality: QualityScreen,
  // BILL
  subscription: SubscriptionScreen,
  usage: UsageScreen,
  deals: DealsScreen,
  invoices: InvoicesScreen,
  // ADMIN
  users: UsersAdminScreen,
  roles: RolesScreen,
  sso: SSOScreen,
  'audit-logs': AuditLogsScreen,
  'feature-flags': FeatureFlagsScreen,
  // DEV
  'api-docs': APIDocsScreen,
  'api-keys': APIKeysScreen,
  playground: PlaygroundScreen,
  webhooks: WebhooksScreen,
  // SET
  profile: ProfileScreen,
  security: SecuritySettingsScreen,
  notifications: NotificationsScreen,
  preferences: PreferencesScreen,
  // LEGAL
  privacy: PrivacyPolicyScreen,
  terms: TermsScreen,
  compliance: ComplianceScreen,
  // SUPP
  'help-center': HelpCenterScreen,
  tickets: TicketsScreen,
  'system-status': SystemStatusScreen,
  // INV
  'investor-dashboard': InvestorDashboardScreen,
  'cap-table': CapTableScreen,
  // MISC
  changelog: ChangelogScreen,
  roadmap: RoadmapScreen,
  feedback: FeedbackScreen,
}
