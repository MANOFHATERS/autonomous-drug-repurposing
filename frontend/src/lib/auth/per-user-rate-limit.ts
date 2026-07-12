/**
 * Generic per-user rate limiter (in-memory sliding window).
 *
 * FE-069 ROOT FIX: /api/rl was hitting disk + parsing CSV on every request
 * with no rate limiting — a single authenticated user could DoS the platform
 * by spamming GET/POST /api/rl. The existing `rate-limit.ts` only protects
 * the login endpoint (per-IP + per-account lockout for brute-force). There
 * was no general per-user throttle for expensive authenticated endpoints.
 *
 * This module provides a per-userId sliding-window counter. It is:
 *   - In-memory (single-node). For multi-node, swap in @upstash/ratelimit.
 *   - Bounded: each user bucket is capped at `windowRequests` entries; the
 *     Map is periodically garbage-collected so it cannot grow unboundedly.
 *   - Self-cleaning: a cleanup pass runs at most once every 10 minutes.
 *
 * Usage:
 *   const rl = checkUserRateLimit(userId, { max: 60, windowSeconds: 60 });
 *   if (rl.blocked) return NextResponse.json({ error: "rate_limited" }, {
 *     status: 429,
 *     headers: { "Retry-After": String(rl.retryAfterSeconds) },
 *   });
 */

interface UserBucket {
  attempts: number[]; // ms timestamps within the current window
}

const DEFAULT_MAX = 60; // 60 requests...
const DEFAULT_WINDOW_SECONDS = 60; // ...per 60 seconds (60 req/min)
const CLEANUP_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes
const MAX_BUCKET_ATTEMPTS = 1000; // hard cap per bucket to bound memory

const buckets = new Map<string, UserBucket>();
let lastCleanup = Date.now();

function maybeCleanup(windowMs: number) {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  const cutoff = now - windowMs;
  for (const [userId, bucket] of buckets) {
    bucket.attempts = bucket.attempts.filter((t) => t >= cutoff);
    if (bucket.attempts.length === 0) {
      buckets.delete(userId);
    }
  }
}

export interface UserRateLimitOptions {
  max?: number;
  windowSeconds?: number;
}

export interface UserRateLimitResult {
  blocked: boolean;
  retryAfterSeconds: number;
  remaining: number;
}

/**
 * Check whether `userId` is currently rate-limited. Records the attempt
 * (success path) — call this BEFORE doing the expensive work, not after,
 * so a flood of requests is rejected before touching disk/DB.
 *
 * Returns `{ blocked: true, retryAfterSeconds }` if the user has exceeded
 * their quota within the window. Otherwise `{ blocked: false, remaining }`.
 */
export function checkUserRateLimit(
  userId: string,
  opts: UserRateLimitOptions = {}
): UserRateLimitResult {
  const max = opts.max ?? DEFAULT_MAX;
  const windowSeconds = opts.windowSeconds ?? DEFAULT_WINDOW_SECONDS;
  const windowMs = windowSeconds * 1000;
  const now = Date.now();

  maybeCleanup(windowMs);

  const bucket = buckets.get(userId) || { attempts: [] };
  // Drop attempts outside the window.
  bucket.attempts = bucket.attempts.filter((t) => now - t < windowMs);

  if (bucket.attempts.length >= max) {
    // Oldest attempt in window — when it falls off, the user can retry.
    const oldest = bucket.attempts[0];
    const retryAfterSeconds = Math.max(
      1,
      Math.ceil((oldest + windowMs - now) / 1000)
    );
    buckets.set(userId, bucket);
    return { blocked: true, retryAfterSeconds, remaining: 0 };
  }

  // Record this attempt.
  bucket.attempts.push(now);
  // Bound memory: if a malicious user somehow generates >MAX_BUCKET_ATTEMPTS
  // within the window (shouldn't be possible due to the max check above, but
  // defense in depth), trim the oldest.
  if (bucket.attempts.length > MAX_BUCKET_ATTEMPTS) {
    bucket.attempts = bucket.attempts.slice(-MAX_BUCKET_ATTEMPTS);
  }
  buckets.set(userId, bucket);

  return {
    blocked: false,
    retryAfterSeconds: 0,
    remaining: Math.max(0, max - bucket.attempts.length),
  };
}

/**
 * Reset a user's rate-limit bucket. Used in tests and (rarely) by admins
 * to manually unblock a user.
 */
export function resetUserRateLimit(userId: string): void {
  buckets.delete(userId);
}

/**
 * Test-only helper: clear ALL buckets. Never call from production code.
 */
export function __clearAllUserRateLimitsForTests(): void {
  buckets.clear();
  lastCleanup = Date.now();
}
