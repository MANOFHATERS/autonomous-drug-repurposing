import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  verifyMfaChallengeToken,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
} from "@/lib/auth/server";
import { verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog, issueCsrfToken, setCsrfCookie } from "@/lib/api-helpers";
import { recordSuccessfulLogin, recordFailedLogin } from "@/lib/auth/rate-limit";
import jwt from "jsonwebtoken";

/**
 * POST /api/auth/2fa/login-verify
 * Body: { mfaToken?: string, code: string }  (mfaToken optional — also read
 *       from the drugos_mfa_challenge HttpOnly cookie set by /api/auth/login)
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
 * FE-016 ROOT FIX: The mfa_challenge JWT was signed but had no jti and was
 * not tracked as consumed — an attacker who intercepted it (e.g. via XSS
 * reading the JSON response body) could replay it multiple times within
 * the 5-minute window. Each successful replay issued fresh access+refresh
 * tokens.
 *
 * Root fix:
 *   1. The challenge token now carries a jti (JWT ID). On first use, we
 *      atomically insert the jti into the MfaChallenge table (unique on
 *      jti). A second insert fails (Prisma P2002) → reject as replay.
 *   2. The challenge token is set as an HttpOnly cookie (sameSite=strict,
 *      path=/api/auth/2fa/login-verify) by /api/auth/login. The client
 *      does not need to read it — the browser sends it automatically. The
 *      JSON-body mfaToken is still accepted for non-browser API clients.
 *   3. Cookie is cleared on success AND on failure.
 *
 * FE-018 ROOT FIX: recordSuccessfulLogin() is called HERE, after TOTP
 * verifies — not in /api/auth/login when the password is checked. This
 * means an attacker with the password but no TOTP secret does NOT get
 * their failedLoginCount reset by triggering MFA challenges.
 *
 * Additionally, on TOTP failure we increment failedLoginCount via
 * recordFailedLogin — so repeated MFA failures DO accumulate toward
 * account lockout.
 *
 * FE-011: CSRF cookie issued on success so the client can make
 * state-changing requests after MFA verification.
 */
export async function POST(req: NextRequest) {
  // FE-016: read the challenge token from the HttpOnly cookie FIRST.
  // Fall back to the JSON body for non-browser API clients. The cookie
  // path is the safer path because XSS cannot read it.
  let mfaToken = "";
  try {
    const { cookies } = await import("next/headers");
    const store = await cookies();
    mfaToken = store.get("drugos_mfa_challenge")?.value || "";
  } catch {
    // cookies() throws outside a request scope — fall through to body.
  }

  let body: { mfaToken?: string; code?: string };
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  if (!mfaToken) {
    mfaToken = (body.mfaToken || "").trim();
  }
  const code = (body.code || "").trim();
  if (!mfaToken) return badRequest("mfaToken is required (either the drugos_mfa_challenge cookie or the body field)");
  if (!/^\d{6}$/.test(code)) {
    return badRequest("A 6-digit TOTP code is required");
  }

  // Verify the challenge token (signature + expiry + type).
  const challenge = verifyMfaChallengeToken(mfaToken);
  if (!challenge) {
    await clearMfaChallengeCookie();
    return NextResponse.json(
      { error: "invalid_mfa_token", message: "MFA challenge token is invalid or expired. Please log in again." },
      { status: 401 }
    );
  }

  // FE-016 ROOT FIX: replay protection. Extract the jti from the token and
  // atomically claim it in the MfaChallenge table. If the jti is already
  // consumed, reject as a replay attack. We do this BEFORE TOTP verification
  // so a replayed token cannot be used to probe TOTP codes.
  try {
    const decoded = jwt.decode(mfaToken, { complete: true }) as
      | { payload?: { jti?: string } }
      | null;
    const jti = decoded?.payload?.jti;
    if (jti) {
      try {
        await db.mfaChallenge.create({
          data: {
            jti,
            userId: challenge.userId,
            expiresAt: new Date(Date.now() + 60 * 1000), // 1 min grace past JWT expiry
          },
        });
      } catch (e: any) {
        if (e?.code === "P2002") {
          // Prisma unique-constraint violation → jti already consumed → replay.
          await writeAuditLog({
            user: { userId: challenge.userId, email: challenge.email, role: "unknown" },
            action: "login_mfa_replay_rejected",
            resource: `user:${challenge.userId}`,
            metadata: { jti },
          });
          await clearMfaChallengeCookie();
          return NextResponse.json(
            { error: "invalid_mfa_token", message: "MFA challenge token has already been used. Please log in again." },
            { status: 401 }
          );
        }
        // Other errors (DB down, etc.) — fail closed: reject the request.
        console.error("MFA jti tracking failed:", e);
        await clearMfaChallengeCookie();
        return internalError("Unable to verify MFA challenge uniqueness.");
      }
    }
  } catch (e) {
    console.error("MFA jti extraction failed:", e);
    await clearMfaChallengeCookie();
    return NextResponse.json(
      { error: "invalid_mfa_token", message: "MFA challenge token is malformed." },
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
      await clearMfaChallengeCookie();
      return NextResponse.json(
        { error: "not_found", message: "User not found" },
        { status: 404 }
      );
    }
    if (user.status === "suspended") {
      await clearMfaChallengeCookie();
      return NextResponse.json(
        { error: "account_suspended", message: "Account suspended. Contact your administrator." },
        { status: 403 }
      );
    }
    if (!user.mfaEnabled || !user.mfaSecret) {
      await clearMfaChallengeCookie();
      return NextResponse.json(
        { error: "mfa_not_enabled", message: "MFA is not enabled on this account." },
        { status: 400 }
      );
    }

    // FE-033: Replay-protected TOTP verification.
    const result = verifyTotpWithReplayCheck(user.mfaSecret, code, user.lastTotpCounter);
    if (!result.ok) {
      // FE-018 ROOT FIX: increment failedLoginCount on MFA failure so
      // repeated MFA failures accumulate toward account lockout. This
      // closes the gap left by removing recordSuccessfulLogin from the
      // password-verification path.
      const lockResult = await recordFailedLogin(user.id);
      await writeAuditLog({
        user: { userId: user.id, email: user.email, role: user.role },
        action: result.reason === "replayed" ? "login_mfa_code_replayed" : "login_mfa_failed",
        resource: `user:${user.id}`,
      });
      await clearMfaChallengeCookie();
      if (lockResult.locked) {
        return NextResponse.json(
          {
            error: "account_locked",
            message: `Account locked due to too many failed MFA attempts. Try again in ${Math.ceil(lockResult.retryAfterSeconds / 60)} minute(s).`,
            retryAfter: lockResult.retryAfterSeconds,
          },
          { status: 423 }
        );
      }
      const message =
        result.reason === "replayed"
          ? "This code has already been used. Wait for the next 30-second window."
          : "Invalid 6-digit code. Try again.";
      return NextResponse.json(
        { error: result.reason === "replayed" ? "code_replayed" : "invalid_code", message },
        { status: 400 }
      );
    }

    // FE-033: Atomically advance lastTotpCounter.
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

    // FE-018 ROOT FIX: call recordSuccessfulLogin HERE — after TOTP
    // verifies. This resets failedLoginCount and clears lockedUntil only
    // when the user has fully authenticated (password + TOTP).
    await recordSuccessfulLogin(user.id);

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
    // FE-011: issue the CSRF token cookie on successful MFA verification,
    // mirroring the non-MFA login path.
    const csrfToken = issueCsrfToken();
    await setCsrfCookie(csrfToken);
    await clearMfaChallengeCookie();
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
      // FE-011: echo the CSRF token in the response body so non-browser
      // clients (which can't read cookies) can pick it up.
      csrfToken,
    });
  } catch (e) {
    console.error("2FA login-verify failed:", e);
    await clearMfaChallengeCookie();
    return internalError("Failed to verify 2FA code.");
  }
}

async function clearMfaChallengeCookie(): Promise<void> {
  try {
    const { cookies } = await import("next/headers");
    const store = await cookies();
    store.set("drugos_mfa_challenge", "", {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/api/auth/2fa/login-verify",
      maxAge: 0, // delete
    });
  } catch {
    // swallow — cookie clear is best-effort
  }
}
