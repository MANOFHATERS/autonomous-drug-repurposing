import { NextRequest, NextResponse } from "next/server";
import { randomBytes } from "crypto";

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
 * FE-008 ROOT FIX (Teammate 14, HIGH) — replaces the previous FE-033 fix:
 *
 * The previous FE-033 fix generated a per-request nonce but ALSO kept
 * `'unsafe-inline'` in script-src as a "fallback for browsers that don't
 * support nonces". Per the CSP spec (CSP Level 2 §6.6.2), when a nonce
 * source expression is present in a source list, the 'unsafe-inline'
 * keyword MUST be ignored by modern browsers. So the comment was right
 * that 'unsafe-inline' was dead code for modern browsers — but it was
 * LIVE for every browser that didn't support nonces (which, in 2026,
 * includes several embedded WebViews used in pharma internal tools).
 *
 * The bigger problem: even on nonce-supporting browsers, 'unsafe-inline'
 * was a defense-in-depth failure. If a future refactor accidentally
 * removed the nonce from a single script tag (e.g., a third-party widget
 * loaded without nonce propagation), 'unsafe-inline' would silently
 * re-activate as the fallback — the CSP would still pass but XSS would
 * be possible. A patient-safety platform cannot rely on a "fallback"
 * that becomes the primary attack surface when the primary defense fails.
 *
 * ROOT FIX:
 *   1. Remove 'unsafe-inline' from `script-src` entirely. The nonce is
 *      the ONLY way an inline script can execute. Next.js 16 generates
 *      the nonce-attached hydration script automatically — no inline
 *      script in the app needs 'unsafe-inline'. If a future feature
 *      needs an inline script, the developer MUST attach the nonce
 *      explicitly (forcing them to think about it).
 *   2. Keep 'unsafe-inline' in `style-src` ONLY because Tailwind 4 and
 *      several Radix UI primitives inject inline styles at runtime via
 *      the `style` attribute (which is governed by style-src 'unsafe-inline'
 *      — nonces do NOT gate the `style` attribute, only <style> blocks).
 *      Removing 'unsafe-inline' from style-src would break the entire UI.
 *      This is a known limitation of CSP Level 2; CSP Level 3's 'unsafe-hashes'
 *      would let us whitelist specific style attributes, but Next.js 16
 *      doesn't generate those hashes automatically. We accept this trade-off:
 *      inline styles can't execute JS (so they're not an XSS vector).
 *   3. Restrict `connect-src` to 'self' plus an EXPLICIT ALLOWLIST of
 *      upstream biomedical APIs the frontend actually calls from the
 *      browser. The previous `connect-src 'self' https:` allowed an XSS
 *      payload to exfiltrate data to ANY https:// URL (e.g.,
 *      https://attacker.com/). The new allowlist is:
 *        - 'self' (Next.js API routes — same origin)
 *        - https://api.fda.gov (openFDA adverse events + drug labels)
 *        - https://clinicaltrials.gov (CT.gov search API)
 *        - https://eutils.ncbi.nlm.nih.gov (PubMed + MeSH E-utilities)
 *        - https://rxnav.nlm.nih.gov (RxNorm drug search)
 *        - https://id.nlm.nih.gov (NLM MeSH descriptor lookup)
 *        - https://search.patentsview.org (USPTO PatentsView patent search)
 *        - https://www.ebi.ac.uk (ChEMBL — for future direct browser calls)
 *      Note: in the current architecture, ALL of these are called from
 *      Next.js API routes (server-side), NOT from the browser. The browser
 *      only calls /api/* (same-origin). The allowlist is defense-in-depth —
 *      if a future component accidentally calls an external API from the
 *      browser, the CSP will block it unless the domain is on this list.
 *
 * Policy choices (FE-008 root fix):
 *   - default-src 'self': only same-origin resources by default.
 *   - script-src 'self' 'nonce-<random>': ONLY nonce-attached scripts run.
 *     No 'unsafe-inline' — see above.
 *   - style-src 'self' 'nonce-<random>' 'unsafe-inline': nonce for <style>
 *     blocks, 'unsafe-inline' for style ATTRIBUTES (Tailwind/Radix need it).
 *   - img-src 'self' data: https: avatar images from external CDNs.
 *   - connect-src 'self' https://api.fda.gov https://clinicaltrials.gov
 *     https://eutils.ncbi.nlm.nih.gov https://rxnav.nlm.nih.gov
 *     https://id.nlm.nih.gov https://search.patentsview.org
 *     https://www.ebi.ac.uk — explicit allowlist, no wildcards.
 *   - frame-ancestors 'none': prevent clickjacking (no iframing).
 *   - object-src 'none': no Flash/Java/PDF embeds.
 *   - base-uri 'self': prevent <base> tag hijacking.
 *   - form-action 'self': prevent form submissions to external origins.
 *
 * Additional headers:
 *   - X-Content-Type-Options: nosniff — prevent MIME-sniff XSS.
 *   - X-Frame-Options: DENY — legacy clickjacking protection.
 *   - Referrer-Policy: strict-origin-when-cross-origin — don't leak
 *     sensitive query params to external origins.
 *   - Permissions-Policy: deny camera, microphone, geolocation — DruGOS
 *     has no legitimate use for these.
 */
export function middleware(_req: NextRequest) {
  // FE-033 ROOT FIX (v115, MEDIUM): generate a per-request nonce.
  // The nonce is 32 random bytes, base64-encoded (44 chars). It's
  // attached to the response via the `x-nextjs-nonce` header —
  // Next.js 16 reads this header and injects the nonce into all
  // inline scripts it generates (hydration, RSC payload, etc.).
  // The CSP then only allows scripts/styles with this exact nonce.
  const nonce = randomBytes(32).toString("base64");

  const res = NextResponse.next();
  // Set the nonce header so Next.js picks it up.
  res.headers.set("x-nextjs-nonce", nonce);

  // FE-008 ROOT FIX: explicit upstream allowlist for connect-src.
  // Every domain here is a real public biomedical API the platform
  // integrates with. If a future feature needs a new upstream, the
  // developer MUST add it here — this is the chokepoint that prevents
  // silent data exfiltration via XSS.
  const connectSrcAllowlist = [
    "'self'",
    "https://api.fda.gov",
    "https://clinicaltrials.gov",
    "https://eutils.ncbi.nlm.nih.gov",
    "https://rxnav.nlm.nih.gov",
    "https://id.nlm.nih.gov",
    "https://search.patentsview.org",
    "https://www.ebi.ac.uk",
  ].join(" ");

  const csp = [
    "default-src 'self'",
    // FE-008 ROOT FIX: 'unsafe-inline' is REMOVED from script-src.
    // The nonce is the ONLY way an inline script can execute. This is
    // stricter than the previous FE-033 fix (which kept 'unsafe-inline'
    // as a "fallback" — see file header for why that was wrong).
    `script-src 'self' 'nonce-${nonce}'`,
    // FE-008 ROOT FIX: 'unsafe-inline' is KEPT in style-src ONLY for
    // the `style` attribute (Tailwind/Radix inject inline style attrs
    // at runtime; nonces do not gate style attributes, only <style>
    // blocks). Inline styles cannot execute JS, so this is not an XSS
    // vector. The nonce still gates <style> blocks.
    `style-src 'self' 'nonce-${nonce}' 'unsafe-inline'`,
    "img-src 'self' data: https:",
    "font-src 'self' data:",
    `connect-src ${connectSrcAllowlist}`,
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
