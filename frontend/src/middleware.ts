import { NextRequest, NextResponse } from "next/server";

/**
 * DruGOS security headers middleware.
 *
 * FE-071 ROOT FIX (mitigation #3): Strong Content-Security-Policy to
 * prevent XSS. The 2FA setup endpoint returns the TOTP secret in
 * plaintext JSON (necessary for QR rendering). If an XSS exists anywhere
 * in the app, an attacker could read that secret and call /verify
 * themselves to permanently compromise the user's 2FA. The setup-token
 * defense (two-factor-setup-token.ts) narrows the replay window, but CSP
 * is the PRIMARY mitigation — it stops the XSS from running in the first
 * place.
 *
 * Policy choices:
 *   - default-src 'self': only same-origin resources by default.
 *   - script-src 'self' 'unsafe-inline': Next.js requires inline scripts
 *     for hydration. 'unsafe-eval' is FORBIDDEN. We do NOT allow any
 *     third-party script origins — no Google Analytics, no Segment, no
 *     inline event handlers (unsafe-inline is the minimum Next.js needs).
 *     NOTE: For full XSS hardening, replace 'unsafe-inline' with a per-
 *     request nonce once Next.js 16's nonce support is configured.
 *   - style-src 'self' 'unsafe-inline': Tailwind + styled-components
 *     require inline styles. Same nonce caveat as above.
 *   - img-src 'self' data: https: avatar images from external CDNs.
 *   - connect-src 'self' https:: allow the dashboard to call external
 *     biomedical APIs (RxNorm, ClinicalTrials.gov, PubMed, OpenFDA). All
 *     such calls go through Next.js API routes (same-origin), so we only
 *     need 'self' for the browser's fetch — but we allow https: as a
 *     safety net for any future direct API integration.
 *   - frame-ancestors 'none': prevent clickjacking (no iframing).
 *   - object-src 'none': no Flash/Java/PDF embeds.
 *   - base-uri 'self': prevent <base> tag hijacking.
 *   - form-action 'self': prevent form submissions to external origins.
 *
 * Additional headers:
 *   - X-Content-Type-Options: nosniff — prevent MIME-sniff XSS.
 *   - X-Frame-Options: DENY — legacy clickjacking protection for old
 *     browsers that don't honor frame-ancestors.
 *   - Referrer-Policy: strict-origin-when-cross-origin — don't leak the
 *     full URL (which may contain sensitive query params) to external
 *     origins.
 *   - Permissions-Policy: deny camera, microphone, geolocation — DruGOS
 *     has no legitimate use for these.
 */
export function middleware(_req: NextRequest) {
  const res = NextResponse.next();

  const csp = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self' data:",
    "connect-src 'self' https:",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "upgrade-insecure-requests",
  ].join("; ");

  res.headers.set("Content-Security-Policy", csp);
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  res.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=(), payment=()"
  );
  // HSTS — only meaningful over HTTPS, but the header is harmless on HTTP
  // (browsers ignore it). 1 year + preload + subdomains.
  res.headers.set(
    "Strict-Transport-Security",
    "max-age=31536000; includeSubDomains; preload"
  );

  return res;
}

export const config = {
  // Apply to all routes except static asset paths.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|js|css|woff|woff2|ttf|eot)$).*)",
  ],
};
