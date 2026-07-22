'use client';

// FE-030 v129 ROOT FIX (Teammate 13): skeleton fallback shown while the
// lazy-loaded remaining-screens chunk downloads. Extracted from
// core-screens.tsx (lines 3401-3414) as part of FE-023-A.
export function ScreenSkeleton() {
  return (
    <div className="space-y-4 p-6" aria-busy="true" aria-live="polite">
      <div className="h-8 w-48 bg-muted animate-pulse rounded" />
      <div className="h-4 w-72 bg-muted/60 animate-pulse rounded" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6">
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
      </div>
      <div className="h-64 bg-muted/30 animate-pulse rounded-lg" />
    </div>
  );
}
