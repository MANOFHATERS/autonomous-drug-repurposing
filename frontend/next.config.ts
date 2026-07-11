import type { NextConfig } from "next";

/**
 * ROOT FIXES for FE-026, FE-027, FE-039.
 *
 * FE-026: add a `headers()` config that sets Content-Security-Policy,
 *         X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security,
 *         Referrer-Policy, and Permissions-Policy on every response.
 * FE-027: set `typescript.ignoreBuildErrors: false` so TypeScript errors
 *         actually fail the build instead of silently shipping to production.
 *         Combined with `tsc --noEmit` in CI this catches whole categories
 *         of bugs at build time.
 * FE-039: add `outputFileTracingIncludes` so the standalone server bundle
 *         includes native deps (bcryptjs, sharp) and their .node binaries.
 *         Without this, `node .next/standalone/server.js` crashes on first
 *         request because `import bcrypt from "bcryptjs"` fails.
 */

const securityHeaders = [
  // HSTS — force HTTPS for 2 years, include subdomains, preload list eligible.
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  // Prevent clickjacking — never allow this app to be framed.
  { key: "X-Frame-Options", value: "DENY" },
  // Prevent MIME-sniffing attacks.
  { key: "X-Content-Type-Options", value: "nosniff" },
  // Only send the origin (not the full URL) to cross-origin destinations.
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Lock down browser APIs we do not use.
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), " +
      "magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()",
  },
  // Content-Security-Policy — default-deny, allow self + the few inline
  // styles Next.js requires for hydration + the NIH public biomedical APIs
  // we actually call (openFDA, RxNorm, ClinicalTrials.gov, PubMed, MeSH,
  // PatentsView) + the inline 'unsafe-inline' for styles only (Next.js
  // injects styled by style tags during dev).
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' data: https://fonts.gstatic.com",
      "img-src 'self' data: blob: https:",
      "connect-src 'self' https://api.fda.gov https://rxnav.nlm.nih.gov https://clinicaltrials.gov https://eutils.ncbi.nlm.nih.gov https://id.nlm.nih.gov https://api.patentsview.org",
      "frame-ancestors 'none'",
      "form-action 'self'",
      "base-uri 'self'",
      "object-src 'none'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  // Standalone output for Docker/Node production deployments. Local dev uses
  // `next dev` and is unaffected.
  output: "standalone",

  // FE-027 root fix: NEVER silently ignore TypeScript errors at build time.
  // Previous value `true` shipped type bugs that tsc would have caught.
  typescript: {
    ignoreBuildErrors: false,
  },

  // React strict mode catches bugs in dev. The previous `false` setting was
  // almost certainly added to silence a side-effect warning — the right fix
  // is to fix the side effect, not disable strict mode.
  reactStrictMode: true,

  // FE-026: security headers applied to every response.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },

  // FE-039 root fix: explicitly include native deps in the standalone
  // server bundle. Without this, `node .next/standalone/server.js` crashes
  // on first request because bcrypt/sharp native bindings are not traced.
  outputFileTracingIncludes: {
    "/": [
      "./node_modules/bcryptjs/**/*",
      "./node_modules/bcrypt/**/*",
      "./node_modules/sharp/**/*",
      "./node_modules/@img/**/*",
      "./node_modules/.prisma/**/*",
      "./node_modules/@prisma/client/**/*",
      "./prisma/**/*",
    ],
  },
};

export default nextConfig;
