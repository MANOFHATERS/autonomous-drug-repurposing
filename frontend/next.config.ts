import type { NextConfig } from "next";

/**
 * BE-012 ROOT FIX (v115, HIGH): production security headers.
 *
 * ROOT CAUSE: the previous next.config.ts had ZERO security headers.
 * The middleware.ts file set CSP / X-Frame-Options / etc., but only on
 * routes that matched the middleware matcher (which EXCLUDES static
 * assets, _next/static, etc.). Worse, middleware runs AFTER Next.js
 * serves a response for some paths — so the headers were missing on
 * critical responses.
 *
 * The OWASP ASVS V5.1 requires a hardened HTTP response header set on
 * EVERY response from a production web app. The Next.js `headers()`
 * function in next.config.ts is the canonical place for these — it
 * applies to every route (including static assets, API routes, and
 * the Next.js internal routes) and is evaluated at build time so there
 * is no runtime overhead.
 *
 * FE-008 ROOT FIX (Teammate 14, HIGH): the previous version of this
 * file ALSO set a Content-Security-Policy header here. Two problems:
 *
 *   1. Next.js applies `headers()` from next.config.ts AFTER middleware.
 *      That means the CSP set here OVERWRITES the per-request nonce-based
 *      CSP that middleware.ts generates. The middleware's nonce is
 *      silently thrown away, and the looser 'unsafe-inline' CSP from
 *      here wins for every dynamic route. The nonce-based defense was
 *      dead code.
 *
 *   2. The CSP here allowed `script-src 'self' 'unsafe-inline'` and
 *      `connect-src 'self' https:` — both OWASP anti-patterns. A single
 *      XSS injection (e.g., from a dangerouslySetInnerHTML elsewhere)
 *      executes arbitrary JS, and an XSS payload can exfiltrate data
 *      to any https:// URL.
 *
 * ROOT FIX:
 *   - The CSP is now set ONLY in middleware.ts, which generates a fresh
 *     32-byte nonce per request and uses `script-src 'self' 'nonce-<n>'`
 *     (no 'unsafe-inline'). The middleware also restricts `connect-src`
 *     to an explicit allowlist of upstream biomedical APIs.
 *   - This file keeps the OTHER security headers (X-Frame-Options,
 *     X-Content-Type-Options, Referrer-Policy, HSTS, Permissions-Policy)
 *     because they don't depend on per-request state and are safe to
 *     apply globally (including to static assets that bypass middleware).
 *   - These headers do NOT conflict with middleware — middleware sets
 *     its own values for them, and Next.js lets the middleware values
 *     win when both are present (X-Frame-Options etc. are not
 *     per-request, so the values match).
 */
const securityHeaders = [
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  // FE-008 ROOT FIX: NO Content-Security-Policy header here. The CSP is
  // owned by middleware.ts, which generates a per-request nonce. Setting
  // a CSP here would either (a) overwrite the middleware's nonce-based
  // CSP (because next.config.ts headers() run AFTER middleware) or
  // (b) require 'unsafe-inline' (because next.config.ts cannot generate
  // per-request nonces). Both options are worse than letting middleware
  // own the CSP. See middleware.ts for the strict, nonce-based, explicit
  // connect-src allowlist CSP.
];

const nextConfig: NextConfig = {
  // Note: output: "standalone" is enabled for production Docker/Node deployments.
  // Disable it locally if you just want `next dev` / `next start` to work without
  // copying the .next/standalone folder around.
  output: "standalone",
  // FE-011/FE-012/FE-013 ROOT FIX: typescript.ignoreBuildErrors was previously
  // `true`, which let broken imports silently pass the build. Production-grade
  // code MUST fail the build on type errors.
  typescript: {
    ignoreBuildErrors: false,
  },
  // FE-028 ROOT FIX: reactStrictMode was disabled — React 19's built-in
  // bug detection was off. Strict mode catches stale closures and missing
  // effect cleanups BEFORE they reach production.
  reactStrictMode: true,
  // BE-012 ROOT FIX (v115): apply security headers to EVERY response,
  // including static assets that bypass the middleware matcher.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
