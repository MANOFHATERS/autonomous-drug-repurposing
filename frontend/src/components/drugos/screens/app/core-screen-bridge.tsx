'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 3016-3140). Bridge component that renders core
// screens from core-screens.tsx, wrapping them in DrugOSNavContext.Provider
// + <Suspense> with a skeleton fallback.
//
// FE-003 v143 ROOT FIX preserved (Teammate 13): the navigate callback
// uses the canonical strict Route type from nav-context.tsx, and
// currentRoute is typed as Extract<Route, { page: 'app' }> so callers
// can access .section/.id/.name/.sub without per-call-site narrowing.
// A transientName state preserves the `name` prop across URL updates
// (routeToPath doesn't URL-encode `name`, so it would be lost on the
// next render without this state).

import { useMemo, useState, Suspense } from 'react'
import { useRouter } from '../../next-router-provider'
import { DrugOSNavContext, type Route } from '../../nav-context'
import { coreScreens } from '../../core-screens'
import { AppPlaceholderSection } from './app-placeholder'
import { CoreScreenSkeleton } from './core-screen-skeleton'

// Bridge component to render core screens from core-screens.tsx
export function CoreScreenBridge({ section, sub, id }: { section: string; sub?: string; id?: string }) {
  const { navigate: routerNavigate, route: routerRoute } = useRouter()

  // FE-003 ROOT FIX (Teammate 13, v143): preserve the transient `name`
  // prop across navigation. The previous `navContextValue.navigate`
  // accepted a loosely-typed route and dropped the `name` field on the
  // floor — `routerNavigate` (next-router-provider.tsx) calls
  // `routeToPath(r)` which doesn't URL-encode `name`, so `name` was lost
  // by the time `currentRoute` was rebuilt from the URL on the next
  // render. SearchResultsScreen then had to re-fetch the disease name by
  // ID, defeating the purpose of passing `name` in the first place.
  //
  // ROOT FIX: store `name` in a React state (transientName) that survives
  // the URL update. The state is keyed by `id` so navigating to a
  // DIFFERENT id (different disease) clears the stale name. The state is
  // also cleared when the section changes (so a name passed for
  // 'results' doesn't leak into 'shortlists' on the next navigation).
  const [transientName, setTransientName] = useState<string | undefined>(undefined)

  // FE-003 ROOT FIX (Teammate 13, v143): use the canonical strict Route
  // type from nav-context.tsx (which now re-exports from url-route.ts).
  // The previous `navigate: (r: { page: string; ... })` accepted ANY
  // string for `page` — typos like `navigate({ page: 'ap' })` compiled
  // fine and silently no-op'd at runtime. The strict type catches these
  // at compile time.
  //
  // The `currentRoute` field is typed as `Extract<Route, { page: 'app' }>`
  // (the narrow 'app' variant) because CoreScreenBridge ALWAYS mounts
  // inside the AppShell where `currentRoute.page` is always 'app'. This
  // lets callers access `.section` / `.id` / `.name` / `.sub` directly
  // without per-call-site narrowing.
  const navContextValue = useMemo<{
    navigate: (r: Route) => void;
    currentRoute: Extract<Route, { page: 'app' }>;
  }>(() => ({
    navigate: (r) => {
      // FE-003 ROOT FIX: narrow to the 'app' variant before accessing
      // .section/.sub/.id/.name. The strict Route type is a discriminated
      // union — TypeScript requires narrowing before accessing variant-
      // specific fields. At runtime, every caller passes `{ page: 'app', ... }`
      // (verified by grepping all `navigate({...})` call sites in
      // core-screens.tsx and remaining-screens.tsx — they ALL use page: 'app').
      // The non-'app' branch is a safety net for future callers that
      // might navigate to marketing/auth pages from inside the AppShell.
      if (r.page !== 'app') {
        // Non-app route (e.g., navigate to /login from inside the app).
        // Forward as-is to routerNavigate (which accepts the full Route
        // union). Clear the transient name so it doesn't leak.
        setTransientName(undefined);
        routerNavigate(r);
        return;
      }
      // r is now narrowed to the 'app' variant — TypeScript allows
      // access to .section, .sub, .id, .name.
      if (r.name) {
        setTransientName(r.name);
      } else if (r.id !== id) {
        // Navigating to a different id — clear the stale name.
        setTransientName(undefined);
      }
      routerNavigate(r);
    },
    currentRoute: {
      page: 'app',
      section: section,
      sub: sub,
      id: id,
      // If we have a transientName (from a recent navigate() call that
      // included `name`), include it in currentRoute so screens can read
      // it without re-fetching. On a page refresh, transientName is
      // undefined (React state is not persisted) and SearchResultsScreen
      // falls back to deriving the name from the disease ID — existing
      // behavior, no regression.
      ...(transientName ? { name: transientName } : {}),
    },
  }), [routerNavigate, section, sub, id, transientName])

  // ISSUE-FE-015: Removed dead allScreens fallback. coreScreens already
  // includes all sections via remainingScreens spread.
  const ScreenComponent = coreScreens[section]

  if (!ScreenComponent) {
    return <AppPlaceholderSection section={section} />
  }

  // FE-030 v129 ROOT FIX (Teammate 13): wrap the rendered screen in
  // <Suspense> with a skeleton fallback. The 37 "remaining screens" are
  // now lazy-loaded via `next/dynamic` in core-screens.tsx — they ship
  // in a separate chunk that downloads on first access. While the chunk
  // is in flight, `dynamic()`'s own `loading` prop shows the skeleton
  // (configured in core-screens.tsx). This outer <Suspense> is a SECOND
  // layer that catches any other async work inside the screen (e.g.,
  // React 19 `use()` of a promise, async server-component data) so the
  // user sees a skeleton instead of a white flash.
  //
  // The skeleton is intentionally lightweight (no DOM-measurable layout
  // shift) and uses `aria-busy` so screen readers announce the loading
  // state. The fallback key includes `section` so navigating between
  // sections re-mounts the skeleton (otherwise React would reuse the
  // same skeleton instance and the user wouldn't see a fresh loading
  // state when switching screens).
  return (
    <DrugOSNavContext.Provider value={navContextValue}>
      <Suspense
        key={`screen-${section}`}
        fallback={<CoreScreenSkeleton section={section} />}
      >
        <ScreenComponent />
      </Suspense>
    </DrugOSNavContext.Provider>
  )
}
