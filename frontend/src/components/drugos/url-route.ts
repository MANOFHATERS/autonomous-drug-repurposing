/**
 * FE-001 ROOT FIX: URL-based routing codec.
 *
 * Pure functions that convert between the in-memory `Route` discriminated
 * union and URL query strings. Extracted from app-router.tsx so they can be
 * unit-tested without loading the entire React app.
 *
 * The app is a single Next.js route at "/", so we sync route state to the
 * URL query string (?p=app&s=dashboard&sub=results&id=D006816). This
 * restores deep-linking, browser back/forward, refresh, and shareable URLs.
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

/**
 * Round-trip a Route through routeToUrl → parseUrlToRoute and back.
 * Used by tests. Returns true if the round-trip is identity-preserving.
 */
export function roundTripPreserves(r: Route): boolean {
  const encoded = routeToUrl(r);
  const decoded = parseUrlToRoute(encoded);
  return JSON.stringify(decoded) === JSON.stringify(r);
}
