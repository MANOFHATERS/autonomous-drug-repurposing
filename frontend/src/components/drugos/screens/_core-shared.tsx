'use client';

// FE-023-A ROOT FIX (Teammate 17 Subagent A): shared helpers extracted from
// the 3646-line `core-screens.tsx`. Each per-screen file under `screens/`
// imports these helpers from this module so the screens themselves stay
// small and self-contained.
//
// Contents (named exports):
//   - Color constants: PRIMARY, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED, BG
//   - scoreColor(s: number) — maps a 0-100 score to a hex color
//   - EmptyDataState({ title, hint }) — honest empty-state with Database icon
//   - StatCard({ icon, value, label, color }) — icon is React.ElementType
//   - PageHeader({ title, description, actions, onBack }) — uses useDrugOSNav
//   - FadeIn({ children, delay }) — framer-motion fade-in wrapper
import React from 'react';
import { motion } from 'framer-motion';
import { ArrowLeft, Database } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useDrugOSNav } from '../nav-context';

// ═══════════════════════════════════════════
// SHARED COLOR CONSTANTS
// ═══════════════════════════════════════════

export const PRIMARY = '#5B4FCF';
export const ACCENT_GREEN = '#1D9E75';
export const ACCENT_ORANGE = '#D4853A';
export const ACCENT_RED = '#C0392B';
export const BG = '#F8F8FA';

// ═══════════════════════════════════════════
// SHARED HELPERS
// ═══════════════════════════════════════════

export function scoreColor(s: number) {
  if (s >= 80) return ACCENT_GREEN;
  if (s >= 60) return ACCENT_ORANGE;
  return ACCENT_RED;
}

/**
 * Shared empty-state for screens that have no data yet. Per the project
 * doc (Team_Cosmic_Build_Process_Updated.docx) and the FE-034 root fix,
 * production code must NEVER fabricate sample data — it must show an
 * honest empty state that tells the researcher the data is not loaded
 * (and, where relevant, how to load it). This replaces the previous
 * pattern of `.map()` over an empty array rendering nothing (leaving
 * the researcher staring at a blank table with no explanation).
 */
export function EmptyDataState({ title, hint }: { title: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Database className="h-10 w-10 mx-auto text-muted-foreground/50 mb-3" aria-hidden />
        <p className="font-medium text-foreground">{title}</p>
        {hint && <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">{hint}</p>}
      </CardContent>
    </Card>
  );
}

export function StatCard({ icon: Icon, value, label, color = PRIMARY }: { icon: React.ElementType; value: string | number; label: string; color?: string }) {
  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-2xl font-bold mt-1">{value}</p>
          </div>
          <div className="rounded-lg p-2.5" style={{ backgroundColor: `${color}15` }}>
            <Icon className="h-5 w-5" style={{ color }} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function PageHeader({ title, description, actions, onBack }: { title: string; description?: string; actions?: React.ReactNode; onBack?: () => void }) {
  const { navigate } = useDrugOSNav();
  return (
    <div className="flex items-start justify-between mb-6">
      <div className="flex items-start gap-3">
        {onBack && (
          <Button variant="ghost" size="sm" onClick={onBack} className="mt-0.5 h-8 w-8 p-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        )}
        <div>
          <h1 className="text-2xl font-bold text-foreground">{title}</h1>
          {description && <p className="text-sm text-muted-foreground mt-0.5">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay }}>
      {children}
    </motion.div>
  );
}
