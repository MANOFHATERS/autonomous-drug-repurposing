/**
 * Auth utilities: password hashing (bcrypt), JWT issuing/verification,
 * session helpers for Next.js route handlers, API-key authentication,
 * and a small in-memory login rate limiter (FE-009).
 *
 * ROOT-CAUSE FIXES (FE-008 / FE-024 / FE-025 / FE-033 / FE-036):
 *  - JWT_SECRET is no longer a hardcoded fallback. If unset or <32 chars,
 *    every call site throws a clear configuration error. (FE-008, FE-024)
 *  - Refresh cookie is now SameSite=Strict and scoped to path="/". (FE-025, FE-033)
 *  - authenticateApiKey is now reachable via getAuthenticatedUser(), which
 *    checks the Authorization: Bearer drugos_... header first and falls back
 *    to cookie auth. The developer platform now actually works. (FE-036)
 *
 * Security choices:
 *  - bcrypt with cost factor 12 (OWASP-recommended minimum as of 2024).
 *  - Access tokens are short-lived (15 min) JWTs signed with HS256.
 *  - Refresh tokens are opaque random strings persisted in the DB so they
 *    can be revoked.
 *  - Passwords MUST meet a minimum complexity policy before hashing.
 *  - A simple in-memory rate limiter caps login attempts per email+IP to
 *    mitigate brute-force attacks. (FE-009)
 */

import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { randomBytes } from "crypto";
import { cookies, headers } from "next/headers";
import { db } from "@/lib/db";

// ---------------------------------------------------------------------------
// JWT secret — ROOT FIX for FE-008 / FE-024
// ---------------------------------------------------------------------------
// Previously: `process.env.JWT_SECRET || "dev-only-insecure-secret-change-me"`.
// That fallback silently signed production JWTs with a 35-byte ASCII string
// checked into git. Anyone reading the repo could forge valid JWTs.
//
// ROOT FIX: we read the env var ONCE at module load. If it is missing or
// shorter than 32 bytes we throw immediately — every code path that signs
// or verifies a JWT will fail loudly rather than silently degrade. The
// error message tells the operator exactly what to fix.
//
// In test mode we allow a deterministic test secret so unit tests can run
// without forcing operators to set JWT_SECRET in CI just for `npm test`.
const isTestMode = process.env.NODE_ENV === "test";

function resolveJwtSecret(): string {
  const raw = process.env.JWT_SECRET;
  if (!raw) {
    if (isTestMode) {
      // Deterministic 64-byte test secret. Never used in production because
      // NODE_ENV !== "test" in real deployments.
      return "test-only-secret-do-not-use-in-production-32-bytes-minimum-aaaa";
    }
    throw new Error(
      "FATAL: JWT_SECRET environment variable is not set. Refusing to sign " +
      "or verify any JWT. Set JWT_SECRET to a cryptographically random " +
      "string of at least 32 bytes (e.g. `openssl rand -base64 48`)."
    );
  }
  if (raw.length < 32 && !isTestMode) {
    throw new Error(
      `FATAL: JWT_SECRET is only ${raw.length} bytes — minimum is 32. ` +
      "Refusing to sign or verify any JWT. Generate a stronger secret with " +
      "`openssl rand -base64 48`."
    );
  }
  return raw;
}

const JWT_SECRET = resolveJwtSecret();
const JWT_ISSUER = "drugos";
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
  // Pragmatic RFC-5322-ish check; we do not need to be perfect — we send a
  // verification email for real accounts.
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
// Login rate limiter — ROOT FIX for FE-009
// ---------------------------------------------------------------------------
// Previously: login had no rate limiting and no account lockout. An attacker
// could hammer /api/auth/login with arbitrary passwords at line speed.
//
// ROOT FIX: a process-local in-memory limiter tracks the last N failed
// attempts per (email, ip) tuple. After 5 failed attempts in 15 minutes the
// tuple is locked out for 15 minutes. Successful logins clear the counter.
//
// This is intentionally simple — for a multi-instance deployment you would
// move this to Redis. But it is a real, working rate limiter, not a stub.
//
// It is exported so the login route handler can call `checkLoginRate` before
// doing any DB work and `recordLoginFailure` / `recordLoginSuccess` after.

interface RateBucket {
  failures: number;
  firstFailureAt: number;
  lockedUntil: number;
}

const LOGIN_RATE_WINDOW_MS = 15 * 60 * 1000; // 15 minutes
const LOGIN_RATE_MAX_FAILURES = 5;
const LOGIN_RATE_LOCKOUT_MS = 15 * 60 * 1000; // 15 minutes
const loginBuckets = new Map<string, RateBucket>();

// Periodic cleanup so the map does not grow unboundedly. We do not use a
// setInterval because Next.js route handlers may be invoked in serverless
// contexts where the module is reloaded frequently — instead we clean
// opportunistically on every check.
function rateLimitKey(email: string, ip: string): string {
  return `${email.toLowerCase()}|${ip}`;
}

export interface LoginRateResult {
  allowed: boolean;
  retryAfterSeconds: number;
  remaining: number;
}

export function checkLoginRate(email: string, ip: string): LoginRateResult {
  const now = Date.now();
  // Opportunistic GC: drop expired entries every ~1000 calls.
  if (loginBuckets.size > 1000) {
    for (const [k, v] of loginBuckets) {
      if (now - v.firstFailureAt > LOGIN_RATE_WINDOW_MS && now > v.lockedUntil) {
        loginBuckets.delete(k);
      }
    }
  }
  const key = rateLimitKey(email, ip);
  const bucket = loginBuckets.get(key);
  if (!bucket) {
    return { allowed: true, retryAfterSeconds: 0, remaining: LOGIN_RATE_MAX_FAILURES };
  }
  if (now < bucket.lockedUntil) {
    return {
      allowed: false,
      retryAfterSeconds: Math.ceil((bucket.lockedUntil - now) / 1000),
      remaining: 0,
    };
  }
  // Window expired — reset.
  if (now - bucket.firstFailureAt > LOGIN_RATE_WINDOW_MS) {
    loginBuckets.delete(key);
    return { allowed: true, retryAfterSeconds: 0, remaining: LOGIN_RATE_MAX_FAILURES };
  }
  return {
    allowed: true,
    retryAfterSeconds: 0,
    remaining: Math.max(0, LOGIN_RATE_MAX_FAILURES - bucket.failures),
  };
}

export function recordLoginFailure(email: string, ip: string): void {
  const now = Date.now();
  const key = rateLimitKey(email, ip);
  const bucket = loginBuckets.get(key) ?? {
    failures: 0,
    firstFailureAt: now,
    lockedUntil: 0,
  };
  bucket.failures += 1;
  if (bucket.failures >= LOGIN_RATE_MAX_FAILURES) {
    bucket.lockedUntil = now + LOGIN_RATE_LOCKOUT_MS;
  }
  loginBuckets.set(key, bucket);
}

export function recordLoginSuccess(email: string, ip: string): void {
  loginBuckets.delete(rateLimitKey(email, ip));
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
  return jwt.sign(jwtPayload, JWT_SECRET, {
    issuer: JWT_ISSUER,
    expiresIn: ACCESS_TOKEN_TTL_SECONDS,
    algorithm: "HS256",
  });
}

export function verifyAccessToken(token: string): AuthenticatedUser | null {
  try {
    const decoded = jwt.verify(token, JWT_SECRET, {
      issuer: JWT_ISSUER,
      algorithms: ["HS256"],
    }) as AccessTokenPayload;
    if (!decoded || decoded.type !== "access" || !decoded.sub) return null;
    return {
      userId: decoded.sub,
      email: decoded.email,
      role: decoded.role,
      orgId: decoded.orgId,
    };
  } catch {
    return null;
  }
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
  const { token, expiresAt } = issueRefreshToken();
  await db.refreshToken.create({ data: { userId, token, expiresAt } });
  const user = await db.user.findUnique({ where: { id: userId } });
  if (!user) throw new Error("User not found while rotating refresh token");
  const access = signAccessToken({ userId: user.id, email: user.email, role: user.role });
  return { refresh: token, access };
}

export async function consumeRefreshToken(token: string): Promise<{ refresh: string; access: string } | null> {
  const record = await db.refreshToken.findUnique({ where: { token } });
  if (!record) return null;
  if (record.revokedAt) return null;
  if (record.expiresAt.getTime() < Date.now()) return null;
  await db.refreshToken.update({ where: { id: record.id }, data: { revokedAt: new Date() } });
  return rotateRefreshToken(record.userId);
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
// FE-025 / FE-033 ROOT FIX:
//  - Refresh cookie is now `sameSite: "strict"` so cross-site GETs can no
//    longer rotate tokens via /api/auth/refresh.
//  - Refresh cookie `path` is now "/" so it is sent on every route —
//    getAuthenticatedUser() can transparently auto-refresh expired access
//    tokens from any handler, not just /api/auth/refresh. (The previous
//    `path: "/api/auth/refresh"` made the auto-refresh fallback dead code
//    on every other route and forced users to re-login every 15 minutes.)

export const ACCESS_COOKIE = "drugos_access";
export const REFRESH_COOKIE = "drugos_refresh";
// CSRF token cookie — double-submit cookie pattern. The browser sends this
// on every same-site request; for state-changing endpoints we compare the
// cookie value to the X-CSRF-Token header. Cross-site attackers cannot
// read the cookie value (SameSite=strict) and therefore cannot forge the
// header. (FE-025)
export const CSRF_COOKIE = "drugos_csrf";

export function issueCsrfToken(): string {
  return randomBytes(32).toString("hex");
}

export async function setAuthCookies(access: string, refresh: string): Promise<string> {
  const store = await cookies();
  const isProd = process.env.NODE_ENV === "production";
  store.set(ACCESS_COOKIE, access, {
    httpOnly: true,
    secure: isProd,
    sameSite: "lax", // access cookie must be readable on top-level GETs
    path: "/",
    maxAge: ACCESS_TOKEN_TTL_SECONDS,
  });
  store.set(REFRESH_COOKIE, refresh, {
    httpOnly: true,
    secure: isProd,
    sameSite: "strict", // FE-025: strict so cross-site GETs can't rotate
    path: "/",           // FE-033: every route can auto-refresh
    maxAge: REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
  });
  // Issue a fresh CSRF token with every auth cookie rotation. The browser
  // will echo this back on every same-site request; the client reads it
  // via document.cookie (it is NOT httpOnly) and includes it in the
  // X-CSRF-Token header for state-changing requests.
  const csrf = issueCsrfToken();
  store.set(CSRF_COOKIE, csrf, {
    httpOnly: false, // client must read this to echo it in the header
    secure: isProd,
    sameSite: "strict",
    path: "/",
    maxAge: REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
  });
  return csrf;
}

export async function clearAuthCookies(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
  store.delete(CSRF_COOKIE);
}

/**
 * Verify the double-submit CSRF token for state-changing requests.
 * Returns true if the X-CSRF-Token header matches the drugos_csrf cookie.
 *
 * Call this from POST/PUT/PATCH/DELETE handlers. (FE-025)
 */
export async function verifyCsrfToken(): Promise<boolean> {
  const store = await cookies();
  const cookieToken = store.get(CSRF_COOKIE)?.value;
  if (!cookieToken) return false;
  const h = await headers();
  const headerToken = h.get("x-csrf-token");
  if (!headerToken) return false;
  // Constant-time-ish comparison.
  if (cookieToken.length !== headerToken.length) return false;
  let diff = 0;
  for (let i = 0; i < cookieToken.length; i++) {
    diff |= cookieToken.charCodeAt(i) ^ headerToken.charCodeAt(i);
  }
  return diff === 0;
}

// ---------------------------------------------------------------------------
// API key auth (for the developer platform / programmatic access)
// ---------------------------------------------------------------------------

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
  await db.apiKey.update({ where: { id: key.id }, data: { lastUsedAt: new Date() } });
  return {
    userId: key.user.id,
    email: key.user.email,
    role: key.user.role,
    orgId: key.organizationId,
  };
}

// ---------------------------------------------------------------------------
// Combined auth: Authorization Bearer header (API key OR bearer JWT) first,
// fall back to session cookies. — ROOT FIX for FE-036.
// ---------------------------------------------------------------------------

/**
 * Reads the Authorization header. If it is `Bearer drugos_...`, we treat
 * it as an API key and look it up. If it is `Bearer <jwt>`, we verify the
 * JWT. Otherwise (no Authorization header) we fall back to the session
 * cookie flow.
 *
 * This is what makes the developer platform actually work: pharma partners
 * can now call /api/... programmatically with `Authorization: Bearer
 * drugos_<key>` and the same auth context is populated as if they had
 * logged in via the web UI. (FE-036)
 */
export async function getAuthenticatedUser(): Promise<AuthenticatedUser | null> {
  // 1. Authorization header — supports both API keys and raw JWTs.
  const h = await headers();
  const authHeader = h.get("authorization");
  if (authHeader && authHeader.toLowerCase().startsWith("bearer ")) {
    const token = authHeader.slice(7).trim();
    if (token.startsWith("drugos_")) {
      // API key path.
      const user = await authenticateApiKey(token);
      if (user) return user;
    } else {
      // JWT path — allows programmatic clients that prefer to ship a
      // short-lived access token instead of an API key.
      const user = verifyAccessToken(token);
      if (user) return user;
    }
  }

  // 2. Cookie session.
  const store = await cookies();
  const access = store.get(ACCESS_COOKIE)?.value;
  if (access) {
    const user = verifyAccessToken(access);
    if (user) return user;
  }
  // Try refresh — auto-rotates expired access tokens transparently.
  const refresh = store.get(REFRESH_COOKIE)?.value;
  if (!refresh) return null;
  const refreshed = await consumeRefreshToken(refresh);
  if (!refreshed) return null;
  await setAuthCookies(refreshed.access, refreshed.refresh);
  return verifyAccessToken(refreshed.access);
}

// ---------------------------------------------------------------------------
// Authorization helpers — ROOT FIX for FE-010
// ---------------------------------------------------------------------------
// Previously: `requireRole` was defined here but NEVER called anywhere in
// the codebase. Every privileged route used `requireAuth` only, so a viewer
// could call /api/api-keys, /api/billing/subscription, etc.
//
// ROOT FIX: requireRole is now the canonical role check. The companion
// helper `requireRoleOrSend` (in api-helpers.ts) wraps it so route handlers
// can do `if (!requireRoleOrSend(auth, "admin", "owner")) return;` in one
// line. The routes that need role checks have all been updated.

export function requireRole(user: AuthenticatedUser | null, ...roles: string[]): boolean {
  if (!user) return false;
  if (roles.length === 0) return true;
  return roles.includes(user.role);
}
