'use client';

/**
 * FE-064 ROOT FIX: Admin / Billing / Investor / Executive screen helpers.
 *
 * Previously this file declared 10 hardcoded arrays of fake analytics data:
 *   usageTrendData, endpointData, revenueProjectionData, marketSizingData,
 *   radarData, comparableData, pipelinePredictData, royaltyData,
 *   apiUsageTimeData, moatData
 *
 * These were presented as real analytics in admin/billing/investor screens.
 * Per the issue: "An admin viewing Pipeline Analytics sees fake success
 * rates (15%, 30%, 45%, 65%, 90% by phase). An investor viewing Investor
 * Dashboard sees fake revenue projections ($12M → $350M over 5 years)."
 *
 * Root fix:
 *   1. DELETE every hardcoded analytics array. They are never rendered in
 *      the current codebase (grep confirms zero usages outside this file)
 *      but they were a footgun — any future screen importing them would
 *      display fabricated numbers to decision-makers.
 *   2. Provide typed React Query / fetch hooks that call REAL API endpoints.
 *      If the backend has no data yet, the hook returns null and the UI
 *      renders an EmptyState ("No data available") — never fabricated
 *      numbers.
 *   3. Keep the presentational helpers (StatCard, ColorProgress,
 *      StatusBadge, PageHeader) since they are pure UI primitives with no
 *      data dependency.
 */

import { useState, useEffect, useCallback } from 'react';
import { useDrugOSNav } from './nav-context';
import {
  CreditCard, TrendingUp, Shield, Scale, Code, Settings, HelpCircle,
  FileQuestion, Users, CheckCircle, AlertTriangle, Clock, Download,
  Plus, Search, Filter, X, ChevronRight, ChevronDown, ExternalLink,
  Key, Globe, Lock, Eye, Mail, Bell, Zap, RefreshCw, Copy, Trash2,
  Edit, MoreVertical, ArrowUpRight, ArrowDownRight, DollarSign,
  BarChart3, PieChart, Target, BookOpen, MessageSquare, Send,
  FileText, Database, Server, Activity, Info, Save, Upload,
  ChevronLeft, Check, Minus, PlusCircle, AlertCircle, ShieldCheck,
  Building, UserCog, MapPin, Palette, Plug, BellRing, ScrollText,
  Calculator, Gauge, Workflow, Percent, ShoppingCart,
  ToggleRight, FolderLock, KeyRound, ScanSearch, Cookie, Archive,
  BookMarked, Play, Webhook, Braces, Upload as UploadIcon,
  Settings2, User, Smartphone, Headphones, Ticket, Library,
  MessageCircle, Lightbulb, RotateCcw, Heart as HeartIcon, ShieldAlert,
  LogIn, UserCheck, Hourglass, Siren
} from 'lucide-react';
import {
  Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog';
import {
  Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '@/components/ui/accordion';
import { ScrollArea } from '@/components/ui/scroll-area';

// FE-065: app-router.tsx owns the dashboard / notification / billing data
// fetching now. We do NOT import mock-data here — all analytics shown in
// admin/billing/investor screens must come from a real API hook returning
// either live data or null (empty state).

// ─── Design Tokens ───
const C = { primary: '#5B4FCF', green: '#1D9E75', orange: '#D4853A', red: '#C0392B', bg: '#F8F8FA' };

// ─── Shared Helpers ───
function PageHeader({ title, desc, actions }: { title: string; desc?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-6">
      <div><h1 className="text-2xl font-bold text-foreground">{title}</h1>{desc && <p className="text-sm text-muted-foreground mt-1">{desc}</p>}</div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
    </div>
  );
}

function StatCard({ title, value, subtitle, icon: Icon, trend }: { title: string; value: string | number; subtitle?: string; icon?: React.ComponentType<{className?:string}>; trend?: string }) {
  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm text-muted-foreground">{title}</p>
            <p className="text-2xl font-bold text-foreground mt-1">{value}</p>
            {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
            {trend && <p className={`text-xs mt-1 font-medium ${trend.startsWith('+') ? 'text-emerald-600' : 'text-red-500'}`}>{trend}</p>}
          </div>
          {Icon && <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center"><Icon className="h-5 w-5 text-primary" /></div>}
        </div>
      </CardContent>
    </Card>
  );
}

function ColorProgress({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = Math.min((value / max) * 100, 100);
  const color = pct > 90 ? C.red : pct > 75 ? C.orange : pct > 50 ? '#EAB308' : C.green;
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm"><span className="text-muted-foreground">{label}</span><span className="font-medium">{value.toLocaleString()}/{max.toLocaleString()}</span></div>
      <div className="h-2 w-full bg-muted rounded-full overflow-hidden"><div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} /></div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
    active: 'default', operational: 'default', healthy: 'default', Paid: 'default',
    inactive: 'secondary', degraded: 'secondary', pending: 'outline', invited: 'outline',
    suspended: 'destructive', error: 'destructive',
  };
  return <Badge variant={map[status] ?? 'outline'}>{status}</Badge>;
}

// ─── FE-064 ROOT FIX: Empty state for analytics with no real data yet ───
export function EmptyState({
  title = 'No data available',
  description = 'Connect the relevant backend service to populate this view.',
  icon: Icon = Database,
}: {
  title?: string;
  description?: string;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-12 text-center">
        <Icon className="h-10 w-10 text-muted-foreground mb-3" />
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground mt-1 max-w-md">{description}</p>
      </CardContent>
    </Card>
  );
}

// ─── FE-064 ROOT FIX: Real API hooks for analytics ───
// Each hook returns { data, loading, error }. When the backend has no data,
// `data` is null and the UI must render <EmptyState />. We NEVER fabricate
// numbers.

export interface UsageTrendPoint { month: string; queries: number; api: number; compute: number; }
export interface EndpointStat { name: string; calls: number; errors: number; }
export interface RevenueProjectionPoint { year: string; revenue: number; expense: number; ebitda: number; }
export interface MarketSizingSlice { name: string; value: number; }
export interface RadarPoint { subject: string; [competitor: string]: string | number; }
export interface ComparableRow { name: string; rev: number; growth: number; ev: number; multiple: number; }
export interface PipelinePredictRow { name: string; count: number; successRate: number; }
export interface RoyaltyRow { volume: string; rate: number; projected: number; }
export interface ApiUsageTimePoint { hour: string; calls: number; }
export interface MoatPoint { category: string; score: number; }

interface AnalyticsState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * Generic fetch hook: calls a JSON endpoint and returns { data, loading, error }.
 * Used by every analytics hook below so we have ONE place to maintain
 * fetch + JSON-parse + error-normalization logic.
 */
function useAnalyticsFetch<T>(endpoint: string | null): AnalyticsState<T> {
  const [state, setState] = useState<AnalyticsState<T>>({ data: null, loading: false, error: null });
  useEffect(() => {
    if (!endpoint) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    fetch(endpoint, { credentials: 'include' })
      .then(async (res) => {
        const text = await res.text();
        let body: any = null;
        if (text) {
          try { body = JSON.parse(text); } catch { body = { raw: text }; }
        }
        if (!res.ok) {
          throw new Error(body?.message || `Request failed with status ${res.status}`);
        }
        return body as T;
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        if (!cancelled) setState({ data: null, loading: false, error: msg });
      });
    return () => { cancelled = true; };
  }, [endpoint]);
  return state;
}

/**
 * Usage trend (queries / api calls / compute minutes over last 6 months).
 * Source: GET /api/analytics/usage-trend (admin-only).
 */
export function useUsageTrend() {
  return useAnalyticsFetch<{ items: UsageTrendPoint[] }>('/api/analytics/usage-trend');
}

/**
 * Endpoint call distribution + error counts.
 * Source: GET /api/analytics/endpoints (admin-only).
 */
export function useEndpointStats() {
  return useAnalyticsFetch<{ items: EndpointStat[] }>('/api/analytics/endpoints');
}

/**
 * Revenue projection (5-year forward).
 * Source: GET /api/analytics/revenue-projection (executive-only).
 */
export function useRevenueProjection() {
  return useAnalyticsFetch<{ items: RevenueProjectionPoint[] }>('/api/analytics/revenue-projection');
}

/**
 * Market sizing (TAM / SAM / SOM).
 * Source: GET /api/analytics/market-sizing (executive-only).
 */
export function useMarketSizing() {
  return useAnalyticsFetch<{ items: MarketSizingSlice[] }>('/api/analytics/market-sizing');
}

/**
 * Competitive radar (DrugOS vs BenevolentAI / Recursion / OpenTargets).
 * Source: GET /api/analytics/competitor-radar (executive-only).
 */
export function useCompetitorRadar() {
  return useAnalyticsFetch<{ items: RadarPoint[] }>('/api/analytics/competitor-radar');
}

/**
 * Comparable companies table (revenue, growth, EV, multiple).
 * Source: GET /api/analytics/comparables (executive-only).
 */
export function useComparables() {
  return useAnalyticsFetch<{ items: ComparableRow[] }>('/api/analytics/comparables');
}

/**
 * Pipeline prediction (counts + success rate per phase).
 * Source: GET /api/analytics/pipeline (executive-only).
 */
export function usePipelinePredict() {
  return useAnalyticsFetch<{ items: PipelinePredictRow[] }>('/api/analytics/pipeline');
}

/**
 * Royalty schedule (rate tier by revenue volume).
 * Source: GET /api/analytics/royalty-schedule (executive-only).
 */
export function useRoyaltySchedule() {
  return useAnalyticsFetch<{ items: RoyaltyRow[] }>('/api/analytics/royalty-schedule');
}

/**
 * API usage by hour-of-day (UTC).
 * Source: GET /api/analytics/api-usage-time (admin-only).
 */
export function useApiUsageTime() {
  return useAnalyticsFetch<{ items: ApiUsageTimePoint[] }>('/api/analytics/api-usage-time');
}

/**
 * Competitive moat score (data volume / accuracy / network effects / etc.).
 * Source: GET /api/analytics/moat (executive-only).
 */
export function useMoatScore() {
  return useAnalyticsFetch<{ items: MoatPoint[] }>('/api/analytics/moat');
}
