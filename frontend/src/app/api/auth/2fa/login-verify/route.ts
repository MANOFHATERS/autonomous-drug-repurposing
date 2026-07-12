import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  verifyMfaChallengeToken,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
} from "@/lib/auth/server";
import { verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";
import {
  checkTotpRateLimit,
  recordFailedTotp,
  clearTotpAttempts,
} from "@/lib/auth/rate-limit";

/**
 * POST /api/auth/2fa/login-verify
 * Body: { mfaToken: string, code: string }
 *
 * FE-004 ROOT FIX: This endpoint did NOT exist before. The login route
 * never checked mfaEnabled, and the MFAChallengePage's "Verify" button
 * just navigated to the dashboard without verifying anything. 2FA was
 * purely cosmetic.
 *
 * FE-033 ROOT FIX: TOTP replay protection. Now uses
 * verifyTotpWithReplayCheck to reject already-used codes. The matching
 * counter is persisted atomically via updateMany with
 * `where: { lastTotpCounter: { lt: counter } }` so concurrent
 * verifications of the same code cannot both succeed.
 *
 * Now the flow is:
 *   1. User POSTs /api/auth/login with email+password.
 *   2. If password is correct AND user.mfaEnabled === true, login returns
 *      { mfaRequired: true, mfaToken: <5-min challenge token> }. NO access
 *      or refresh tokens are issued yet.
 *   3. Client collects the 6-digit TOTP code and POSTs here with
 *      { mfaToken, code }.
 *   4. We verify the challenge token (signature + expiry + type), look up
 *      the user, verify the TOTP code against the stored mfaSecret, and
 *      ONLY THEN issue access+refresh tokens.
 *
 * Security properties:
 *   - The challenge token is signed with the same JWT_SECRET but has
 *     type="mfa_challenge", so it CANNOT be used as an access token.
 *   - The challenge token expires in 5 minutes.
 *   - TOTP verification uses constant-time comparison (timingSafeEqual).
 *   - TOTP codes cannot be replayed (lastTotpCounter monotonically
 *     advances on each successful verification).
 *   - FE-003 ROOT FIX (v2): Per-user TOTP brute-force rate limiting is
 *     ENFORCED in this route via checkTotpRateLimit / recordFailedTotp.
 *     After 5 wrong TOTP codes within 5 minutes the account is locked
 *     for 15 minutes. The previous version had the rate-limit primitives
 *     in rate-limit.ts but NEVER CALLED THEM from this route — the test
 *     file totp-rate-limit.test.ts passed because it tested the primitives
 *     in isolation, but the actual HTTP endpoint was still brute-forceable
 *     (1M codes / 1000 req/s = 17 minutes for full keyspace). This is
 *     wired in NOW.
 */
export async function POST(req: NextRequest) {
  let body: { mfaToken?: string; code?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const mfaToken = (body.mfaToken || "").trim();
  const code = (body.code || "").trim();
  if (!mfaToken) return badRequest("mfaToken is required");
  if (!/^\d{6}$/.test(code)) {
    return badRequest("A 6-digit TOTP code is required");
  }

  // Verify the challenge token.
  const challenge = verifyMfaChallengeToken(mfaToken);
  if (!challenge) {
    return NextResponse.json(
      { error: "invalid_mfa_token", message: "MFA challenge token is invalid or expired. Please log in again." },
      { status: 401 }
    );
  }

  try {
    const user = await db.user.findUnique({
      where: { id: challenge.userId },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        status: true,
        mfaEnabled: true,
        mfaSecret: true,
        lastTotpCounter: true,
      },
    });
    if (!user) {
      return NextResponse.json(
        { error: "not_found", message: "User not found" },
        { status: 404 }
      );
    }
    if (user.status === "suspended") {
      return NextResponse.json(
        { error: "account_suspended", message: "Account suspended. Contact your administrator." },
        { status: 403 }
      );
    }
    if (!user.mfaEnabled || !user.mfaSecret) {
      return NextResponse.json(
        { error: "mfa_not_enabled", message: "MFA is not enabled on this account." },
        { status: 400 }
      );
    }

    // FE-003 ROOT FIX (v2): Per-user TOTP brute-force gate.
    // BEFORE we spend a TOTP verification attempt, check whether the user
    // is currently locked. This must come AFTER the user lookup (we need
    // the userId) but BEFORE the TOTP verify (so a locked user cannot
    // burn attempts). The IP-level rate limit (checkIpRateLimit) is a
    // separate outer layer that limits raw request volume; this inner
    // layer limits per-user TOTP guesses regardless of source IP.
    const totpLock = checkTotpRateLimit(user.id);
    if (totpLock.locked) {
      await writeAuditLog({
        user: { userId: user.id, email: user.email, role: user.role },
        action: "login_mfa_locked",
        resource: `user:${user.id}`,
      });
      return NextResponse.json(
        {
          error: "totp_locked",
          message: `Too many incorrect 2FA codes. Try again in ${Math.ceil(totpLock.retryAfterSeconds / 60)} minute(s).`,
          retryAfterSeconds: totpLock.retryAfterSeconds,
        },
        { status: 429, headers: { "Retry-After": String(totpLock.retryAfterSeconds) } }
      );
    }

    // FE-033: Replay-protected TOTP verification.
    const result = verifyTotpWithReplayCheck(user.mfaSecret, code, user.lastTotpCounter);
    if (!result.ok) {
      // FE-003 ROOT FIX (v2): Record the failed attempt. This increments
      // the per-user sliding-window counter and locks the account after
      // TOTP_MAX_ATTEMPTS (5) wrong codes within TOTP_WINDOW_MINUTES (5).
      const afterFail = recordFailedTotp(user.id);
      await writeAuditLog({
        user: { userId: user.id, email: user.email, role: user.role },
        action: result.reason === "replayed" ? "login_mfa_code_replayed" : "login_mfa_failed",
        resource: `user:${user.id}`,
      });
      const message =
        result.reason === "replayed"
          ? "This code has already been used. Wait for the next 30-second window."
          : afterFail.locked
            ? `Invalid 6-digit code. 2FA is now locked for ${Math.ceil(afterFail.retryAfterSeconds / 60)} minute(s) due to too many failed attempts.`
            : `Invalid 6-digit code. ${afterFail.attemptsRemaining} attempt(s) remaining before 2FA is locked.`;
      const status = afterFail.locked ? 429 : 400;
      const headers: Record<string, string> = {};
      if (afterFail.locked) headers["Retry-After"] = String(afterFail.retryAfterSeconds);
      return NextResponse.json(
        {
          error: afterFail.locked ? "totp_locked" : (result.reason === "replayed" ? "code_replayed" : "invalid_code"),
          message,
          attemptsRemaining: afterFail.attemptsRemaining,
          retryAfterSeconds: afterFail.retryAfterSeconds,
        },
        { status, headers }
      );
    }

    // FE-003 ROOT FIX (v2): Successful verification — clear the per-user
    // TOTP attempt counter so a user who eventually gets it right doesn't
    // carry a partial lock forward. This is the OWASP-recommended reset-
    // on-success pattern.
    clearTotpAttempts(user.id);

    // FE-033: Atomically advance lastTotpCounter. The `updateMany` with
    // `where: { lastTotpCounter: { lt: result.counter } }` ensures that
    // if two concurrent verifications of the same code race, only one
    // actually persists the update — the other is a no-op. This is the
    // standard RFC 6238 §5.2 replay-protection race prevention.
    if (user.lastTotpCounter === null) {
      await db.user.update({
        where: { id: user.id },
        data: { lastTotpCounter: result.counter },
      });
    } else {
      await db.user.updateMany({
        where: { id: user.id, lastTotpCounter: { lt: result.counter } },
        data: { lastTotpCounter: result.counter },
      });
    }

    // Success — issue the real access+refresh tokens.
    const membership = await db.organizationMember.findFirst({
      where: { userId: user.id },
      orderBy: { joinedAt: "asc" },
    });
    const tokens = await rotateRefreshToken(user.id);
    const access = signAccessToken({
      userId: user.id,
      email: user.email,
      role: user.role,
      orgId: membership?.organizationId,
    });
    await setAuthCookies(access, tokens.refresh);
    await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role, orgId: membership?.organizationId },
      action: "login_mfa_success",
      resource: `user:${user.id}`,
    });

    return NextResponse.json({
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        role: user.role,
      },
      organizationId: membership?.organizationId,
    });
  } catch (e) {
    console.error("2FA login-verify failed:", e);
    return internalError("Failed to verify 2FA code.");
  }
}
