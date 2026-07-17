/**
 * FE-001 ROOT FIX: unit tests for URL-based routing codec.
 *
 * Before the fix, the router used `useState<Route>({ page: 'landing' })` —
 * pure in-memory state. Deep-linking, browser back/forward, refresh, and
 * shareable URLs were all broken. The fix syncs route state to the URL
 * query string via routeToUrl / parseUrlToRoute.
 *
 * These tests verify the round-trip identity: for every Route variant,
 * parseUrlToRoute(routeToUrl(route)) must equal the original route. They
 * also verify that invalid/malicious `p` params fall back to 'landing'
 * (no arbitrary string injection into React state).
 */
import { routeToUrl, parseUrlToRoute, roundTripPreserves, type Route } from '../url-route';

describe('FE-001: URL-based routing codec', () => {
  describe('routeToUrl', () => {
    it('encodes a landing route as "/"', () => {
      expect(routeToUrl({ page: 'landing' })).toBe('/');
    });

    it('encodes a simple page with ?p=', () => {
      expect(routeToUrl({ page: 'pricing' })).toBe('/?p=pricing');
      expect(routeToUrl({ page: 'login' })).toBe('/?p=login');
    });

    it('encodes a features page with ?p=features&slug=', () => {
      const url = routeToUrl({ page: 'features', slug: 'disease-search' });
      expect(url).toBe('/?p=features&slug=disease-search');
    });

    it('encodes an app route with section, sub, and id', () => {
      const url = routeToUrl({ page: 'app', section: 'dashboard' });
      expect(url).toBe('/?p=app&s=dashboard');

      const urlFull = routeToUrl({ page: 'app', section: 'search', sub: 'results', id: 'Huntingtons' });
      expect(urlFull).toContain('p=app');
      expect(urlFull).toContain('s=search');
      expect(urlFull).toContain('sub=results');
      expect(urlFull).toContain('id=Huntingtons');
    });
  });

  describe('parseUrlToRoute', () => {
    it('parses "/" as landing', () => {
      expect(parseUrlToRoute('http://localhost/')).toEqual({ page: 'landing' });
    });

    it('parses "?p=pricing" as pricing', () => {
      expect(parseUrlToRoute('http://localhost/?p=pricing')).toEqual({ page: 'pricing' });
    });

    it('parses "?p=features&slug=knowledge-graph"', () => {
      expect(parseUrlToRoute('http://localhost/?p=features&slug=knowledge-graph'))
        .toEqual({ page: 'features', slug: 'knowledge-graph' });
    });

    it('parses "?p=app&s=search&sub=results&id=Alzheimers"', () => {
      const route = parseUrlToRoute('http://localhost/?p=app&s=search&sub=results&id=Alzheimers');
      expect(route).toEqual({
        page: 'app',
        section: 'search',
        sub: 'results',
        id: 'Alzheimers',
      });
    });

    it('defaults section to "dashboard" when missing', () => {
      const route = parseUrlToRoute('http://localhost/?p=app');
      expect(route).toEqual({ page: 'app', section: 'dashboard' });
    });

    it('defaults slug to "disease-search" when missing', () => {
      const route = parseUrlToRoute('http://localhost/?p=features');
      expect(route).toEqual({ page: 'features', slug: 'disease-search' });
    });

    it('REJECTS an invalid page param (injection attempt) → falls back to landing', () => {
      // FE-001 security: an attacker cannot inject arbitrary page strings.
      const route = parseUrlToRoute('http://localhost/?p=<script>alert(1)</script>');
      expect(route).toEqual({ page: 'landing' });
    });

    it('REJECTS an unknown page name → falls back to landing', () => {
      const route = parseUrlToRoute('http://localhost/?p=admin');
      expect(route).toEqual({ page: 'landing' });
    });

    it('handles a malformed URL → falls back to landing', () => {
      const route = parseUrlToRoute('not-a-url');
      expect(route).toEqual({ page: 'landing' });
    });
  });

  describe('round-trip identity (FE-001 core invariant)', () => {
    const routes: Route[] = [
      { page: 'landing' },
      { page: 'pricing' },
      { page: 'about' },
      { page: 'security' },
      { page: 'status' },
      { page: 'blog' },
      { page: 'contact' },
      { page: 'careers' },
      { page: 'case-studies' },
      { page: 'login' },
      { page: 'register' },
      { page: 'forgot-password' },
      { page: 'reset-password' },
      { page: 'mfa-challenge' },
      { page: 'email-verification' },
      { page: 'academic-verification' },
      { page: 'org-selection' },
      { page: 'onboarding-welcome' },
      { page: 'onboarding-role' },
      { page: 'onboarding-workspace' },
      { page: 'onboarding-invite' },
      { page: 'admin-approval' },
      { page: 'account-locked' },
      { page: 'features', slug: 'disease-search' },
      { page: 'features', slug: 'knowledge-graph' },
      { page: 'app', section: 'dashboard' },
      { page: 'app', section: 'search' },
      { page: 'app', section: 'search', sub: 'results', id: 'Huntingtons' },
      { page: 'app', section: 'candidate', id: 'DC001' },
    ];

    for (const route of routes) {
      it(`round-trips ${JSON.stringify(route)}`, () => {
        expect(roundTripPreserves(route)).toBe(true);
      });
    }
  });
});
