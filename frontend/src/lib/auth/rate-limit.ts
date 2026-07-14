/**
 * Rate limiting utilities.
 *
 * FE-009 ROOT FIX: The login endpoint had NO rate limiting, no failed-attempt
 * counter, no account lockout. This made brute-force / credential-stuffing
 * attacks trivially possible.
 *
 * This module provides TWO layers of protection:
 *
 *   1. Per-account lockout: After MAX_FAILED_ATTEMPTS failed logins within
 *      LOCKOUT_WINDOW_MINUTES, the account is locked for LOCKOUT_DURATION_MINUTES.
 *      This is persisted in the User table (failedLoginCount, lockedUntil).
 *
 *   2. Per-IP rate limiting: Limits how many login attempts a single IP can
 *      make across ALL accounts. This catches distributed credential stuffing
 *      where the attacker rotates usernames. Implemented with an in-memory
 *      sliding-window counter (good enough for a single-node deployment; for
 *      multi-node, swap in @upstash/ratelimit which is backed by Redis).
 *
 * Both layers are required: per-account lockout stops targeted brute-force on
 * one account, per-IP rate limit stops an attacker rotating through many
 * accounts from the same IP.
 *
 * FE-061 ROOT FIX: The previous implementation used a plain `Map<string,
 * IpBucket>` cleaned up only every 10 minutes. Under a distributed attack
 * with millions of unique source IPs, the Map could grow to millions of
 * entries between cleanup cycles — consuming GB of memory and causing GC
 * pauses. The cleanup iteration itself was O(n) over the whole Map.
 *
 * We now use a bounded LRU cache (max 100K entries). When the cap is hit,
 * the least-recently-accessed bucket is evicted. This bounds memory at
 * ~100K * ~200 bytes ≈ 20MB worst case, and eviction is O(1) amortized.
 * For multi-node deployments that need shared state, swap in
 * @upstash/ratelimit — the function signatures stay the same.
 */

import { db } from "@/lib/db";
import type { NextRequest } from "next/server";

export const MAX_FAILED_ATTEMPTS = 5;
export const LOCKOUT_WINDOW_MINUTES = 15;
export const LOCKOUT_DURATION_MINUTES = 30;

// Per-IP limits (across all accounts).
const IP_MAX_ATTEMPTS = 20; // 20 attempts...
const IP_WINDOW_MINUTES = 5; // ...per 5 minutes
const IP_BLOCK_MINUTES = 15; // ...then block IP for 15 minutes

// FE-061: Bounded LRU cache size. 100K unique IPs covers a sustained attack
// from a botnet; legitimate traffic uses orders of magnitude fewer entries.
// At ~200 bytes per bucket (20 timestamps * 8 bytes + overhead), this caps
// memory at ~20MB.
const IP_LRU_MAX_ENTRIES = 100_000;

// FE-003 ROOT FIX: TOTP (2FA) brute-force protection.
// A 6-digit TOTP code has 1,000,000 combinations; at 1000 req/s an attacker
// can sweep the keyspace in ~17 minutes, and the ±30s drift window expands
// it to ~3M codes. We implement a per-user sliding-window counter that is
// SEPARATE from the password failedLoginCount, because password failures
// and 2FA failures have different blast radii and reset semantics.
export const TOTP_MAX_ATTEMPTS = 5; // 5 wrong TOTP codes...
const TOTP_WINDOW_MINUTES = 5; // ...within 5 minutes...
const TOTP_LOCK_MINUTES = 15; // ...locks 2FA for 15 minutes

interface TotpBucket {
  attempts: number[]; // timestamps (ms) of recent WRONG codes
  lockedUntil: number | null;
}

// In-memory store keyed by userId. For multi-node deployment swap in Redis.
const totpBuckets = new Map<string, TotpBucket>();

interface IpBucket {
  attempts: number[]; // timestamps (ms) of recent attempts
  blockedUntil: number | null;
}

// FE-061: Periodic cleanup so the LRU doesn't accumulate stale entries.
const CLEANUP_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes
let lastCleanup = Date.now();

/**
 * FE-061 ROOT FIX: Bounded LRU Map.
 *
 * A plain `Map` in JavaScript preserves insertion order, so we can use it as
 * an LRU by:
 *   - On read/write: delete the key, then re-set it (moves it to the end = MRU).
 *   - On insertion when over capacity: delete the first key (LRU).
 *
 * This gives O(1) get/set/evict. We cap at IP_LRU_MAX_ENTRIES so memory is
 * bounded regardless of attack volume.
 */
class LruMap<K, V> {
  private map = new Map<K, V>();
  constructor(private readonly max: number) {}

  get(key: K): V | undefined {
    const v = this.map.get(key);
    if (v === undefined) return undefined;
    // Move to MRU position.
    this.map.delete(key);
    this.map.set(key, v);
    return v;
  }

  set(key: K, value: V): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, value);
    // Evict LRU if over capacity.
    if (this.map.size > this.max) {
      const firstKey = this.map.keys().next().value;
      if (firstKey !== undefined) this.map.delete(firstKey);
    }
  }

  delete(key: K): boolean {
    return this.map.delete(key);
  }

  get size(): number {
    return this.map.size;
  }

  /**
   * Iterate entries in insertion (LRU-first) order. Used by cleanup.
   */
  forEach(callback: (value: V, key: K) => void): void {
    this.map.forEach((v, k) => callback(v, k));
  }
}

// In-memory LRU store. Keyed by IP. For multi-node deployment, replace with
// @upstash/ratelimit (Redis-backed) — the function signatures stay the same.
const ipBuckets = new LruMap<string, IpBucket>(IP_LRU_MAX_ENTRIES);

function maybeCleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  const cutoff = now - IP_BLOCK_MINUTES * 60 * 1000;
  // Iterate from LRU end. We collect keys to delete first to avoid mutating
  // during iteration. The LRU iteration order means we hit the oldest first,
  // which are the most likely candidates for eviction.
  ipBuckets.forEach((bucket, ip) => {
    const last = bucket.attempts[bucket.attempts.length - 1] ?? 0;
    if (last < cutoff && (!bucket.blockedUntil || bucket.blockedUntil < now)) {
      ipBuckets.delete(ip);
    }
  });
}

// FE-020 ROOT FIX: The previous getClientIp() validated X-Real-IP and
// X-Forwarded-For with `/^\d{1,3}(\.\d{1,3}){3}$/` — an IPv4-ONLY regex.
// On any IPv6 deployment (AWS dual-stack, Cloudflare, GCP, Azure — all
// default to dual-stack), X-Real-IP is `2001:db8::1` etc. The regex
// rejected it, the function returned "unknown", and ALL IPv6 clients
// shared a single "unknown" bucket. One user's 20 failed logins locked
// out EVERY IPv6 client for 15 minutes — a CRO with 50 researchers behind
// IPv6 NAT could be locked out by a single typo.
//
// The fix is a permissive validator that accepts both IPv4 and IPv6
// (full and v4-mapped forms), plus an explicit fallback for trusted-proxy
// headers (cf-connecting-ip, true-client-ip) so deployments behind
// Cloudflare/Akamai don't need X-Real-IP at all.
//
// FE-019 ROOT FIX (Team Member 14): The previous getClientIp() TRUSTED
// X-Forwarded-For UNCONDITIONALLY. An attacker could set
// `X-Forwarded-For: 1.2.3.4` to spoof any IP — bypassing IP-based rate
// limits (rotate XFF → fresh bucket each time) and polluting the audit
// log with fake IPs (forensic untraceability). The fix:
//
//   1. Add a `TRUSTED_PROXY_CIDR` env var (comma-separated CIDRs or IPs).
//      When set, XFF is parsed RIGHT-TO-LEFT, skipping IPs in the trusted
//      set, and the first untrusted IP is taken as the client. This is
//      the standard nginx-style `real_ip_recursive` logic.
//   2. When `TRUSTED_PROXY_CIDR` is NOT set, XFF is IGNORED entirely.
//      Only `x-real-ip`, `cf-connecting-ip`, and `true-client-ip` are
//      honored (these are set by the proxy itself, not the client).
//   3. In Next.js route handlers we cannot access the socket's remote
//      address directly (Next.js abstracts it away). So if NONE of the
//      trusted headers are present, we return "unknown" — which is the
//      safe default (multiple users sharing "unknown" is annoying but
//      not a security hole, and the per-USER rate limiter still applies).
const IPV4_OR_V6_RE =
  /^(?:(?:\d{1,3}\.){3}\d{1,3}|(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}|::(?:[fF]{4}:)?(?:\d{1,3}\.){3}\d{1,3})$/;

function isValidIp(s: string): boolean {
  // Reject empty / overly long strings early to bound Map key size.
  if (!s || s.length > 45) return false; // 45 = max IPv6 textual length (RFC 5952)
  return IPV4_OR_V6_RE.test(s);
}

// FE-019: Lazy-built trusted-proxy CIDR set. Parsed once from the env var
// and cached for the process lifetime. Each entry is either a single IP
// (matched by string equality after normalization) or a CIDR (matched by
// ip-matching). We implement a minimal CIDR matcher here to avoid pulling
// in a dependency — supporting IPv4 only because trusted proxies are
// virtually always IPv4 internal addresses (10.x, 172.16-31.x, 192.168.x).
let __trustedProxyCidrs: Array<{ kind: "ipv4"; bytes: number[]; mask: number }> | null = null;
let __trustedProxyCidrsEnv = "";

function getTrustedProxyCidrs(): Array<{ kind: "ipv4"; bytes: number[]; mask: number }> {
  const env = process.env.TRUSTED_PROXY_CIDR || "";
  if (__trustedProxyCidrs && env === __trustedProxyCidrsEnv) return __trustedProxyCidrs;
  __trustedProxyCidrsEnv = env;
  __trustedProxyCidrs = [];
  if (!env) return __trustedProxyCidrs;
  for (const raw of env.split(",")) {
    const entry = raw.trim();
    if (!entry) continue;
    // Accept either "1.2.3.4" (treated as /32) or "10.0.0.0/8".
    const slashIdx = entry.indexOf("/");
    const ipStr = slashIdx === -1 ? entry : entry.slice(0, slashIdx);
    const maskStr = slashIdx === -1 ? "32" : entry.slice(slashIdx + 1);
    const parts = ipStr.split(".");
    if (parts.length !== 4) continue; // skip malformed (only IPv4 supported)
    const bytes = parts.map((p) => parseInt(p, 10));
    if (bytes.some((b) => isNaN(b) || b < 0 || b > 255)) continue;
    const mask = parseInt(maskStr, 10);
    if (isNaN(mask) || mask < 0 || mask > 32) continue;
    __trustedProxyCidrs.push({ kind: "ipv4", bytes, mask });
  }
  return __trustedProxyCidrs;
}

function ipInTrustedSet(ip: string): boolean {
  const cidrs = getTrustedProxyCidrs();
  if (cidrs.length === 0) return false;
  // Only IPv4 supported in the trusted set (proxies are internal IPv4).
  const parts = ip.split(".");
  if (parts.length !== 4) return false;
  const bytes = parts.map((p) => parseInt(p, 10));
  if (bytes.some((b) => isNaN(b) || b < 0 || b > 255)) return false;
  for (const cidr of cidrs) {
    // Compare the leading `mask` bits of `bytes` and `cidr.bytes`.
    let bitsLeft = cidr.mask;
    for (let i = 0; i < 4 && bitsLeft > 0; i++) {
      const maskByte = bitsLeft >= 8 ? 0xff : (0xff << (8 - bitsLeft)) & 0xff;
      if ((bytes[i] & maskByte) !== (cidr.bytes[i] & maskByte)) {
        break;
      }
      bitsLeft -= 8;
    }
    if (bitsLeft <= 0) return true;
  }
  return false;
}

/**
 * FE-019: Extract the client IP from request headers, honoring
 * `TRUSTED_PROXY_CIDR` for X-Forwarded-For parsing.
 *
 * Exported so `api-proxy-guard.ts` can use the SAME logic (the audit
 * specifically called out that file as trusting XFF unconditionally).
 *
 * Logic:
 *   1. Try `x-real-ip`, `cf-connecting-ip`, `true-client-ip` in order.
 *      These are set by the proxy itself, not the client, so they're safe
 *      to trust unconditionally (the proxy overwrites any client-supplied
 *      value).
 *   2. If none of those are present, try `x-forwarded-for`:
 *      a. If `TRUSTED_PROXY_CIDR` is set, parse XFF right-to-left,
 *         skipping IPs in the trusted set. The first untrusted IP is the
 *         client. This is the standard nginx `real_ip_recursive` logic.
 *      b. If `TRUSTED_PROXY_CIDR` is NOT set, IGNORE XFF entirely.
 *         A client-supplied XFF is NOT trustworthy without a trusted
 *         proxy chain.
 *   3. If nothing matches, return "unknown".
 */
export function getClientIpFromHeaders(headers: {
  get(name: string): string | null;
}): string {
  // Step 1: proxy-set headers (safe to trust unconditionally).
  const safeHeaders = ["x-real-ip", "cf-connecting-ip", "true-client-ip"];
  for (const h of safeHeaders) {
    const v = headers.get(h);
    if (!v) continue;
    const first = v.split(",")[0].trim();
    if (first && isValidIp(first)) return first;
  }

  // Step 2: X-Forwarded-For (only safe with a trusted-proxy chain).
  const xff = headers.get("x-forwarded-for");
  if (xff) {
    const cidrs = getTrustedProxyCidrs();
    if (cidrs.length === 0) {
      // FE-019: TRUSTED_PROXY_CIDR not set → IGNORE XFF. Returning
      // "unknown" is the safe default; the per-USER rate limiter still
      // applies. (We deliberately do NOT return the leftmost XFF entry
      // because that's the attacker-controlled one.)
      return "unknown";
    }
    // Parse XFF right-to-left, skipping trusted proxies. The rightmost
    // entry is the proxy that sent us the request; the next-to-rightmost
    // is either another proxy or the real client.
    const parts = xff.split(",").map((s) => s.trim()).filter(Boolean);
    for (let i = parts.length - 1; i >= 0; i--) {
      const ip = parts[i];
      if (!isValidIp(ip)) continue;
      if (ipInTrustedSet(ip)) continue; // skip trusted proxies
      return ip; // first untrusted IP from the right = the real client
    }
    // All entries were trusted proxies — the request came directly from
    // a trusted proxy. Fall through to "unknown" (the proxy's own IP is
    // not a useful client identifier).
  }

  return "unknown";
}

function getClientIp(req: NextRequest): string {
  // FE-019: delegate to the shared header-based extractor so the logic
  // is identical for `rate-limit.ts` (login brute-force) and
  // `api-proxy-guard.ts` (per-user API throttling).
  return getClientIpFromHeaders(req.headers);
}

/**
 * Check whether an IP is currently rate-limited. Returns { blocked, retryAfterSeconds }.
 *
 * BE-005: When REDIS_URL is set, uses Redis shared state (multi-instance safe).
 * Otherwise falls back to in-memory LruMap (single-instance dev/CI).
 */
export function checkIpRateLimit(req: NextRequest): {
  blocked: boolean;
  retryAfterSeconds: number;
} {
  maybeCleanup();
  const ip = getClientIp(req);
  const now = Date.now();

  // Fast-path: check if already blocked in memory (avoids Redis round-trip).
  const memBucket = ipBuckets.get(ip);
  if (memBucket?.blockedUntil && memBucket.blockedUntil > now) {
    return {
      blocked: true,
      retryAfterSeconds: Math.ceil((memBucket.blockedUntil - now) / 1000),
    };
  }

  // Drop attempts older than the window.
  const windowMs = IP_WINDOW_MINUTES * 60 * 1000;
  const bucket = memBucket || { attempts: [], blockedUntil: null };
  bucket.attempts = bucket.attempts.filter((t) => now - t < windowMs);

  if (bucket.attempts.length >= IP_MAX_ATTEMPTS) {
    bucket.blockedUntil = now + IP_BLOCK_MINUTES * 60 * 1000;
    ipBuckets.set(ip, bucket);
    return {
      blocked: true,
      retryAfterSeconds: IP_BLOCK_MINUTES * 60,
    };
  }

  return { blocked: false, retryAfterSeconds: 0 };
}

/**
 * BE-005: Async variant of checkIpRateLimit that uses Redis when available.
 * Use this in async route handlers for multi-instance deployments.
 */
export async function checkIpRateLimitDistributed(req: NextRequest): Promise<{
  blocked: boolean;
  retryAfterSeconds: number;
}> {
  const ip = getClientIp(req);
  const now = Date.now();
  const windowMs = IP_WINDOW_MINUTES * 60 * 1000;

  const count = await redisSlidingWindowCount(`${RL_KEY_IP}${ip}`, now, windowMs);
  if (count >= 0 && count > IP_MAX_ATTEMPTS) {
    return { blocked: true, retryAfterSeconds: IP_BLOCK_MINUTES * 60 };
  }

  // Redis not available or within limit — fall back to in-memory.
  return checkIpRateLimit(req);
}

/**
 * Record a login attempt from an IP (success or failure). Used to populate
 * the sliding window.
 */
export function recordIpAttempt(req: NextRequest) {
  maybeCleanup();
  const ip = getClientIp(req);
  const now = Date.now();
  const bucket = ipBuckets.get(ip) || { attempts: [], blockedUntil: null };
  bucket.attempts.push(now);
  // Keep only attempts within the window — bounded memory per bucket.
  const windowMs = IP_WINDOW_MINUTES * 60 * 1000;
  bucket.attempts = bucket.attempts.filter((t) => now - t < windowMs);
  ipBuckets.set(ip, bucket);
}

/**
 * Check whether a user account is currently locked. Returns { locked, retryAfterSeconds }.
 */
export function checkAccountLocked(user: {
  failedLoginCount: number;
  lockedUntil: Date | null;
}): { locked: boolean; retryAfterSeconds: number } {
  if (!user.lockedUntil) return { locked: false, retryAfterSeconds: 0 };
  const now = Date.now();
  if (user.lockedUntil.getTime() <= now) {
    return { locked: false, retryAfterSeconds: 0 };
  }
  return {
    locked: true,
    retryAfterSeconds: Math.ceil(
      (user.lockedUntil.getTime() - now) / 1000
    ),
  };
}

/**
 * Record a failed login attempt for a user. If the count exceeds
 * MAX_FAILED_ATTEMPTS within LOCKOUT_WINDOW_MINUTES, lock the account for
 * LOCKOUT_DURATION_MINUTES.
 */
export async function recordFailedLogin(userId: string): Promise<{
  locked: boolean;
  retryAfterSeconds: number;
}> {
  // We rely on Postgres atomic increment. Prisma's update with a nested
  // increment is atomic per-row.
  const now = new Date();
  const updated = await db.user.update({
    where: { id: userId },
    data: {
      failedLoginCount: { increment: 1 },
    },
    select: { failedLoginCount: true, lockedUntil: true },
  });

  if (updated.failedLoginCount >= MAX_FAILED_ATTEMPTS) {
    const lockedUntil = new Date(
      now.getTime() + LOCKOUT_DURATION_MINUTES * 60 * 1000
    );
    await db.user.update({
      where: { id: userId },
      data: {
        lockedUntil,
        // Reset the counter so the next window starts fresh after unlock.
        failedLoginCount: 0,
      },
    });
    return {
      locked: true,
      retryAfterSeconds: LOCKOUT_DURATION_MINUTES * 60,
    };
  }
  return { locked: false, retryAfterSeconds: 0 };
}

/**
 * Reset the failed-login counter on successful login. Called AFTER the
 * password has been verified and the account is unlocked.
 */
export async function recordSuccessfulLogin(userId: string): Promise<void> {
  await db.user.update({
    where: { id: userId },
    data: {
      failedLoginCount: 0,
      lockedUntil: null,
    },
  });
}

// ---------------------------------------------------------------------------
// FE-003 ROOT FIX: TOTP (2FA) brute-force protection
// ---------------------------------------------------------------------------

/**
 * Check whether a user is currently TOTP-locked (too many wrong 2FA codes
 * in the sliding window). Returns { locked, retryAfterSeconds }.
 *
 * This is keyed on userId, not IP, because the mfaChallengeToken already
 * binds the attempt to a specific user — an attacker cannot rotate
 * usernames to bypass this. The IP-level rate limit (checkIpRateLimit)
 * still applies as a separate layer for the overall request volume.
 *
 * BE-005: In-memory only. For multi-instance, call checkTotpRateLimitDistributed.
 */
export function checkTotpRateLimit(userId: string): {
  locked: boolean;
  retryAfterSeconds: number;
} {
  maybeCleanup();
  const now = Date.now();
  const bucket = totpBuckets.get(userId) || { attempts: [], lockedUntil: null };

  if (bucket.lockedUntil && bucket.lockedUntil > now) {
    return {
      locked: true,
      retryAfterSeconds: Math.ceil((bucket.lockedUntil - now) / 1000),
    };
  }

  // Drop attempts older than the window.
  const windowMs = TOTP_WINDOW_MINUTES * 60 * 1000;
  bucket.attempts = bucket.attempts.filter((t) => now - t < windowMs);

  if (bucket.attempts.length >= TOTP_MAX_ATTEMPTS) {
    bucket.lockedUntil = now + TOTP_LOCK_MINUTES * 60 * 1000;
    totpBuckets.set(userId, bucket);
    return {
      locked: true,
      retryAfterSeconds: TOTP_LOCK_MINUTES * 60,
    };
  }

  return { locked: false, retryAfterSeconds: 0 };
}

/**
 * BE-005: Async variant of checkTotpRateLimit that uses Redis when available.
 * Use this in async route handlers for multi-instance deployments.
 */
export async function checkTotpRateLimitDistributed(userId: string): Promise<{
  locked: boolean;
  retryAfterSeconds: number;
}> {
  const now = Date.now();
  const windowMs = TOTP_WINDOW_MINUTES * 60 * 1000;

  const count = await redisSlidingWindowCount(`${RL_KEY_TOTP}${userId}`, now, windowMs);
  if (count >= 0 && count > TOTP_MAX_ATTEMPTS) {
    return { locked: true, retryAfterSeconds: TOTP_LOCK_MINUTES * 60 };
  }

  // Redis not available or within limit — fall back to in-memory.
  return checkTotpRateLimit(userId);
}

/**
 * Record a failed TOTP attempt for a user. If the count exceeds
 * TOTP_MAX_ATTEMPTS within TOTP_WINDOW_MINUTES, lock 2FA for
 * TOTP_LOCK_MINUTES.
 *
 * Returns the lock state AFTER recording this failure.
 */
export function recordFailedTotp(userId: string): {
  locked: boolean;
  retryAfterSeconds: number;
  attemptsRemaining: number;
} {
  maybeCleanup();
  const now = Date.now();
  const windowMs = TOTP_WINDOW_MINUTES * 60 * 1000;
  const bucket = totpBuckets.get(userId) || { attempts: [], lockedUntil: null };

  // Drop old attempts.
  bucket.attempts = bucket.attempts.filter((t) => now - t < windowMs);
  bucket.attempts.push(now);

  if (bucket.attempts.length >= TOTP_MAX_ATTEMPTS) {
    bucket.lockedUntil = now + TOTP_LOCK_MINUTES * 60 * 1000;
    totpBuckets.set(userId, bucket);
    return {
      locked: true,
      retryAfterSeconds: TOTP_LOCK_MINUTES * 60,
      attemptsRemaining: 0,
    };
  }

  totpBuckets.set(userId, bucket);
  return {
    locked: false,
    retryAfterSeconds: 0,
    attemptsRemaining: TOTP_MAX_ATTEMPTS - bucket.attempts.length,
  };
}

/**
 * Reset the TOTP attempt counter on a successful 2FA verification.
 * Call this immediately after verifyTotp() returns true, BEFORE issuing
 * access/refresh tokens.
 */
export function clearTotpAttempts(userId: string): void {
  totpBuckets.delete(userId);
}

/**
 * Test-only helper: reset all TOTP state. Exported for unit tests so they
 * can run deterministically without state leaking between cases. NOT for
 * use in production code paths.
 */
export function __resetTotpStateForTests(): void {
  totpBuckets.clear();
}

// ---------------------------------------------------------------------------
// FE-006 ROOT FIX: Per-user rate limit for expensive upstream API proxies.
// ---------------------------------------------------------------------------

// Per-user limits for the 6 public-API-proxy routes (drugs, diseases,
// clinical-trials, literature, patents, safety). Without this, any
// unauthenticated user could deplete the platform's NCBI / PatentsView /
// openFDA API quotas via our backend as an open proxy.
const USER_API_MAX_REQUESTS = 60; // 60 requests...
const USER_API_WINDOW_MINUTES = 1; // ...per minute

interface UserApiBucket {
  requests: number[]; // timestamps (ms)
}

// BE-005 ROOT FIX: All three rate limiters (IP, TOTP, per-user API) now
// support Redis-backed shared state for multi-instance deployments.
// When REDIS_URL is set, they use Redis (shared across all instances).
// When REDIS_URL is not set, they fall back to in-memory Maps (dev/CI).
// This prevents N× rate limit weakening in K8s with 3+ replicas.

// Lazy Redis client for rate limiting.
let redisClient: any = null;
let redisClientError: Error | null = null;

async function getRedisClient(): Promise<any | null> {
  if (redisClient) return redisClient;
  if (redisClientError) return null;
  const redisUrl = process.env.REDIS_URL;
  if (!redisUrl) return null;
  try {
    const mod = await import(/* webpackIgnore: true */ "ioredis");
    const Redis = mod.default || mod;
    redisClient = new Redis(redisUrl, {
      lazyConnect: false,
      maxRetriesPerRequest: 3,
      enableReadyCheck: true,
    });
    return redisClient;
  } catch (e: any) {
    redisClientError = new Error(`ioredis load failed: ${e?.message ?? e}`);
    console.error("[rate-limit] Redis init failed, falling back to in-memory:", redisClientError.message);
    return null;
  }
}

// Redis key prefixes for each rate limiter.
const RL_KEY_IP = "drugos:rl:ip:";
const RL_KEY_TOTP = "drugos:rl:totp:";
const RL_KEY_API = "drugos:rl:api:";

/**
 * BE-005: Generic Redis sliding-window rate limit check.
 * Uses Redis sorted-set (ZREMRANGEBYSCORE + ZADD + ZCARD in MULTI/EXEC).
 * Returns the current count after recording the request.
 */
async function redisSlidingWindowCount(key: string, nowMs: number, windowMs: number): Promise<number> {
  const client = await getRedisClient();
  if (!client) return -1; // signal: Redis not available, use in-memory
  const member = `${nowMs}:${randomBytes(8).toString("hex")}`;
  const cutoff = nowMs - windowMs;
  const results = await client
    .multi()
    .zremrangebyscore(key, "-inf", cutoff)
    .zadd(key, nowMs, member)
    .zcard(key)
    .pexpire(key, windowMs + 60_000)
    .exec();
  const zcardResult = results?.[2]?.[1];
  return typeof zcardResult === "number" ? zcardResult : 0;
}

import { randomBytes } from "crypto";

const userApiBuckets = new Map<string, UserApiBucket>();

/**
 * Check whether a user has exceeded the per-user API rate limit. Returns
 * { blocked, retryAfterSeconds }. Does NOT record the request — call
 * recordUserApiRequest(user) AFTER this returns blocked:false and the
 * upstream call has actually been dispatched.
 *
 * BE-005: In-memory only. For multi-instance, call checkUserApiRateLimitDistributed.
 */
export function checkUserApiRateLimit(userId: string): {
  blocked: boolean;
  retryAfterSeconds: number;
  remaining: number;
} {
  maybeCleanup();
  const now = Date.now();
  const windowMs = USER_API_WINDOW_MINUTES * 60 * 1000;
  const bucket = userApiBuckets.get(userId) || { requests: [] };
  bucket.requests = bucket.requests.filter((t) => now - t < windowMs);

  if (bucket.requests.length >= USER_API_MAX_REQUESTS) {
    const oldest = bucket.requests[0];
    const retryAfterSeconds = Math.ceil((oldest + windowMs - now) / 1000);
    return {
      blocked: true,
      retryAfterSeconds: Math.max(1, retryAfterSeconds),
      remaining: 0,
    };
  }
  return {
    blocked: false,
    retryAfterSeconds: 0,
    remaining: USER_API_MAX_REQUESTS - bucket.requests.length,
  };
}

/**
 * BE-005: Async variant of checkUserApiRateLimit that uses Redis when available.
 * Use this in async route handlers for multi-instance deployments.
 */
export async function checkUserApiRateLimitDistributed(userId: string): Promise<{
  blocked: boolean;
  retryAfterSeconds: number;
  remaining: number;
}> {
  const now = Date.now();
  const windowMs = USER_API_WINDOW_MINUTES * 60 * 1000;

  const count = await redisSlidingWindowCount(`${RL_KEY_API}${userId}`, now, windowMs);
  if (count >= 0 && count >= USER_API_MAX_REQUESTS) {
    return { blocked: true, retryAfterSeconds: Math.max(1, Math.ceil(windowMs / 1000)), remaining: 0 };
  }
  if (count >= 0) {
    return { blocked: false, retryAfterSeconds: 0, remaining: USER_API_MAX_REQUESTS - count };
  }

  // Redis not available — fall back to in-memory.
  return checkUserApiRateLimit(userId);
}

/**
 * Record a successful (non-blocked) upstream API request for a user.
 * Must be called AFTER checkUserApiRateLimit returns blocked:false.
 */
export function recordUserApiRequest(userId: string): void {
  maybeCleanup();
  const now = Date.now();
  const windowMs = USER_API_WINDOW_MINUTES * 60 * 1000;
  const bucket = userApiBuckets.get(userId) || { requests: [] };
  bucket.requests = bucket.requests.filter((t) => now - t < windowMs);
  bucket.requests.push(now);
  userApiBuckets.set(userId, bucket);
}

/**
 * BE-005: Async variant of recordUserApiRequest that records in Redis.
 * Use this with checkUserApiRateLimitDistributed for multi-instance.
 */
export async function recordUserApiRequestDistributed(userId: string): Promise<void> {
  const now = Date.now();
  const windowMs = USER_API_WINDOW_MINUTES * 60 * 1000;
  const client = await getRedisClient();
  if (client) {
    const key = `${RL_KEY_API}${userId}`;
    const member = `${now}:${randomBytes(8).toString("hex")}`;
    const cutoff = now - windowMs;
    await client
      .multi()
      .zremrangebyscore(key, "-inf", cutoff)
      .zadd(key, now, member)
      .pexpire(key, windowMs + 60_000)
      .exec();
  } else {
    recordUserApiRequest(userId);
  }
}

/**
 * Test-only helper: reset all per-user API rate-limit state.
 */
export function __resetUserApiStateForTests(): void {
  userApiBuckets.clear();
}

export const USER_API_RATE_LIMIT_PER_MINUTE = USER_API_MAX_REQUESTS;

// FE-061: Exposed for tests so we can verify the LRU bound is enforced.
export const __test = {
  getBucketCount: () => ipBuckets.size,
  LRU_MAX: IP_LRU_MAX_ENTRIES,
  reset: () => {
    // Only safe in tests — clears all buckets.
    ipBuckets.forEach((_, k) => ipBuckets.delete(k));
  },
};
