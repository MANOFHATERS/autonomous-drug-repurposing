import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  verifyPassword,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
  signMfaChallengeToken,
} from "@/lib/auth/server";
import { badRequest, writeAuditLog } from "@/lib/api-helpers";
import {
  checkIpRateLimit,
  recordIpAttempt,
  checkAccountLocked,
  recordFailedLogin,
  recordSuccessfulLogin,
} from "@/lib/auth/rate-limit";

interface LoginBody {
  email: string;
  password: string;
}

export async function POST(req: NextRequest) {
  // FE-009 ROOT FIX (Layer 1): IP-based rate limit. Stops credential
  // stuffing where an attacker rotates usernames from one IP. Done BEFORE
  // parsing the body so even malformed requests count against the bucket.
  const ipCheck = checkIpRateLimit(req);
  if (ipCheck.blocked) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: `Too many login attempts from this IP. Try again in ${Math.ceil(ipCheck.retryAfterSeconds / 60)} minute(s).`,
        retryAfter: ipCheck.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(ipCheck.retryAfterSeconds) },
      }
    );
  }

  let body: LoginBody;
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  const email = (body.email || "").trim().toLowerCase();
  const password = body.password || "";
  if (!email || !password) return badRequest("Email and password are required");

  const user = await db.user.findUnique({
    where: { email },
    select: {
      id: true,
      email: true,
      passwordHash: true,
      role: true,
      status: true,
      name: true,
      mfaEnabled: true,
      mfaSecret: true,
      failedLoginCount: true,
      lockedUntil: true,
    },
  });

  // Use the same error message for "user not found" and "wrong password" so
  // an attacker can't enumerate accounts by email.
  const invalidCredentials = () =>
    NextResponse.json(
      { error: "invalid_credentials", message: "Invalid email or password" },
      { status: 401 }
    );

  if (!user) {
    recordIpAttempt(req);
    return invalidCredentials();
  }

  if (user.status === "suspended") {
    return NextResponse.json(
      { error: "account_suspended", message: "Account suspended. Contact your administrator." },
      { status: 403 }
    );
  }

  // FE-009 ROOT FIX (Layer 2): Per-account lockout. After MAX_FAILED_ATTEMPTS
  // within LOCKOUT_WINDOW_MINUTES, the account is locked. The UI's
  // AccountLockedPage is now actually reachable.
  const lockCheck = checkAccountLocked(user);
  if (lockCheck.locked) {
    return NextResponse.json(
      {
        error: "account_locked",
        message: `Account locked due to too many failed login attempts. Try again in ${Math.ceil(lockCheck.retryAfterSeconds / 60)} minute(s).`,
        retryAfter: lockCheck.retryAfterSeconds,
      },
      {
        status: 423,
        headers: { "Retry-After": String(lockCheck.retryAfterSeconds) },
      }
    );
  }

  const ok = await verifyPassword(password, user.passwordHash);
  if (!ok) {
    recordIpAttempt(req);
    // FE-009: Increment failedLoginCount; auto-lock if threshold hit.
    const lockResult = await recordFailedLogin(user.id);
    await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "login_failed",
      resource: `user:${user.id}`,
    });
    if (lockResult.locked) {
      return NextResponse.json(
        {
          error: "account_locked",
          message: `Account locked due to too many failed login attempts. Try again in ${Math.ceil(lockResult.retryAfterSeconds / 60)} minute(s).`,
          retryAfter: lockResult.retryAfterSeconds,
        },
        {
          status: 423,
          headers: { "Retry-After": String(lockResult.retryAfterSeconds) },
        }
      );
    }
    return invalidCredentials();
  }

  // FE-004 ROOT FIX: 2FA challenge gate. If the user has mfaEnabled=true,
  // we do NOT issue access+refresh tokens yet. Instead we return a short-lived
  // mfa_challenge token (5 min TTL) that the client must use to verify a TOTP
  // code at /api/auth/2fa/login-verify. Only then do we issue real tokens.
  //
  // The previous code skipped this check entirely — a user with 2FA enrolled
  // could log in with just a password. The TOTP implementation in totp.ts
  // was dead code on the auth path.
  if (user.mfaEnabled) {
    // Reset failed counter on successful password — but DO NOT issue tokens.
    await recordSuccessfulLogin(user.id);
    const mfaToken = signMfaChallengeToken({
      userId: user.id,
      email: user.email,
    });
    await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "login_mfa_challenge_issued",
      resource: `user:${user.id}`,
    });
    return NextResponse.json({
      mfaRequired: true,
      mfaToken,
      message: "Multi-factor authentication required. POST this token and your 6-digit TOTP code to /api/auth/2fa/login-verify.",
    });
  }

  // Find the user's primary organization (first membership).
  const membership = await db.organizationMember.findFirst({
    where: { userId: user.id },
    orderBy: { joinedAt: "asc" },
  });

  // FE-009: Reset failed counter on successful login.
  await recordSuccessfulLogin(user.id);
  recordIpAttempt(req);

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
