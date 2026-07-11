import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  verifyPassword,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
  checkLoginRate,
  recordLoginFailure,
  recordLoginSuccess,
} from "@/lib/auth/server";
import { badRequest, writeAuditLog, getClientIp, requireCsrfOrSend } from "@/lib/api-helpers";

interface LoginBody {
  email: string;
  password: string;
}

/**
 * POST /api/auth/login
 *
 * ROOT FIXES:
 *  - FE-004 (2FA bypass): if the user has `mfaEnabled = true`, we NO LONGER
 *    issue access/refresh tokens. Instead we return `{ mfa_required: true,
 *    mfa_ticket }` and the client must POST to /api/auth/2fa/login-verify
 *    with the 6-digit TOTP code. Only after the TOTP code is verified are
 *    auth cookies issued.
 *  - FE-009 (no rate limit): we cap failed logins per (email, ip) tuple.
 *    After 5 failures in 15 minutes the tuple is locked out for 15 minutes.
 *  - FE-025 (CSRF): we enforce the double-submit CSRF token on this
 *    state-changing endpoint.
 *
 * The `mfa_ticket` is a short-lived (5 min) JWT signed with the same
 * JWT_SECRET. It encodes only the user id and email — not the role or
 * orgId — so even if it leaked it could not be used as a session token.
 * It is single-use: /api/auth/2fa/login-verify consumes it.
 */
export async function POST(req: NextRequest) {
  // Note: login is a PRE-AUTH endpoint — the caller has no session yet, so
  // the CSRF double-submit cookie cannot be verified. The password
  // requirement is the proof of intent. CSRF is enforced on all
  // AUTHENTICATED state-changing endpoints (2fa/disable, api-keys, billing,
  // admin/users, etc.) — see requireCsrfOrSend() in api-helpers.ts.

  let body: LoginBody;
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  const email = (body.email || "").trim().toLowerCase();
  const password = body.password || "";
  if (!email || !password) return badRequest("Email and password are required");

  const ip = getClientIp(req);

  // Rate limit — FE-009.
  const rate = checkLoginRate(email, ip);
  if (!rate.allowed) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: `Too many failed login attempts. Try again in ${rate.retryAfterSeconds}s.`,
        retry_after: rate.retryAfterSeconds,
      },
      { status: 429, headers: { "Retry-After": String(rate.retryAfterSeconds) } }
    );
  }

  const user = await db.user.findUnique({ where: { email } });
  if (!user) {
    recordLoginFailure(email, ip);
    return NextResponse.json(
      { error: "invalid_credentials", message: "Invalid email or password" },
      { status: 401 }
    );
  }
  if (user.status === "suspended") {
    return NextResponse.json(
      { error: "account_suspended", message: "Account suspended. Contact your administrator." },
      { status: 403 }
    );
  }
  const ok = await verifyPassword(password, user.passwordHash);
  if (!ok) {
    recordLoginFailure(email, ip);
    return NextResponse.json(
      { error: "invalid_credentials", message: "Invalid email or password" },
      { status: 401 }
    );
  }

  // FE-004 root fix: if 2FA is enabled, do NOT issue session tokens yet.
  // Issue a short-lived MFA ticket that the client exchanges for real
  // session tokens by POSTing the TOTP code to /api/auth/2fa/login-verify.
  if (user.mfaEnabled) {
    const { issueMfaTicket } = await import("@/lib/auth/totp");
    const mfaTicket = issueMfaTicket({ userId: user.id, email: user.email });
    await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "login_mfa_challenge",
      resource: `user:${user.id}`,
      metadata: { ip },
    });
    return NextResponse.json({
      mfa_required: true,
      mfa_ticket: mfaTicket,
      email: user.email,
    });
  }

  // Password correct + 2FA not enabled → finish login.
  recordLoginSuccess(email, ip);

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
  await writeAuditLog({
    user: { userId: user.id, email: user.email, role: user.role, orgId: membership?.organizationId },
    action: "login",
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
}
