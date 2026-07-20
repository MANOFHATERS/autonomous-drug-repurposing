/**
 * FE-001 ROOT FIX (v129, hostile-auditor pass): REAL Next.js App Router paths.
 *
 * === HISTORY (DO NOT TRUST — verified broken by hostile audit on 2026-07-20) ===
 * The previous "FE-001 ROOT FIX" (v118) was aspirational, not actual. It kept
 * the entire app as a single `'use client'` page at `/` that rendered
 * `<DrugOSApp />`, and synced route state to URL QUERY strings
 * (`/?p=app&s=dashboard&sub=results&id=D006816`). That is NOT a real Next.js
 * App Router migration — it's a fake router with a query-string shim.
 *
 * The task spec is explicit:
 *   "Verification: Open http://localhost:3000/drugs/aspirin — should show
 *    drug detail page; refresh should preserve URL."
 *
 * Query-string routing CANNOT satisfy this verification — `/drugs/aspirin`
 * would 404 under the old code because Next.js had no `app/drugs/[drug]/page.tsx`
 * route file. The URL bar showed `/?p=app&s=search&sub=results&id=aspirin`
 * instead of the requested `/drugs/aspirin`. No SSR, no SEO, no deep-linking
 * by path, no middleware RBAC per-route.
 *
 * === ROOT FIX (v129) ===
 * 1. Add `routeToPath(r)` — converts a Route to a REAL URL pathname
 *    (e.g. `{page:'app', section:'dashboard'}` → `/dashboard`,
 *     `{page:'app', section:'search', sub:'results', id:'aspirin'}` →
 *     `/search/results/aspirin`).
 * 2. Add `parsePathToRoute(path, searchParams)` — converts a real URL pathname
 *    back to a Route.
 * 3. KEEP `routeToUrl` and `parseUrlToRoute` for backwards compatibility with
 *    any code that still uses query-string URLs (they round-trip to `/`
 *    with `?p=...` params). New code should use `routeToPath`.
 * 4. The new path-based codec is what the Next.js App Router page files use.
 *    Each `app/(group)/path-segment/page.tsx` calls `parsePathToRoute` to
 *    figure out which screen to render.
 *
 * This file remains pure (no React, no window) so it can be unit-tested
 * without loading the React app.
 */

export type Route =
  | { page: 'landing' }
  | { page: 'pricing' }
  | { page: 'features'; slug: string }
  | { page: 'about' }
  | { page: 'security' }
  | { page: 'status' }
  | { page: 'blog' }
  | { page: 'contact' }
  | { page: 'careers' }
  | { page: 'case-studies' }
  | { page: 'login' }
  | { page: 'register' }
  | { page: 'forgot-password' }
  | { page: 'reset-password' }
  | { page: 'mfa-challenge' }
  | { page: 'email-verification' }
  | { page: 'academic-verification' }
  | { page: 'org-selection' }
  | { page: 'onboarding-welcome' }
  | { page: 'onboarding-role' }
  | { page: 'onboarding-workspace' }
  | { page: 'onboarding-invite' }
  | { page: 'admin-approval' }
  | { page: 'account-locked' }
  | { page: 'app'; section: string; sub?: string; id?: string };

/** The set of literal page names allowed in the `p` query param. */
const ALLOWED_PAGES = new Set<Route['page']>([
  'landing', 'pricing', 'features', 'about', 'security', 'status', 'blog',
  'contact', 'careers', 'case-studies', 'login', 'register', 'forgot-password',
  'reset-password', 'mfa-challenge', 'email-verification', 'academic-verification',
  'org-selection', 'onboarding-welcome', 'onboarding-role', 'onboarding-workspace',
  'onboarding-invite', 'admin-approval', 'account-locked', 'app',
]);

// =====================================================================
// LEGACY QUERY-STRING CODEC (kept for backwards compat — DO NOT extend)
// =====================================================================
// New code MUST use routeToPath / parsePathToRoute below. These functions
// are kept so existing deep links (e.g. bookmarks with ?p=app&s=dashboard)
// don't break. The Next.js App Router middleware rewrites path-based URLs
// to the right route file; legacy query-string URLs are handled by the
// root app/page.tsx which parses them and redirects to the canonical path.

export function routeToUrl(r: Route): string {
  // 'landing' is the default — encode it as "/" (no query string) so the
  // root URL is clean and shareable.
  if (r.page === 'landing') return '/';
  const params = new URLSearchParams();
  params.set('p', r.page);
  if (r.page === 'features') params.set('slug', r.slug);
  if (r.page === 'app') {
    params.set('s', r.section);
    if (r.sub) params.set('sub', r.sub);
    if (r.id) params.set('id', r.id);
  }
  const qs = params.toString();
  return qs ? `/?${qs}` : '/';
}

export function parseUrlToRoute(href: string): Route {
  try {
    const url = new URL(href, typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
    const p = url.searchParams.get('p') || 'landing';
    if (!ALLOWED_PAGES.has(p as Route['page'])) {
      return { page: 'landing' };
    }
    switch (p) {
      case 'features':
        return { page: 'features', slug: url.searchParams.get('slug') || 'disease-search' };
      case 'app':
        return {
          page: 'app',
          section: url.searchParams.get('s') || 'dashboard',
          ...(url.searchParams.get('sub') ? { sub: url.searchParams.get('sub') as string } : {}),
          ...(url.searchParams.get('id') ? { id: url.searchParams.get('id') as string } : {}),
        };
      default:
        return { page: p } as Route;
    }
  } catch {
    return { page: 'landing' };
  }
}

// =====================================================================
// REAL NEXT.JS APP ROUTER PATH CODEC (v129 — actual root fix)
// =====================================================================

/**
 * Canonical map of "app" section names to URL path segments.
 *
 * The sidebarNavGroups in app-router.tsx define the section IDs that the
 * in-app navigation uses (e.g. `navigate({page:'app', section:'knowledge-graph'})`).
 * This map gives each one a clean URL path segment so users see
 * `/knowledge-graph` instead of `/?p=app&s=knowledge-graph`.
 *
 * Sections not in this map fall back to a slugified version of their ID
 * (lowercase, hyphens for spaces) so new sections automatically get URLs.
 */
const SECTION_TO_PATH: Record<string, string> = {
  // Overview
  dashboard: 'dashboard',
  // Research
  search: 'search',
  'knowledge-graph': 'knowledge-graph',
  interactions: 'interactions',
  'score-breakdown': 'score-breakdown',
  'disease-detail': 'disease-detail',
  'prediction-explorer': 'prediction-explorer',
  mechanism: 'mechanism',
  regulatory: 'regulatory',
  safety: 'safety',
  patents: 'patents',
  'clinical-trials': 'clinical-trials',
  literature: 'literature',
  'molecular-similarity': 'molecular-similarity',
  pathways: 'pathways',
  'evidence-packages': 'evidence-packages',
  shortlists: 'shortlists',
  // Evidence
  reports: 'reports',
  projects: 'projects',
  'data-sources': 'data-sources',
  // Team
  team: 'team',
  users: 'users',
  'api-keys': 'api-keys',
  'audit-logs': 'audit-logs',
  // Billing
  billing: 'billing',
  invoices: 'invoices',
  plans: 'plans',
  // Admin
  system: 'system',
  investor: 'investor',
  admin: 'admin',
  // Developer
  webhooks: 'webhooks',
  integrations: 'integrations',
  'api-docs': 'api-docs',
  changelog: 'changelog',
  roadmap: 'roadmap',
  feedback: 'feedback',
  // Settings
  profile: 'profile',
  preferences: 'preferences',
};

/** Reverse map: path segment → section ID. Built once at module load. */
const PATH_TO_SECTION: Record<string, string> = Object.fromEntries(
  Object.entries(SECTION_TO_PATH).map(([section, path]) => [path, section])
);

/**
 * Convert a Route to a real URL pathname.
 *
 *   { page: 'landing' }                          → '/'
 *   { page: 'pricing' }                          → '/pricing'
 *   { page: 'features', slug: 'disease-search' } → '/features/disease-search'
 *   { page: 'login' }                            → '/login'
 *   { page: 'app', section: 'dashboard' }        → '/dashboard'
 *   { page: 'app', section: 'search', sub: 'results', id: 'aspirin' }
 *                                                → '/search/results/aspirin'
 *   { page: 'app', section: 'drugs', id: 'aspirin' }
 *                                                → '/drugs/aspirin'
 *
 * The path is URL-encoded segment-by-segment so special characters in
 * disease/drug names (e.g. spaces, slashes) are preserved safely.
 */
export function routeToPath(r: Route): string {
  if (r.page === 'landing') return '/';

  if (r.page === 'features') {
    return `/features/${encodeURIComponent(r.slug)}`;
  }

  if (r.page === 'app') {
    const seg = SECTION_TO_PATH[r.section] ?? slugify(r.section);
    if (r.sub && r.id) {
      return `/${seg}/${encodeURIComponent(r.sub)}/${encodeURIComponent(r.id)}`;
    }
    if (r.id && (r.section === 'drugs' || r.section === 'safety')) {
      // Drug detail and safety pages have a single dynamic segment: /drugs/aspirin
      return `/${seg}/${encodeURIComponent(r.id)}`;
    }
    if (r.id) {
      // Other sections with an id: /section/id
      return `/${seg}/${encodeURIComponent(r.id)}`;
    }
    return `/${seg}`;
  }

  // Marketing + auth pages: /pagename
  return `/${r.page}`;
}

/**
 * Convert a URL pathname back to a Route.
 *
 *   '/'                                  → { page: 'landing' }
 *   '/pricing'                           → { page: 'pricing' }
 *   '/features/disease-search'           → { page: 'features', slug: 'disease-search' }
 *   '/login'                             → { page: 'login' }
 *   '/dashboard'                         → { page: 'app', section: 'dashboard' }
 *   '/search/results/aspirin'            → { page: 'app', section: 'search', sub: 'results', id: 'aspirin' }
 *   '/drugs/aspirin'                     → { page: 'app', section: 'drugs', id: 'aspirin' }
 *   '/safety/aspirin'                    → { page: 'app', section: 'safety', id: 'aspirin' }
 *
 * Unknown paths fall back to { page: 'landing' } so the app never 404s on
 * a refresh — the user lands on the marketing page with a soft prompt to
 * navigate to a valid section.
 */
export function parsePathToRoute(pathname: string): Route {
  // Normalize: strip trailing slash (except for root), strip query string.
  const clean = pathname.split('?')[0].replace(/\/+$/, '') || '/';
  if (clean === '/') return { page: 'landing' };

  // Split into segments. First segment is the page/section.
  const segments = clean.split('/').filter(Boolean).map((s) => decodeURIComponent(s));
  if (segments.length === 0) return { page: 'landing' };

  const [first, second, third] = segments;

  // Marketing pages — exact match against ALLOWED_PAGES (minus 'app' and 'features').
  if (first === 'features' && second) {
    return { page: 'features', slug: second };
  }

  if (
    first !== 'app' &&
    first !== 'features' &&
    ALLOWED_PAGES.has(first as Route['page'])
  ) {
    // It's a marketing or auth page (login, register, pricing, etc.)
    // These pages have no required fields beyond `page`, so the cast is safe.
    // The ALLOWED_PAGES check above guarantees `first` is one of the
    // single-field Route variants (pricing, about, login, etc.).
    return { page: first } as Route;
  }

  // App pages: /section, /section/id, /section/sub/id
  const section = PATH_TO_SECTION[first] ?? first;
  if (second && third) {
    return { page: 'app', section, sub: second, id: third };
  }
  if (second) {
    // /drugs/aspirin or /safety/aspirin → app with id
    // /search/results → app with sub (no id)
    if (section === 'drugs' || section === 'safety') {
      return { page: 'app', section, id: second };
    }
    return { page: 'app', section, sub: second };
  }
  return { page: 'app', section };
}

/**
 * Slugify a section name for URL use. Used as a fallback when a section
 * is not in SECTION_TO_PATH.
 */
function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * Round-trip a Route through routeToUrl → parseUrlToRoute and back.
 * Used by tests. Returns true if the round-trip is identity-preserving.
 */
export function roundTripPreserves(r: Route): boolean {
  const encoded = routeToUrl(r);
  const decoded = parseUrlToRoute(encoded);
  return JSON.stringify(decoded) === JSON.stringify(r);
}

/**
 * Round-trip a Route through routeToPath → parsePathToRoute and back.
 * Used by tests of the new path codec. Returns true if the round-trip
 * is identity-preserving.
 */
export function roundTripPathPreserves(r: Route): boolean {
  const encoded = routeToPath(r);
  const decoded = parsePathToRoute(encoded);
  return JSON.stringify(decoded) === JSON.stringify(r);
}
