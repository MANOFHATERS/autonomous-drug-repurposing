import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";
import jwt from "jsonwebtoken";

/**
 * POST /api/auth/verify-email
 * Body: { token: string }
 *
 * FE-035 ROOT FIX: Email verification flow.
 *
 * Previously, registration set emailVerified=false but never sent a
 * verification email (nodemailer was in package.json but never imported).
 * The flag was a lie — it was always false and never checked.
 *
 * Now the flow is:
 *   1. /api/auth/register creates the user with emailVerified=false,
 *      signs a 24h verification JWT, and "sends" it via EMAIL_SERVICE_URL
 *      (or logs to stderr in dev mode).
 *   2. The user clicks the link in the email, which POSTs the token here.
 *   3. We verify the JWT signature + expiry + type==='email_verify'.
 *   4. We mark the user's emailVerified=true.
 *   5. The user can now log in via /api/auth/login (which rejects
 *      unverified accounts).
 *
 * Security properties:
 *   - The token is signed with JWT_SECRET (same as access tokens).
 *   - The token expires in 24 hours.
 *   - The token type is 'email_verify', so it CANNOT be used as an
 *     access token (verifyAccessToken checks type==='access').
 *   - We do NOT issue access+refresh tokens here — the user must log in
 *     separately after verification.
 */
export async function POST(req: NextRequest) {
  let body: { token?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const token = (body.token || "").trim();
  if (!token) return badRequest("token is required");

  const secret = process.env.JWT_SECRET;
  // BE-063 ROOT FIX: Default to PRODUCTION behavior when NODE_ENV is unset.
  // A misconfigured production deploy (missing NODE_ENV) must NOT use the
  // dev fallback secret.
  const isProd =
    process.env.NODE_ENV !== "development" && process.env.NODE_ENV !== "test";
  if (!secret || secret.length < 32) {
    if (isProd) {
      return internalError("JWT_SECRET not configured.");
    }
    // Dev fallback — same secret as auth/server.ts. ONLY used in dev/test.
  }
  const actualSecret = secret && secret.length >= 32
    ? secret
    : "dev-only-insecure-secret-change-me-MINIMUM-32-CHARS-FOR-HS256!!";

  let decoded: { sub: string; email: string; type: string };
  try {
    decoded = jwt.verify(token, actualSecret, {
      issuer: "drugos",
      algorithms: ["HS256"],
    }) as { sub: string; email: string; type: string };
  } catch {
    return NextResponse.json(
      { error: "invalid_or_expired_token", message: "The verification link is invalid or has expired. Please request a new one." },
      { status: 400 }
    );
  }

  if (decoded.type !== "email_verify" || !decoded.sub) {
    return NextResponse.json(
      { error: "invalid_token", message: "Invalid token type." },
      { status: 400 }
    );
  }

  try {
    const user = await db.user.findUnique({ where: { id: decoded.sub } });
    // BE-064 ROOT FIX: Return the SAME "invalid_or_expired_token" error for
    // both "user not found" AND "email mismatch". The previous code returned
    // "not_found" (404) for missing users and "email_mismatch" (400) for
    // email mismatches. An attacker who steals a verification token for
    // email A and tries to use it for email B would see "email_mismatch" —
    // confirming that email A's user exists. By collapsing both cases into
    // the same generic error, we eliminate this enumeration channel.
    if (!user || user.email !== decoded.email) {
      return NextResponse.json(
        {
          error: "invalid_or_expired_token",
          message: "The verification link is invalid or has expired. Please request a new one.",
        },
        { status: 400 }
      );
    }
    if (user.emailVerified) {
      // Idempotent — already verified. Return success.
      return NextResponse.json({ ok: true, alreadyVerified: true });
    }

    await db.user.update({
      where: { id: user.id },
      data: { emailVerified: true },
    });

    // FE-034: Verification is a security event — audit it as critical.
    const audit = await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "email_verified",
      resource: `user:${user.id}`,
      critical: true,
    });
    if (!audit.ok) {
      // The email was verified, but the audit failed. The user CAN log
      // in (emailVerified is true), but we log the audit failure.
      console.error("[AUDIT-LOG-FAILURE] email_verified action could not be audited.");
    }

    return NextResponse.json({ ok: true, alreadyVerified: false });
  } catch (e) {
    console.error("Email verification failed:", e);
    return internalError("Failed to verify email.");
  }
}
