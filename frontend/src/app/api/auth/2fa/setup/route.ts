import { NextResponse, NextRequest } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { generateTotpSecret, buildOtpAuthUri } from "@/lib/auth/totp";
import { internalError, requireCsrfOrSend } from "@/lib/api-helpers";
import { issue2faSetupToken } from "@/lib/auth/two-factor-setup-token";
// BE-027 v123: distributed per-user rate limit for 2FA setup.
import { check2faSetupRateLimitDistributed } from "@/lib/auth/rate-limit";

/**
 * POST /api/auth/2fa/setup
 *
 * Generates a brand-new TOTP secret for the authenticated user and returns
 * it (along with an otpauth:// URI) so the client can display a QR code.
 *
 * FE-071 ROOT FIX: The previous version returned the TOTP secret in
 * plaintext with no setup token. If any XSS exists in the app, an
 * attacker could read the secret and call /verify themselves to persist
 * 2FA under their control — permanent account compromise.
 *
 * Root fix: alongside the secret, issue a one-time, 5-minute setup token
 * bound to the user's session. The client must send BOTH the secret AND
 * the setupToken to /verify. The setup token:
 *   - Can only be used ONCE (replay attacks rejected).
 *   - Expires after 5 minutes.
 *   - Is bound to the userId (stolen token cannot enroll 2FA for a
 *     different user).
 *
 * This is defense-in-depth on top of the CSP headers (the primary XSS
 * mitigation, enforced via next.config.ts). The combination closes the
 * XSS → 2FA compromise chain.
 *
 * The secret is NOT persisted yet — the user must call /api/auth/2fa/verify
 * with the code from their authenticator app + the setupToken to confirm
 * they have stored it. Only then do we set `mfaEnabled = true`.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  // BE-027 v123 FORENSIC ROOT FIX: rate-limit /api/auth/2fa/setup per user.
  // The previous route had NO rate limit. A malicious user could call it
  // 10001 times to fill the in-memory LRU (two-factor-setup-token.ts),
  // evicting other users' pending tokens and sabotaging their 2FA
  // enrollment. The limit is 5 attempts per 5 minutes per user (generous
  // — a legitimate user rarely retries enrollment more than once or
  // twice). Distributed via Redis when available, with per-process
  // in-memory fallback (same fallback semantics as the IP rate limiter).
  const rl = await check2faSetupRateLimitDistributed(user.userId);
  if (rl.blocked) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: "Too many 2FA setup attempts. Please wait and try again.",
        retryAfterSeconds: rl.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(rl.retryAfterSeconds) },
      },
    );
  }

  try {
    const secret = generateTotpSecret();
    const uri = buildOtpAuthUri({
      issuer: "DrugOS",
      account: user.email,
      secret,
    });
    // FE-071: issue a one-time setup token bound to this user's session.
    // The client must send it back to /verify alongside the secret + code.
    // BE-078 v123: issue2faSetupToken is now async (DB-backed for multi-
    // instance race protection). Await it.
    const { setupToken, expiresAt } = await issue2faSetupToken(user.userId, secret);
    return NextResponse.json({
      secret,
      otpauthUri: uri,
      setupToken,
      setupTokenExpiresAt: expiresAt,
    });
  } catch (e) {
    console.error("2FA setup failed:", e);
    return internalError("Failed to start 2FA enrollment.");
  }
}
