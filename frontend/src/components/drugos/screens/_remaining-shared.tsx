'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { Card, CardContent } from '@/components/ui/card';

const PRIMARY = '#5B4FCF';
const GREEN = '#1D9E75';
const ORANGE = '#D4853A';
const RED = '#C0392B';

const CHART_COLORS = ['#5B4FCF', '#1D9E75', '#D4853A', '#C0392B', '#8B5CF6', '#06B6D4', '#EC4899', '#F59E0B'];

// FE-058 ROOT FIX (Teammate 13, LOW): safe localStorage helpers.
// The 4 cited call sites (NotificationsScreen load/save + PreferencesScreen
// load/save) already wrapped localStorage in try/catch — so they did NOT
// throw in Safari private-browsing mode. HOWEVER the pattern was repeated
// 4x with subtle drift (one site parsed JSON inside the try, another
// outside; error messages differed). Root fix: consolidate into two
// helpers so the defensive pattern is identical everywhere and cannot
// drift. `safeLocalStorageGet` returns `null` on ANY failure (quota
// exceeded, SecurityError in private mode, malformed JSON, SSR where
// localStorage is undefined). `safeLocalStorageSet` returns `true`/`false`.
// Both are no-ops during SSR (window is undefined).
function safeLocalStorageGet(key: string): string | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalStorageSet(key: string, value: string): boolean {
  try {
    if (typeof window === 'undefined') return false;
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeLocalStorageGetJSON<T>(key: string, fallback: T): T {
  const raw = safeLocalStorageGet(key);
  if (raw == null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

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

export {
  PRIMARY,
  GREEN,
  ORANGE,
  RED,
  CHART_COLORS,
  safeLocalStorageGet,
  safeLocalStorageSet,
  safeLocalStorageGetJSON,
  FadeIn,
  PageHeader,
  StatCard,
};
