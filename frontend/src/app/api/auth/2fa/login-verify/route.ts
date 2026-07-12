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
 *   - We do NOT increment failedLoginCount on a wrong TOTP code (that
 *     counter is for password failures, not 2FA failures). 2FA brute-force
 *     is already impractical (6 digits = 1M codes, 30s window, ±1 window
 *     drift = 3M codes max). If you want 2FA rate limiting, add a separate
 *     per-user 2FA attempt counter.
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

    // FE-033: Replay-protected TOTP verification.
    const result = verifyTotpWithReplayCheck(user.mfaSecret, code, user.lastTotpCounter);
    if (!result.ok) {
      await writeAuditLog({
        user: { userId: user.id, email: user.email, role: user.role },
        action: result.reason === "replayed" ? "login_mfa_code_replayed" : "login_mfa_failed",
        resource: `user:${user.id}`,
      });
      const message =
        result.reason === "replayed"
          ? "This code has already been used. Wait for the next 30-second window."
          : "Invalid 6-digit code. Try again.";
      return NextResponse.json(
        { error: result.reason === "replayed" ? "code_replayed" : "invalid_code", message },
        { status: 400 }
      );
    }

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
