/**
 * v129 TM12 Task 12.1 — Real Next.js App Router Verification (Red-Team)
 *
 * Hostile-auditor tests for the v129 root fix. The previous "FE-001 ROOT FIX"
 * (v118) was aspirational — it kept the app as a single `'use client'` page
 * at `/` that faked routing via URL query strings (`/?p=app&s=dashboard`).
 * That is NOT a real Next.js App Router migration.
 *
 * Task 12.1 verification spec:
 *   "Open http://localhost:3000/drugs/aspirin — should show drug detail page;
 *    refresh should preserve URL."
 *
 * These tests verify the REAL fix:
 *   1. `app/drugs/[drug]/page.tsx` route file EXISTS (the verification target).
 *   2. `url-route.ts` exports `routeToPath` and `parsePathToRoute` (real
 *      path-based codec, not query strings).
 *   3. `routeToPath({page:'app', section:'dashboard'})` returns `/dashboard`
 *      (NOT `/?p=app&s=dashboard`).
 *   4. `routeToPath({page:'app', section:'drugs', id:'aspirin'})` returns
 *      `/drugs/aspirin` (the verification target URL).
 *   5. `parsePathToRoute('/drugs/aspirin')` round-trips back to the same Route.
 *   6. `app/layout.tsx` mounts the `NextRouterProvider` (bridges legacy
 *      RouterContext to next/navigation's real useRouter).
 *   7. `app/page.tsx` is NOT marked `'use client'` (it's a server component
 *      for SSR/SEO — the landing page must be server-rendered).
 *   8. `loading.tsx` and `error.tsx` exist at the root for route-level
 *      loading and error states.
 *   9. Each major app section has a real route file (dashboard, search,
 *      knowledge-graph, drugs/[drug], etc.).
 *
 * Red-Team mode: assume every comment is a lie. We check the ACTUAL filesystem
 * and ACTUAL code — not comments.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..', '..', '..', '..');

function fileExists(rel: string): boolean {
  return fs.existsSync(path.resolve(ROOT, rel));
}

function read(rel: string): string {
  return fs.readFileSync(path.resolve(ROOT, rel), 'utf8');
}

describe('v129 TM12 Task 12.1 — Real Next.js App Router (Red-Team)', () => {
  describe('routeToPath: real URL paths, not query strings', () => {
    const urlRoute = read('src/components/drugos/url-route.ts');

    it('exports routeToPath function', () => {
      expect(urlRoute).toContain('export function routeToPath');
    });

    it('exports parsePathToRoute function', () => {
      expect(urlRoute).toContain('export function parsePathToRoute');
    });

    it('routeToPath returns / for landing (NOT query string)', () => {
      // Use dynamic require to get the actual module — this tests the
      // runtime behavior, not just the source code presence.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'landing' })).toBe('/');
    });

    it('routeToPath returns /dashboard for app dashboard (NOT ?p=app&s=dashboard)', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'app', section: 'dashboard' })).toBe('/dashboard');
    });

    it('routeToPath returns /drugs/aspirin for drug detail (VERIFICATION TARGET)', () => {
      // Task 12.1 spec: "Open http://localhost:3000/drugs/aspirin — should
      // show drug detail page; refresh should preserve URL."
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'app', section: 'drugs', id: 'aspirin' })).toBe('/drugs/aspirin');
    });

    it('routeToPath returns /search/results/aspirin for search results', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'app', section: 'search', sub: 'results', id: 'aspirin' })).toBe(
        '/search/results/aspirin'
      );
    });

    it('routeToPath returns /pricing for marketing pages', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'pricing' })).toBe('/pricing');
    });

    it('routeToPath returns /features/disease-search for feature pages', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { routeToPath } = require('../url-route');
      expect(routeToPath({ page: 'features', slug: 'disease-search' })).toBe(
        '/features/disease-search'
      );
    });
  });

  describe('parsePathToRoute: real URL paths → Route (round-trip)', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { parsePathToRoute, routeToPath } = require('../url-route');

    it('parses / as landing', () => {
      expect(parsePathToRoute('/')).toEqual({ page: 'landing' });
    });

    it('parses /dashboard as app dashboard', () => {
      expect(parsePathToRoute('/dashboard')).toEqual({ page: 'app', section: 'dashboard' });
    });

    it('parses /drugs/aspirin as app drugs with id (VERIFICATION TARGET)', () => {
      expect(parsePathToRoute('/drugs/aspirin')).toEqual({
        page: 'app',
        section: 'drugs',
        id: 'aspirin',
      });
    });

    it('parses /search/results/aspirin as app search results with id', () => {
      expect(parsePathToRoute('/search/results/aspirin')).toEqual({
        page: 'app',
        section: 'search',
        sub: 'results',
        id: 'aspirin',
      });
    });

    it('round-trips /drugs/aspirin (routeToPath → parsePathToRoute)', () => {
      const route = { page: 'app' as const, section: 'drugs', id: 'aspirin' };
      const encoded = routeToPath(route);
      const decoded = parsePathToRoute(encoded);
      expect(JSON.stringify(decoded)).toBe(JSON.stringify(route));
    });

    it('round-trips /dashboard (routeToPath → parsePathToRoute)', () => {
      const route = { page: 'app' as const, section: 'dashboard' };
      const encoded = routeToPath(route);
      const decoded = parsePathToRoute(encoded);
      expect(JSON.stringify(decoded)).toBe(JSON.stringify(route));
    });
  });

  describe('app/drugs/[drug]/page.tsx — the verification target route file', () => {
    it('the route file EXISTS', () => {
      expect(fileExists('src/app/drugs/[drug]/page.tsx')).toBe(true);
    });

    it('renders CoreScreenBridge with section="candidate" + the drug id', () => {
      const page = read('src/app/drugs/[drug]/page.tsx');
      // The page must decode the drug param and pass it to CoreScreenBridge.
      expect(page).toContain('decodeURIComponent');
      expect(page).toContain('CoreScreenBridge');
      expect(page).toContain('section="candidate"');
    });

    it('is wrapped in AppShell', () => {
      const page = read('src/app/drugs/[drug]/page.tsx');
      expect(page).toContain('AppShell');
      expect(page).toContain('section="drugs"');
    });
  });

  describe('app/layout.tsx — mounts NextRouterProvider (real next/navigation)', () => {
    const layout = read('src/app/layout.tsx');

    it('imports NextRouterProvider', () => {
      expect(layout).toContain('NextRouterProvider');
      expect(layout).toContain('next-router-provider');
    });

    it('wraps children in <NextRouterProvider>', () => {
      expect(layout).toMatch(/<NextRouterProvider>[\s\S]*\{children\}[\s\S]*<\/NextRouterProvider>/);
    });
  });

  describe('app/page.tsx — landing is a SERVER component (no use client)', () => {
    const page = read('src/app/page.tsx');

    it('is NOT marked as use client (server-rendered for SEO)', () => {
      // The first line of the file must NOT be 'use client'.
      const firstLine = page.split('\n')[0].trim();
      expect(firstLine).not.toBe("'use client'");
      expect(firstLine).not.toBe('"use client"');
    });

    it('redirects legacy ?p=... URLs to canonical paths', () => {
      // Task 12.1: legacy URLs (?p=app&s=dashboard) must redirect to /dashboard.
      expect(page).toContain('legacyToCanonicalUrl');
      expect(page).toContain('redirect');
    });

    it('renders LandingPage inside PublicLayout', () => {
      expect(page).toContain('LandingPage');
      expect(page).toContain('PublicLayout');
    });
  });

  describe('NextRouterProvider — bridges RouterContext to next/navigation', () => {
    const provider = read('src/components/drugos/next-router-provider.tsx');

    it('imports useRouter as useNextRouter from next/navigation', () => {
      expect(provider).toContain("useRouter as useNextRouter");
      expect(provider).toContain('next/navigation');
    });

    it('imports usePathname and useSearchParams from next/navigation', () => {
      expect(provider).toContain('usePathname');
      expect(provider).toContain('useSearchParams');
    });

    it('navigate() calls nextRouter.push(routeToPath(r))', () => {
      // The navigate function must convert the Route to a path and push it
      // via next/navigation's router (NOT window.history.pushState).
      expect(provider).toContain('routeToPath(r)');
      expect(provider).toContain('nextRouter.push');
    });
  });

  describe('loading.tsx + error.tsx — route-level loading and error states', () => {
    it('app/loading.tsx exists', () => {
      expect(fileExists('src/app/loading.tsx')).toBe(true);
    });

    it('app/error.tsx exists', () => {
      expect(fileExists('src/app/error.tsx')).toBe(true);
    });

    it('app/not-found.tsx exists (real 404, not silent fallback)', () => {
      expect(fileExists('src/app/not-found.tsx')).toBe(true);
    });

    it('error.tsx is a client component (Next.js requirement)', () => {
      const errorFile = read('src/app/error.tsx');
      expect(errorFile.startsWith("'use client'") || errorFile.startsWith('"use client"')).toBe(true);
    });

    it('error.tsx has a reset function for recovery', () => {
      const errorFile = read('src/app/error.tsx');
      expect(errorFile).toContain('reset');
    });
  });

  describe('all major app sections have real route files', () => {
    const expectedRoutes = [
      'dashboard',
      'search',
      'knowledge-graph',
      'interactions',
      'score-breakdown',
      'disease-detail',
      'prediction-explorer',
      'mechanism',
      'regulatory',
      'patents',
      'clinical-trials',
      'reports',
      'projects',
      'users',
      'billing',
      'profile',
      'preferences',
    ];

    it.each(expectedRoutes)('app/%s/page.tsx exists', (section) => {
      expect(fileExists(`src/app/${section}/page.tsx`)).toBe(true);
    });

    it('app/search/results/[query]/page.tsx exists (dynamic route for search results)', () => {
      expect(fileExists('src/app/search/results/[query]/page.tsx')).toBe(true);
    });

    it('app/safety/[drug]/page.tsx exists (dynamic route for drug safety)', () => {
      expect(fileExists('src/app/safety/[drug]/page.tsx')).toBe(true);
    });

    it('app/features/[slug]/page.tsx exists (dynamic route for feature pages)', () => {
      expect(fileExists('src/app/features/[slug]/page.tsx')).toBe(true);
    });
  });

  describe('marketing + auth pages have real route files', () => {
    const marketingPages = [
      'pricing',
      'about',
      'security',
      'status',
      'blog',
      'contact',
      'careers',
      'case-studies',
    ];
    const authPages = [
      'login',
      'register',
      'forgot-password',
      'reset-password',
      'mfa-challenge',
      'email-verification',
      'academic-verification',
      'org-selection',
      'onboarding-welcome',
      'onboarding-role',
      'onboarding-workspace',
      'onboarding-invite',
      'admin-approval',
      'account-locked',
    ];

    it.each(marketingPages)('app/%s/page.tsx exists (marketing)', (page) => {
      expect(fileExists(`src/app/${page}/page.tsx`)).toBe(true);
    });

    it.each(authPages)('app/%s/page.tsx exists (auth)', (page) => {
      expect(fileExists(`src/app/${page}/page.tsx`)).toBe(true);
    });
  });

  describe('legacy query-string URLs still parse (backwards compat)', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { parseUrlToRoute, routeToUrl } = require('../url-route');

    it('parses /?p=app&s=dashboard as app dashboard (legacy)', () => {
      expect(parseUrlToRoute('/?p=app&s=dashboard')).toEqual({
        page: 'app',
        section: 'dashboard',
      });
    });

    it('parses /?p=pricing as pricing (legacy)', () => {
      expect(parseUrlToRoute('/?p=pricing')).toEqual({ page: 'pricing' });
    });

    it('routeToUrl still produces query-string format (legacy)', () => {
      // Legacy codec is kept for backwards compat — new code uses routeToPath.
      expect(routeToUrl({ page: 'app', section: 'dashboard' })).toContain('p=app');
      expect(routeToUrl({ page: 'app', section: 'dashboard' })).toContain('s=dashboard');
    });
  });
});
