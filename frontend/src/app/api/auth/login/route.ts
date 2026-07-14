import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  verifyPassword,
  signAccessToken,
  rotateRefreshToken,
  setAuthCookies,
  signMfaChallengeToken,
  clearAuthCookies,
} from "@/lib/auth/server";
import { badRequest, writeAuditLog, internalError, issueCsrfToken, setCsrfCookie } from "@/lib/api-helpers";
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

  // FE-056 ROOT FIX: Record the IP attempt UP FRONT for EVERY request that
  // passes the block check — including malformed JSON, missing fields, and
  // any other early-return path. Previously recordIpAttempt was only called
  // on user-not-found, wrong-password, and successful-login paths, so an
  // attacker could send unlimited malformed-request probes without consuming
  // their rate-limit budget. Recording unconditionally closes that gap.
  recordIpAttempt(req);

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
      emailVerified: true,
      name: true,
      mfaEnabled: true,
      mfaSecret: true,
      failedLoginCount: true,
      lockedUntil: true,
      // FE-055 ROOT FIX: include deletedAt so we can refuse login for
      // soft-deleted accounts. They should appear as "invalid credentials"
      // to the caller (no enumeration leak) but we record an IP attempt.
      deletedAt: true,
    },
  });

  // Use the same error message for "user not found" and "wrong password" so
  // an attacker can't enumerate accounts by email.
  const invalidCredentials = () =>
    NextResponse.json(
      { error: "invalid_credentials", message: "Invalid email or password" },
      { status: 401 }
    );

  // FE-055 ROOT FIX: Treat a soft-deleted user as if they don't exist.
  // (The IP attempt was already recorded up-front at line 49 — FE-056 —
  // so deleted-account probes consume the rate-limit budget correctly.)
  if (!user || user.deletedAt !== null) {
    return invalidCredentials();
  }

  if (user.status === "suspended") {
    return NextResponse.json(
      { error: "account_suspended", message: "Account suspended. Contact your administrator." },
      { status: 403 }
    );
  }

  // FE-035 ROOT FIX: Reject unverified accounts. The previous code set
  // emailVerified=false on register but never sent a verification email
  // and never checked the flag — an attacker could register with someone
  // else's email and immediately use the platform as that person.
  //
  // Now registration sends a real verification email (via EMAIL_SERVICE_URL
  // in prod, stderr in dev) and the user MUST click the link before they
  // can log in. We return 403 with a clear message so the UI can prompt
  // the user to check their inbox or request a new link.
  if (!user.emailVerified) {
    return NextResponse.json(
      {
        error: "email_not_verified",
        message: "Please verify your email before logging in. Check your inbox for a verification link.",
      },
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
    // FE-056: recordIpAttempt was already called up-front at line 49.
    // FE-009: Increment failedLoginCount; auto-lock if threshold hit.
    const lockResult = await recordFailedLogin(user.id);
    // FE-034: login_failed is security-critical — must be auditable.
    const auditResult = await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "login_failed",
      resource: `user:${user.id}`,
      critical: true,
    });
    if (!auditResult.ok) {
      return internalError("Failed to record login failure in audit log.");
    }
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
    // FE-018 ROOT FIX: Do NOT call recordSuccessfulLogin() here. The previous
    // code reset failedLoginCount to 0 and cleared lockedUntil BEFORE the MFA
    // challenge was verified — so an attacker with the password but no TOTP
    // secret could trigger unlimited MFA challenges without ever being locked
    // out. recordSuccessfulLogin is now called in /api/auth/2fa/login-verify
    // ONLY after the TOTP code verifies.
    const mfaToken = signMfaChallengeToken({
      userId: user.id,
      email: user.email,
    });
    // FE-016: set the challenge token as an HttpOnly cookie so XSS cannot
    // read it from the response body. The client SHOULD send the cookie back
    // to /api/auth/2fa/login-verify; the JSON mfaToken is kept for backward
    // compat with non-browser API clients.
    const { cookies: loginCookies } = await import("next/headers");
    const loginStore = await loginCookies();
    // BE-077 ROOT FIX: The previous cookie path was "/api/auth/2fa/login-verify"
    // (the exact endpoint path). This meant the cookie was ONLY sent on requests
    // to that exact path. If the verify endpoint is ever moved (e.g., to
    // "/api/auth/2fa/verify"), the cookie breaks silently. The broader path
    // "/api/auth/2fa" covers ALL 2FA-related endpoints under that prefix,
    // making the MFA flow resilient to endpoint reorganization.
    loginStore.set("drugos_mfa_challenge", mfaToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/api/auth/2fa",
      maxAge: 5 * 60, // 5 minutes — matches MFA_CHALLENGE_TTL_SECONDS
    });
    await writeAuditLog({
      user: { userId: user.id, email: user.email, role: user.role },
      action: "login_mfa_challenge_issued",
      resource: `user:${user.id}`,
      critical: true,
    });
    return NextResponse.json({
      mfaRequired: true,
      mfaToken,
      message: "Multi-factor authentication required. POST this token (or rely on the drugos_mfa_challenge cookie) and your 6-digit TOTP code to /api/auth/2fa/login-verify.",
    });
  }

  // Find the user's primary organization (first membership).
  const membership = await db.organizationMember.findFirst({
    where: { userId: user.id },
    orderBy: { joinedAt: "asc" },
  });

  // BE-079 REAL ROOT FIX (v2): Persist lastActiveOrgId on the User row.
  // The prior code only put orgId in the access-token JWT — it never
  // persisted it to the DB. When rotateRefreshToken fired (15 min later),
  // it had no orgId to put in the refreshed access token, so the user
  // lost their org context. By persisting lastActiveOrgId here, the
  // refresh path can read it and keep the orgId stable across token
  // rotations. We update unconditionally on login (even if the value is
  // the same) so a user whose membership was REMOVED from the previous
  // active org and re-added to a different one gets the correct active
  // org after re-login. The BE-062 org-membership check in
  // getAuthenticatedUser guards against stale orgIds in still-valid tokens.
  if (membership?.organizationId) {
    await db.user.update({
      where: { id: user.id },
      data: { lastActiveOrgId: membership.organizationId },
    });
  }

  // FE-009: Reset failed counter on successful login.
  // FE-056: recordIpAttempt was already called up-front at line 49.
  await recordSuccessfulLogin(user.id);

  const tokens = await rotateRefreshToken(user.id);
  const access = signAccessToken({
    userId: user.id,
    email: user.email,
    role: user.role,
    orgId: membership?.organizationId,
  });
  await setAuthCookies(access, tokens.refresh);
  // FE-011: issue the CSRF token cookie on successful login. The browser
  // client reads this cookie and copies its value into the X-CSRF-Token
  // header on every state-changing request.
  const csrfToken = issueCsrfToken();
  await setCsrfCookie(csrfToken);
  // FE-034: login success is security-critical — must be auditable.
  const loginAudit = await writeAuditLog({
    user: { userId: user.id, email: user.email, role: user.role, orgId: membership?.organizationId },
    action: "login",
    resource: `user:${user.id}`,
    critical: true,
  });
  if (!loginAudit.ok) {
    // We've already set the auth cookies, but we MUST tell the client
    // the login is not considered complete because the audit log failed.
    // Clear the cookies and return 500.
    await clearAuthCookies();
    return internalError("Failed to record login in audit log.");
  }

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
}
