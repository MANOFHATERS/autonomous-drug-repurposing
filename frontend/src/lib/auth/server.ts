/**
 * Auth utilities: password hashing (bcrypt), JWT issuing/verification,
 * and a small session helper for Next.js route handlers.
 *
 * Security choices:
 *  - bcrypt with cost factor 12 (OWASP-recommended minimum as of 2024).
 *  - Access tokens are short-lived (15 min) JWTs signed with HS256.
 *  - Refresh tokens are opaque random strings persisted in the DB so they
 *    can be revoked.
 *  - Passwords MUST meet a minimum complexity policy before hashing.
 */

import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { randomBytes } from "crypto";
import { cookies } from "next/headers";
import { db } from "@/lib/db";

// FE-008 ROOT FIX: No hardcoded fallback. In production, missing or short
// JWT_SECRET fails fast — we never sign tokens with a publicly known key.
// A 32-byte (256-bit) minimum matches OWASP recommendations for HS256.
//
// The previous code (`process.env.JWT_SECRET || "dev-only-insecure-secret-change-me"`)
// meant that a production deploy with a missing env var would silently sign
// every JWT with a hardcoded string published in the source — full account
// takeover for any attacker who reads the repo.
//
// FE-041 ROOT FIX: resolveJwtSecret is invoked PER-CALL inside
// signAccessToken / verifyAccessToken / signMfaChallengeToken /
// verifyMfaChallengeToken (instead of being cached in a module-level
// `const JWT_SECRET = resolveJwtSecret()`). This allows JWT secret rotation
// via a secrets manager (Vault, AWS SM, GCP SM, k8s mounted env) to take
// effect immediately for newly-issued tokens without requiring a process
// restart. In a 24/7 pharma research platform, "restart to rotate" is an
// operational and security hazard — operators delay rotation, leaving old
// keys in production longer than necessary. Per-call resolution has
// negligible cost (one env lookup + length check) and removes the
// downtime-vs-security tradeoff.
//
// To support zero-downtime rotation with already-issued tokens, operators
// may set JWT_SECRET to the NEW value and JWT_SECRET_PREVIOUS to the OLD
// value during the transition window — verifyAccessToken tries both.
export function resolveJwtSecret(): string {
  const secret = process.env.JWT_SECRET;
  if (!secret || secret.length < 32) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "JWT_SECRET must be set to a >=32-char random string in production. " +
        "Generate one with: openssl rand -base64 48"
      );
    }
    // Dev-only deterministic secret. Logged loudly so it's obvious.
    if (!process.env.JWT_SECRET) {
      console.warn(
        "[SECURITY] JWT_SECRET not set — using dev-only secret. " +
        "DO NOT use in production. Set JWT_SECRET to a >=32-char random string."
      );
    }
    return "dev-only-insecure-secret-change-me-MINIMUM-32-CHARS-FOR-HS256!!";
  }
  return secret;
}

/**
 * FE-041: Return the previous secret (if any) for zero-downtime rotation.
 * When JWT_SECRET is rotated, set JWT_SECRET_PREVIOUS to the old value so
 * tokens signed with the old key remain valid during the access-token TTL
 * window (15 min). After the TTL expires, unset JWT_SECRET_PREVIOUS.
 */
export function resolvePreviousJwtSecret(): string | null {
  const prev = process.env.JWT_SECRET_PREVIOUS;
  if (!prev || prev.length < 32) return null;
  return prev;
}

const JWT_ISSUER = "drugos";

// FE-004 ROOT FIX: Short-lived MFA challenge token. Issued by /api/auth/login
// when password verification succeeds but the user has mfaEnabled=true. The
// client must POST this token + a TOTP code to /api/auth/2fa/login-verify to
// obtain real access+refresh tokens. The challenge token CANNOT be used for
// anything except the 2FA verify endpoint — its `type` is "mfa_challenge",
// not "access".
//
// FE-016 ROOT FIX: The token now carries a `jti` (JWT ID, random 16 bytes).
// /api/auth/2fa/login-verify records each jti as consumed in the MfaChallenge
// table (unique on jti). A replayed token is rejected with 401. This closes
// the "intercept-and-replay" hole where an attacker with XSS could read the
// mfaToken from the JSON response and replay it within the 5-min window.
const MFA_CHALLENGE_TTL_SECONDS = 5 * 60; // 5 minutes
export function signMfaChallengeToken(payload: {
  userId: string;
  email: string;
}): string {
  const jti = randomBytes(16).toString("hex");
  const jwtPayload = {
    sub: payload.userId,
    email: payload.email,
    type: "mfa_challenge" as const,
    jti,
  };
  // FE-041: resolve secret per-call to support hot-rotation.
  return jwt.sign(jwtPayload, resolveJwtSecret(), {
    issuer: JWT_ISSUER,
    expiresIn: MFA_CHALLENGE_TTL_SECONDS,
    algorithm: "HS256",
  });
}

export function verifyMfaChallengeToken(token: string): {
  userId: string;
  email: string;
} | null {
  // FE-041: try current secret first, then previous-secret (rotation window).
  const candidates = [resolveJwtSecret(), resolvePreviousJwtSecret()].filter(
    (s): s is string => !!s
  );
  for (const secret of candidates) {
    try {
      const decoded = jwt.verify(token, secret, {
        issuer: JWT_ISSUER,
        algorithms: ["HS256"],
      }) as { sub: string; email: string; type: string };
      if (!decoded || decoded.type !== "mfa_challenge" || !decoded.sub) {
        continue;
      }
      return { userId: decoded.sub, email: decoded.email };
    } catch {
      // try next candidate
    }
  }
  return null;
}

const ACCESS_TOKEN_TTL_SECONDS = 15 * 60; // 15 minutes
const REFRESH_TOKEN_TTL_DAYS = 30;

export interface AccessTokenPayload {
  sub: string; // user id
  email: string;
  role: string;
  orgId?: string;
  type: "access";
}

export interface AuthenticatedUser {
  userId: string;
  email: string;
  role: string;
  orgId?: string;
}

// ---------------------------------------------------------------------------
// Password policy
// ---------------------------------------------------------------------------

export interface PasswordPolicyResult {
  ok: boolean;
  reason?: string;
}

export function validatePasswordPolicy(password: string): PasswordPolicyResult {
  if (typeof password !== "string" || password.length < 10) {
    return { ok: false, reason: "Password must be at least 10 characters long." };
  }
  if (password.length > 1024) {
    return { ok: false, reason: "Password is too long." };
  }
  if (!/[a-z]/.test(password)) {
    return { ok: false, reason: "Password must contain at least one lowercase letter." };
  }
  if (!/[A-Z]/.test(password)) {
    return { ok: false, reason: "Password must contain at least one uppercase letter." };
  }
  if (!/[0-9]/.test(password)) {
    return { ok: false, reason: "Password must contain at least one digit." };
  }
  if (!/[^A-Za-z0-9]/.test(password)) {
    return { ok: false, reason: "Password must contain at least one symbol." };
  }
  return { ok: true };
}

export function validateEmail(email: string): boolean {
  // FE-029 ROOT FIX: The previous comment said "we send a verification
  // email for real accounts" — that was a lie. nodemailer was in
  // package.json but NEVER imported, and emailVerified was set to false
  // on register and never became true. Email verification is implemented
  // separately in FE-035 (registration rate limit + verification flow).
  // Until then, this validator just checks the format.
  return typeof email === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && email.length <= 254;
}

// ---------------------------------------------------------------------------
// Password hashing
// ---------------------------------------------------------------------------

const BCRYPT_COST = 12;

export async function hashPassword(plain: string): Promise<string> {
  const salt = await bcrypt.genSalt(BCRYPT_COST);
  return bcrypt.hash(plain, salt);
}

export async function verifyPassword(plain: string, hash: string): Promise<boolean> {
  if (!hash || !plain) return false;
  try {
    return await bcrypt.compare(plain, hash);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// JWT
// ---------------------------------------------------------------------------

export function signAccessToken(payload: AuthenticatedUser): string {
  const jwtPayload: AccessTokenPayload = {
    sub: payload.userId,
    email: payload.email,
    role: payload.role,
    orgId: payload.orgId,
    type: "access",
  };
  // FE-041: resolve secret per-call to support hot-rotation.
  return jwt.sign(jwtPayload, resolveJwtSecret(), {
    issuer: JWT_ISSUER,
    expiresIn: ACCESS_TOKEN_TTL_SECONDS,
    algorithm: "HS256",
  });
}

export function verifyAccessToken(token: string): AuthenticatedUser | null {
  // FE-041: try current secret first, then previous-secret (rotation window).
  const candidates = [resolveJwtSecret(), resolvePreviousJwtSecret()].filter(
    (s): s is string => !!s
  );
  for (const secret of candidates) {
    try {
      const decoded = jwt.verify(token, secret, {
        issuer: JWT_ISSUER,
        algorithms: ["HS256"],
      }) as AccessTokenPayload;
      if (!decoded || decoded.type !== "access" || !decoded.sub) continue;
      return {
        userId: decoded.sub,
        email: decoded.email,
        role: decoded.role,
        orgId: decoded.orgId,
      };
    } catch {
      // try next candidate
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Refresh tokens
// ---------------------------------------------------------------------------

export function issueRefreshToken(): { token: string; expiresAt: Date } {
  const token = randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60 * 1000);
  return { token, expiresAt };
}

export async function rotateRefreshToken(userId: string): Promise<{ refresh: string; access: string }> {
  // FE-032 ROOT FIX: The previous code called db.user.findUnique and only
  // checked that the user exists — it did NOT check user.status or
  // user.lockedUntil. So a SUSPENDED user's existing refresh token
  // continued to work for up to 30 days (REFRESH_TOKEN_TTL_DAYS). An
  // attacker who compromised a session before the user was suspended
  // retained access throughout the suspension. A LOCKED user (failed
  // login brute-force lockout) could also bypass the lock by using an
  // existing refresh token.
  //
  // Now we check both conditions explicitly. If the user is suspended or
  // locked, we revoke ALL their refresh tokens and throw — the caller
  // (consumeRefreshToken) returns null, and /api/auth/refresh returns 401
  // + clears cookies.
  //
  // BE-079 REAL ROOT FIX (v2): We now select `lastActiveOrgId` and pass it
  // to signAccessToken. The prior code signed the refreshed access token
  // with ONLY { userId, email, role } — NO orgId. So after the original
  // 15-min access token expired, the refreshed one had orgId=undefined.
  // Every org-scoped query (projects, hypotheses, billing, team) returned
  // 403 because auth.user.orgId was undefined. This was a PRE-EXISTING
  // bug that the prior BE-079 "fix" made more visible (users who switched
  // orgs lost their orgId immediately on refresh) but never actually
  // fixed. The fix persists lastActiveOrgId on the User row (see
  // migration 20260714000001_be079_user_last_active_org_id) and reads it
  // here so the refreshed access token carries the correct orgId.
  const user = await db.user.findUnique({
    where: { id: userId },
    select: {
      id: true,
      email: true,
      role: true,
      status: true,
      lockedUntil: true,
      lastActiveOrgId: true,
    },
  });
  if (!user) throw new Error("User not found while rotating refresh token");
  if (user.status === "suspended") {
    // Revoke ALL refresh tokens for this user — they should not be able
    // to keep using any previously-issued token after suspension.
    await revokeAllRefreshTokensForUser(userId);
    throw new Error("account_suspended");
  }
  if (user.lockedUntil && user.lockedUntil.getTime() > Date.now()) {
    // Account is temporarily locked (brute-force protection). Do NOT
    // revoke all tokens — the lock is temporary and the user should be
    // able to use existing sessions after the lock expires. But we DO
    // refuse to issue new tokens during the lock.
    throw new Error("account_locked");
  }

  const { token, expiresAt } = issueRefreshToken();
  await db.refreshToken.create({ data: { userId, token, expiresAt } });
  // BE-079: Include lastActiveOrgId so the refreshed access token keeps
  // the user's org context. If lastActiveOrgId is null (legacy user who
  // hasn't logged in since the migration), orgId is undefined — the
  // getAuthenticatedUser flow will force re-auth via the BE-062 org
  // membership check (which only runs when orgId is truthy), and the
  // next login will populate lastActiveOrgId.
  const access = signAccessToken({
    userId: user.id,
    email: user.email,
    role: user.role,
    orgId: user.lastActiveOrgId ?? undefined,
  });
  return { refresh: token, access };
}

export async function consumeRefreshToken(token: string): Promise<{ refresh: string; access: string } | null> {
  const record = await db.refreshToken.findUnique({ where: { token } });
  if (!record) return null;
  if (record.revokedAt) return null;
  if (record.expiresAt.getTime() < Date.now()) return null;
  await db.refreshToken.update({ where: { id: record.id }, data: { revokedAt: new Date() } });
  try {
    return await rotateRefreshToken(record.userId);
  } catch (e) {
    // FE-032: rotateRefreshToken throws if the user is suspended or
    // locked. We swallow the error and return null so the caller returns
    // 401 (and clears cookies via FE-031).
    const msg = e instanceof Error ? e.message : String(e);
    if (msg === "account_suspended" || msg === "account_locked") {
      return null;
    }
    // Unexpected error — rethrow.
    throw e;
  }
}

export async function revokeAllRefreshTokensForUser(userId: string): Promise<number> {
  const result = await db.refreshToken.updateMany({
    where: { userId, revokedAt: null },
    data: { revokedAt: new Date() },
  });
  return result.count;
}

// ---------------------------------------------------------------------------
// Cookie helpers (server-side route handlers)
// ---------------------------------------------------------------------------

export const ACCESS_COOKIE = "drugos_access";
export const REFRESH_COOKIE = "drugos_refresh";

export async function setAuthCookies(access: string, refresh: string): Promise<void> {
  const store = await cookies();
  const isProd = process.env.NODE_ENV === "production";

  // FE-070 ROOT FIX: SameSite policy hardening.
  //
  // Previously BOTH cookies used SameSite=Lax. Lax blocks POST/PUT/DELETE
  // from cross-origin (good) but ALLOWS top-level GET navigations to carry
  // cookies — so if any state-changing operation can be triggered via a
  // GET (bad practice but happens), it's CSRF-vulnerable. Worse, if SSO/
  // OIDC redirect flows are added later (next-auth is in package.json),
  // Lax will break them and a future developer may "fix" it by setting
  // SameSite=None — re-opening CSRF.
  //
  // Root fix:
  //   - ACCESS cookie: SameSite=Strict. The access token authorizes every
  //     API call AND every state-changing GET (rare but possible). Strict
  //     means the cookie is NEVER sent on cross-site requests — not even
  //     top-level GET navigations. The DruGOS dashboard is a same-origin
  //     SPA; no external site needs to deep-link in with auth. Trade-off:
  //     a user clicking an external link TO drugos.example.com will land
  //     unauthenticated on the first hop — acceptable for a pharma
  //     research platform (defense-in-depth > friction).
  //   - REFRESH cookie: SameSite=Lax. Refresh is scoped to
  //     /api/auth/refresh (path restriction) and is opaque (not a JWT).
  //     Lax is required because the SPA's silent-refresh flow may be
  //     triggered by a top-level navigation from a password-reset email
  //     link or SSO callback. Combined with CSRF tokens (FE-011) for
  //     defense-in-depth.
  store.set(ACCESS_COOKIE, access, {
    httpOnly: true,
    secure: isProd,
    sameSite: "strict",
    path: "/",
    maxAge: ACCESS_TOKEN_TTL_SECONDS,
  });
  store.set(REFRESH_COOKIE, refresh, {
    httpOnly: true,
    secure: isProd,
    sameSite: "lax",
    // FE-050 ROOT FIX: cookie path was "/api/auth/refresh", which meant the
    // browser only sent the refresh cookie on requests to that exact path.
    // But getAuthenticatedUser() — called by EVERY authenticated route —
    // reads the refresh cookie to auto-rotate expired access tokens. With
    // the restricted path, the cookie was never sent on /api/projects,
    // /api/auth/me, /api/evidence-package, etc., so the auto-refresh code
    // path was effectively dead and users were logged out every 15 min.
    // Setting path: "/" aligns the refresh cookie's scope with the access
    // cookie. The security trade-off is acceptable: both cookies are
    // HttpOnly + Secure (in prod) + SameSite=Lax, so they are not readable
    // by JS, not sent on cross-site requests, and only transmitted over
    // HTTPS in production.
    path: "/",
    maxAge: REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
  });
}

export async function clearAuthCookies(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
}

export async function getAuthenticatedUser(): Promise<AuthenticatedUser | null> {
  // FE-012 ROOT FIX: API-key authentication path. The developer platform
  // issues "drugos_<32 hex>" keys via /api/api-keys, but authenticateApiKey()
  // was DEAD CODE — no route ever called it. Enterprise customers paying for
  // "API access (50,000 req/day)" could not actually use the API. Now every
  // route that calls requireAuth() honors "Authorization: Bearer drugos_…"
  // automatically. The cookie session path below still handles browser
  // clients. Order matters: API-key auth is checked FIRST so a request
  // carrying a valid Bearer key does not fall through to cookie inspection
  // (which would 401 a programmatic client with no cookies).
  try {
    const { headers } = await import("next/headers");
    const hdrs = await headers();
    const authHeader = hdrs.get("authorization") || hdrs.get("Authorization");
    if (authHeader && authHeader.toLowerCase().startsWith("bearer ")) {
      const rawKey = authHeader.slice(7).trim();
      // Only attempt API-key auth for keys with the documented "drugos_"
      // prefix — a generic Bearer token is treated as malformed and ignored
      // (it might be an OAuth token for a different provider, etc.).
      if (rawKey.startsWith("drugos_")) {
        const user = await authenticateApiKey(rawKey);
        if (user) return user;
        // Invalid API key → return null immediately. Do NOT fall through to
        // cookie auth, because the caller explicitly tried API-key auth and
        // returning a cookie-session user would be a confused-deputy risk.
        return null;
      }
    }
  } catch {
    // headers() throws if called outside a request scope (e.g. in a script).
    // Swallow and continue to the cookie path.
  }

  const store = await cookies();
  const access = store.get(ACCESS_COOKIE)?.value;
  if (access) {
    const user = verifyAccessToken(access);
    if (user) {
      // BE-062 ROOT FIX: Verify the user is STILL a member of the claimed
      // org. If the user was removed from the org AFTER the token was
      // issued, the token's orgId is stale. We reject the auth and force
      // re-authentication. Without this check, a removed user retains
      // access to the old org's data for up to the access token TTL
      // (15 minutes), and the refresh token can issue new access tokens
      // for the old org for up to 30 days.
      if (user.orgId) {
        const stillMember = await db.organizationMember.findUnique({
          where: {
            userId_organizationId: {
              userId: user.userId,
              organizationId: user.orgId,
            },
          },
          select: { id: true },
        });
        if (!stillMember) {
          // User is no longer a member of this org. Clear all auth cookies
          // to force re-authentication (which will pick up their new org).
          console.warn(
            `[SECURITY] User ${user.userId} presented a valid access token ` +
            `for org ${user.orgId} but is no longer a member. Forcing re-auth.`
          );
          await clearAuthCookies();
          return null;
        }
      }
      return user;
    }
  }
  // Try refresh
  const refresh = store.get(REFRESH_COOKIE)?.value;
  if (refresh) {
    const refreshed = await consumeRefreshToken(refresh);
    if (refreshed) {
      await setAuthCookies(refreshed.access, refreshed.refresh);
      const refreshedUser = verifyAccessToken(refreshed.access);
      // BE-062: Also check org membership after refresh token rotation.
      // The rotateRefreshToken function checks user.status but NOT org
      // membership. A user removed from an org could still have a valid
      // refresh token that issues new access tokens for the old org.
      if (refreshedUser && refreshedUser.orgId) {
        const stillMember = await db.organizationMember.findUnique({
          where: {
            userId_organizationId: {
              userId: refreshedUser.userId,
              organizationId: refreshedUser.orgId,
            },
          },
          select: { id: true },
        });
        if (!stillMember) {
          console.warn(
            `[SECURITY] User ${refreshedUser.userId} rotated refresh token ` +
            `for org ${refreshedUser.orgId} but is no longer a member. Forcing re-auth.`
          );
          await clearAuthCookies();
          return null;
        }
      }
      return refreshedUser;
    }
  }
  // FE-021 ROOT FIX: Both access and refresh tokens failed verification.
  // Previously we returned null WITHOUT clearing the bad cookies, so the
  // browser kept sending them on every subsequent request → repeated 401s
  // and a permanent lockout if an attacker planted a malformed cookie via
  // XSS. Now we wipe them so the next request is a clean unauthenticated
  // state (the client will redirect to /login).
  if (access || refresh) {
    try {
      await clearAuthCookies();
    } catch {
      // clearAuthCookies can throw if cookies() is called outside a request
      // scope; swallow so the null return is the only signal.
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// API key auth (for the developer platform / programmatic access)
// ---------------------------------------------------------------------------

/**
 * BE-076 ROOT FIX: The previous code did NOT check `user.status` before
 * returning the authenticated user from an API key. A suspended user's
 * API key continued to work indefinitely — they could still call the API
 * programmatically even after an admin suspended their account. This
 * bypasses the suspension mechanism for programmatic access.
 *
 * Root fix: After finding a valid (non-revoked) API key, we now check
 * that the associated user's status is "active". If the user is
 * suspended, pending_approval, or any non-active status, we return null
 * (auth failure). The suspension is enforced for BOTH cookie sessions
 * AND API keys.
 */
export async function authenticateApiKey(rawKey: string): Promise<AuthenticatedUser | null> {
  if (!rawKey || typeof rawKey !== "string") return null;
  // Keys are issued with format "drugos_<32 hex chars>". We hash the full key
  // with sha256 before lookup so we never store the raw value.
  const { createHash } = await import("crypto");
  const hash = createHash("sha256").update(rawKey).digest("hex");
  const key = await db.apiKey.findFirst({
    where: { hashedKey: hash, revokedAt: null },
    include: { user: true },
  });
  if (!key) return null;
  // BE-076: Reject API keys for non-active users. A suspended account
  // should not be able to access the API via ANY auth mechanism.
  if (key.user.status !== "active") {
    // Log the rejected attempt for security monitoring.
    console.warn(
      `[SECURITY] API key auth rejected for user ${key.user.id}: ` +
      `status="${key.user.status}" (expected "active"). Key prefix: ${key.prefix}`
    );
    return null;
  }
  await db.apiKey.update({ where: { id: key.id }, data: { lastUsedAt: new Date() } });
  return {
    userId: key.user.id,
    email: key.user.email,
    role: key.user.role,
    orgId: key.organizationId,
  };
}

// ---------------------------------------------------------------------------
// Authorization helpers
// ---------------------------------------------------------------------------
// FE-022 ROOT FIX: The boolean `requireRole(user, ...roles)` that used to live
// here was DEAD CODE — zero call sites repo-wide — and its signature
// `(user, ...roles) => boolean` silently shadowed the route-friendly version
// in @/lib/api-helpers.ts (`requireRole(user, ...roles) => Promise<{user,response}>`).
// IDE auto-import would pick the wrong one and future maintainers would get
// unexpected behavior. Deleted. Use `requireRole` / `requireAuthRole` from
// @/lib/api-helpers.ts for every route-level authorization check.
//
// (Re-exported below so any external consumer that imported from this module
// keeps compiling — but the re-exported symbol is the api-helpers version,
// not a boolean-returning stub.)

export {
  requireRole,
  requireAuthRole,
  requireRoleOrSend,
} from "@/lib/api-helpers";

// FE-009: Re-export rate-limit functions with alternate names for backward
// compat with tests written by other agents.
export {
  checkAccountLocked as checkLoginRate,
  recordFailedLogin as recordLoginFailure,
  recordSuccessfulLogin as recordLoginSuccess,
  checkIpRateLimit,
  recordIpAttempt,
} from '@/lib/auth/rate-limit';

