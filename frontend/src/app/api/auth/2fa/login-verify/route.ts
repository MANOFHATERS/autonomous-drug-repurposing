import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
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
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const ticket = body.mfa_ticket || "";
  const code = (body.code || "").trim();
  if (!ticket) return badRequest("mfa_ticket is required");
  if (!/^\d{6}$/.test(code)) {
    return badRequest("A 6-digit TOTP code is required");
  }

  const payload = verifyMfaTicket(ticket);
  if (!payload) {
    return NextResponse.json(
      { error: "invalid_ticket", message: "MFA ticket is invalid or expired. Please log in again." },
      { status: 401 }
    );
  }

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
}
