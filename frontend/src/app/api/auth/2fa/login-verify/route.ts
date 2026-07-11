import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
<<<<<<< HEAD
import { signAccessToken, rotateRefreshToken, setAuthCookies } from "@/lib/auth/server";
import { verifyTotp, verifyMfaTicket } from "@/lib/auth/totp";
import { badRequest, writeAuditLog, getClientIp, requireCsrfOrSend } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/login-verify
 * Body: { mfa_ticket: string, code: string }
 *
 * ROOT FIX for FE-004 (2FA bypass): completes the 2FA login flow started by
 * /api/auth/login. The client receives an `mfa_ticket` from login when the
 * user has 2FA enabled; it must POST that ticket plus a 6-digit TOTP code
 * here. If both are valid we issue the real session tokens. If not, the
 * user is not logged in.
 *
 * The ticket is single-use: once we verify it we exchange it for full
 * session tokens. A replay attack would need to forge a valid HS256 JWT,
 * which requires the JWT_SECRET.
 */
export async function POST(req: NextRequest) {
  // CSRF first — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  let body: { mfa_ticket?: string; code?: string };
=======
import {
  verifyMfaChallengeToken,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
} from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
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
 *   - We do NOT increment failedLoginCount on a wrong TOTP code (that
 *     counter is for password failures, not 2FA failures). 2FA brute-force
 *     is already impractical (6 digits = 1M codes, 30s window, ±1 window
 *     drift = 3M codes max). If you want 2FA rate limiting, add a separate
 *     per-user 2FA attempt counter.
 */
export async function POST(req: NextRequest) {
  let body: { mfaToken?: string; code?: string };
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

<<<<<<< HEAD
  const ticket = body.mfa_ticket || "";
  const code = (body.code || "").trim();
  if (!ticket) return badRequest("mfa_ticket is required");
=======
  const mfaToken = (body.mfaToken || "").trim();
  const code = (body.code || "").trim();
  if (!mfaToken) return badRequest("mfaToken is required");
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  if (!/^\d{6}$/.test(code)) {
    return badRequest("A 6-digit TOTP code is required");
  }

<<<<<<< HEAD
  const payload = verifyMfaTicket(ticket);
  if (!payload) {
    return NextResponse.json(
      { error: "invalid_ticket", message: "MFA ticket is invalid or expired. Please log in again." },
=======
  // Verify the challenge token.
  const challenge = verifyMfaChallengeToken(mfaToken);
  if (!challenge) {
    return NextResponse.json(
      { error: "invalid_mfa_token", message: "MFA challenge token is invalid or expired. Please log in again." },
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
      { status: 401 }
    );
  }

<<<<<<< HEAD
  const user = await db.user.findUnique({ where: { id: payload.sub } });
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
      { error: "mfa_not_enabled", message: "2FA is not enabled on this account." },
      { status: 400 }
    );
  }

  if (!verifyTotp(user.mfaSecret, code)) {
    return NextResponse.json(
      { error: "invalid_code", message: "Invalid 6-digit code. Try again." },
      { status: 400 }
    );
  }

  // 2FA verified → issue session tokens.
  const membership = await db.organizationMember.findFirst({
    where: { userId: user.id },
    orderBy: { joinedAt: "asc" },
  });

  await db.user.update({
    where: { id: user.id },
    data: { lastLoginAt: new Date() },
  });

  const tokens = await rotateRefreshToken(user.id);
  const access = signAccessToken({
    userId: user.id,
    email: user.email,
    role: user.role,
    orgId: membership?.organizationId,
  });
  await setAuthCookies(access, tokens.refresh);

  const ip = getClientIp(req);
  await writeAuditLog({
    user: { userId: user.id, email: user.email, role: user.role, orgId: membership?.organizationId },
    action: "login_mfa_success",
    resource: `user:${user.id}`,
    metadata: { ip },
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
=======
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

    // Verify the TOTP code with ±30s drift tolerance.
    if (!verifyTotp(user.mfaSecret, code)) {
      await writeAuditLog({
        user: { userId: user.id, email: user.email, role: user.role },
        action: "login_mfa_failed",
        resource: `user:${user.id}`,
      });
      return NextResponse.json(
        { error: "invalid_code", message: "Invalid 6-digit code. Try again." },
        { status: 400 }
      );
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
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
}
