'use client';

import { createContext, useContext } from 'react';
// FE-003 ROOT FIX (Teammate 13, v143, CRITICAL — type-safety hole):
// The previous version of this file declared its OWN loose `Route` type:
//
//   export type Route = { page: string; section?: string; sub?: string; id?: string; name?: string };
//
// This LOOSE type (page: string — accepts any value) was INCOMPATIBLE
// with the STRICT discriminated-union `Route` in url-route.ts:
//
//   export type Route = | { page: 'landing' } | { page: 'pricing' } | ...
//                       | { page: 'app'; section: string; sub?: string; id?: string };
//
// Consequences:
//   1. core-screens.tsx:268 calls
//        `navigate({ page: 'app', section: 'results', id: diseaseId, name: diseaseName })`
//      — the `name` field was REJECTED by the strict type (compile error)
//        but ACCEPTED by the loose type (silent drop). Depending on which
//        `navigate` was imported, the call either failed the build or
//        silently lost the disease name.
//   2. The loose `page: string` accepted ANY string — typos like
//        `navigate({ page: 'ap' })` (missing 'p') compiled fine and
//        silently no-op'd at runtime.
//
// ROOT FIX: delete the loose Route type and re-export the canonical
// one from url-route.ts. The canonical type now has `name?: string`
// on the 'app' variant (FE-003 fix in url-route.ts), so the call in
// core-screens.tsx:268 is type-safe AND preserves the disease name.
//
// The 'app' section in the routeToPath codec ignores the `name` field
// (it's a transient prop, not URL-encoded). On a page refresh, `name`
// is undefined and SearchResultsScreen falls back to deriving the
// disease name from the URL's disease ID (existing behavior).
import { type Route } from './url-route';

// Re-export Route so existing imports from './nav-context' continue to work.
// Existing code that does `import { type Route } from './nav-context'`
// will now get the canonical strict type from url-route.ts — a breaking
// change for callers that relied on the loose type's `page: string`,
// but that breaking change is the EXACT type-safety upgrade the audit
// demanded. Typos in `page` values now fail at compile time.
export type { Route };

/**
 * FE-003 ROOT FIX (Teammate 13, v143): the in-app navigation context
 * ALWAYS holds an 'app'-page route. The CoreScreenBridge provider (in
 * app-router.tsx) is the ONLY component that mounts DrugOSNavContext,
 * and it constructs `currentRoute` as `{ page: 'app', section, ... }`
 * unconditionally. So at runtime, `currentRoute.page` is always 'app'.
 *
 * Typing `currentRoute` as the full `Route` union forced callers to
 * narrow with `if (currentRoute.page === 'app')` before accessing
 * `.section` / `.id` / `.name` — but every caller (SearchResultsScreen,
 * CandidateDetailScreen, etc.) is mounted INSIDE the AppShell and
 * already knows `currentRoute` is the 'app' variant. The narrowing was
 * pure boilerplate.
 *
 * The fix: type `currentRoute` as `Extract<Route, { page: 'app' }>` —
 * the narrowest type that matches the runtime invariant. Callers can
 * access `.section`, `.id`, `.name`, `.sub` directly without narrowing.
 *
 * `navigate` still accepts the full `Route` union so callers can pass
 * any Route variant (in practice they only pass 'app' variants, but
 * the type allows the future flexibility of navigating to marketing
 * pages from inside the AppShell if needed).
 */
type AppRoute = Extract<Route, { page: 'app' }>;

type NavContext = {
  navigate: (route: Route) => void;
  currentRoute: AppRoute;
};

export const DrugOSNavContext = createContext<NavContext>({
  navigate: () => {},
  currentRoute: { page: 'app', section: 'search' },
});

export function useDrugOSNav() {
  return useContext(DrugOSNavContext);
}
