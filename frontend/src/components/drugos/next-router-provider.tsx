'use client';

/**
 * FE-001 ROOT FIX (v129, hostile-auditor pass): Next.js App Router bridge.
 *
 * This provider is the SINGLE source of truth for the in-app `RouterContext`.
 * It uses Next.js's real `useRouter` (from `next/navigation`, NOT the fake
 * custom one) to push URL paths to the browser history. The existing
 * `RouterContext` is preserved so 100+ `navigate({...})` call sites in
 * app-router.tsx / core-screens.tsx / remaining-screens.tsx continue to
 * work — but each `navigate(r)` now produces a REAL URL path
 * (e.g. `/dashboard`, `/drugs/aspirin`) instead of a query string.
 *
 * Why this is the root fix:
 *   - The URL bar shows real paths (`/drugs/aspirin`), not query strings.
 *   - Refresh preserves the URL (Next.js App Router handles the path).
 *   - Browser back/forward buttons work via Next.js's router.
 *   - Server-side rendering works (Next.js can render the route on the server).
 *   - Middleware RBAC per-route is now possible (the URL changes per route).
 *
 * The legacy `useUrlRoute()` hook in app-router.tsx (which used
 * `window.history.pushState` directly) is REPLACED by this provider.
 * The legacy hook is kept only as a fallback for tests that mount
 * `<DrugOSApp />` in isolation; production renders go through this provider.
 */

import React, { createContext, useContext, useCallback, useMemo, type ReactNode } from 'react';
import { useRouter as useNextRouter, usePathname, useSearchParams } from 'next/navigation';
import {
  type Route,
  routeToPath,
  parsePathToRoute,
  routeToUrl,
  parseUrlToRoute,
} from './url-route';

interface RouterContextType {
  route: Route;
  navigate: (r: Route) => void;
}

const RouterContext = createContext<RouterContextType>({
  route: { page: 'landing' },
  navigate: () => {},
});

export function useRouter(): RouterContextType {
  return useContext(RouterContext);
}

export { RouterContext };

/**
 * Bridge the Next.js App Router to the in-app RouterContext.
 *
 * Mount this ONCE at the root of the app (in app/layout.tsx). It reads the
 * current URL via `usePathname` + `useSearchParams`, converts it to a Route,
 * and exposes a `navigate(r)` function that calls `router.push(routeToPath(r))`.
 *
 * Legacy query-string URLs (`/?p=app&s=dashboard`) are still recognized so
 * existing bookmarks don't break — they're parsed via `parseUrlToRoute`.
 * New navigations always produce path-based URLs.
 */
export function NextRouterProvider({ children }: { children: ReactNode }) {
  const nextRouter = useNextRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const route: Route = useMemo(() => {
    // Prefer the path-based codec. Fall back to query-string codec for
    // legacy URLs (e.g. `/?p=app&s=dashboard` from old bookmarks).
    const path = pathname ?? '/';
    const parsedFromPath = parsePathToRoute(path);

    // If the path is just '/' AND there's a `?p=...` query string, treat
    // it as a legacy URL and parse via parseUrlToRoute.
    if (path === '/' && searchParams?.get('p')) {
      const href = `${path}?${searchParams.toString()}`;
      return parseUrlToRoute(href);
    }

    return parsedFromPath;
  }, [pathname, searchParams]);

  const navigate = useCallback(
    (r: Route) => {
      const url = routeToPath(r);
      // Next.js router.push with scroll:false so the page doesn't jump to
      // top on every navigation (the AppShell persists across navigations).
      nextRouter.push(url, { scroll: false });
    },
    [nextRouter]
  );

  const value = useMemo<RouterContextType>(
    () => ({ route, navigate }),
    [route, navigate]
  );

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

/**
 * Legacy URL format check — returns true if the current URL uses the old
 * `?p=...` query-string format. Used by app/page.tsx to redirect legacy
 * URLs to the canonical path-based URL.
 */
export function isLegacyUrl(pathname: string, search: string | null): boolean {
  if (pathname !== '/' && pathname !== '') return false;
  return !!search && search.includes('p=');
}

/**
 * Convert a legacy query-string URL to the canonical path-based URL.
 * Returns null if the URL is not a legacy URL.
 *
 * Used by app/page.tsx to redirect `/?p=app&s=dashboard` → `/dashboard`
 * on first load, so bookmarks from before the v129 migration keep working.
 */
export function legacyToCanonicalUrl(pathname: string, search: string | null): string | null {
  if (!isLegacyUrl(pathname, search)) return null;
  const href = `${pathname}${search ? `?${search}` : ''}`;
  const route = parseUrlToRoute(href);
  // If the legacy URL was just landing, no redirect needed.
  if (route.page === 'landing') return null;
  return routeToPath(route);
}
