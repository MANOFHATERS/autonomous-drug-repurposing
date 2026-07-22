'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 3067-3086). Skeleton shown by <Suspense> while a
// lazy-loaded screen chunk downloads OR while a screen's internal async
// work (React 19 `use()`, server-component data) is pending. Kept here
// (not in core-screens.tsx) because CoreScreenBridge is the only consumer.
// Preserved VERBATIM — only the import block at the top is new.


export function CoreScreenSkeleton({ section }: { section: string }) {
  return (
    <div
      className="space-y-4 p-6"
      aria-busy="true"
      aria-live="polite"
      role="status"
    >
      <div className="h-8 w-48 bg-muted animate-pulse rounded" />
      <div className="h-4 w-72 bg-muted/60 animate-pulse rounded" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6">
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
        <div className="h-32 bg-muted/40 animate-pulse rounded-lg" />
      </div>
      <div className="h-64 bg-muted/30 animate-pulse rounded-lg" />
      <span className="sr-only">Loading {section}…</span>
    </div>
  )
}
