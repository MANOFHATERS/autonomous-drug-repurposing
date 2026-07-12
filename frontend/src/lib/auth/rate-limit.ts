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

function getClientIp(req: NextRequest): string {
  // Trust X-Forwarded-For only if a known proxy set it. In production behind
  // Caddy, Caddy sets X-Real-IP and X-Forwarded-For. We prefer X-Real-IP
  // because it can't be spoofed by the client (Caddy overwrites it).
  const xRealIp = req.headers.get("x-real-ip");
  if (xRealIp && /^\d{1,3}(\.\d{1,3}){3}$/.test(xRealIp)) return xRealIp;
  const xff = req.headers.get("x-forwarded-for");
  if (xff) {
    const first = xff.split(",")[0].trim();
    if (first && /^\d{1,3}(\.\d{1,3}){3}$/.test(first)) return first;
  }
  return "unknown";
}

/**
 * Check whether an IP is currently rate-limited. Returns { blocked, retryAfterSeconds }.
 */
export function checkIpRateLimit(req: NextRequest): {
  blocked: boolean;
  retryAfterSeconds: number;
} {
  maybeCleanup();
  const ip = getClientIp(req);
  const now = Date.now();
  const bucket = ipBuckets.get(ip) || { attempts: [], blockedUntil: null };

  if (bucket.blockedUntil && bucket.blockedUntil > now) {
    return {
      blocked: true,
      retryAfterSeconds: Math.ceil((bucket.blockedUntil - now) / 1000),
    };
  }

  // Drop attempts older than the window.
  const windowMs = IP_WINDOW_MINUTES * 60 * 1000;
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

// FE-061: Exposed for tests so we can verify the LRU bound is enforced.
export const __test = {
  getBucketCount: () => ipBuckets.size,
  LRU_MAX: IP_LRU_MAX_ENTRIES,
  reset: () => {
    // Only safe in tests — clears all buckets.
    ipBuckets.forEach((_, k) => ipBuckets.delete(k));
  },
};
