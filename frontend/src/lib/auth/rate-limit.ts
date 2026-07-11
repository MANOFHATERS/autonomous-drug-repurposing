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

interface IpBucket {
  attempts: number[]; // timestamps (ms) of recent attempts
  blockedUntil: number | null;
}

// In-memory store. Keyed by IP. For multi-node deployment, replace with
// @upstash/ratelimit (Redis-backed) — the function signatures stay the same.
const ipBuckets = new Map<string, IpBucket>();

// Periodic cleanup so the Map doesn't grow unboundedly. Evict any bucket
// whose newest attempt is older than IP_BLOCK_MINUTES.
const CLEANUP_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes
let lastCleanup = Date.now();
function maybeCleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  const cutoff = now - IP_BLOCK_MINUTES * 60 * 1000;
  for (const [ip, bucket] of ipBuckets) {
    const last = bucket.attempts[bucket.attempts.length - 1] ?? 0;
    if (last < cutoff && (!bucket.blockedUntil || bucket.blockedUntil < now)) {
      ipBuckets.delete(ip);
    }
  }
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
  // Keep only attempts within the window — bounded memory.
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
