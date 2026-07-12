/**
 * One-time setup token for 2FA enrollment.
 *
 * FE-071 ROOT FIX: /api/auth/2fa/setup returned the TOTP secret in
 * plaintext JSON. This is necessary for QR-code rendering, but if any XSS
 * exists anywhere in the app, the attacker can read the secret and
 * permanently compromise the user's 2FA — they can call /verify themselves
 * to persist it.
 *
 * Mitigation (this module): issue a short-lived, one-time-use setup token
 * bound to the user's session. The token is returned alongside the secret,
 * but:
 *   1. It can only be used ONCE. After /verify consumes it, a second
 *      attacker request with the same token is rejected.
 *   2. It expires after 5 minutes (TTL).
 *   3. It is bound to the userId — a stolen token cannot be used to enroll
 *      2FA for a different user.
 *
 * This does NOT fully prevent XSS-driven 2FA compromise (an attacker who
 * can read the response can also call /verify immediately), but it DOES:
 *   - Close the replay window (a token sniffed from logs cannot be reused).
 *   - Add a defense-in-depth layer on top of the CSP headers (which are
 *     the primary XSS mitigation).
 *
 * The token is a random 32-byte hex string. We store a SHA-256 hash of it
 * in memory (never the raw token). Lookup is O(1) via Map.
 */

import { createHash, randomBytes } from "crypto";

const SETUP_TOKEN_TTL_MS = 5 * 60 * 1000; // 5 minutes
const MAX_CONCURRENT_TOKENS = 10000; // bounded memory

interface PendingEnrollment {
  userId: string;
  secretHash: string; // sha256(secret) — we don't store the raw secret
  setupTokenHash: string; // sha256(setupToken)
  expiresAt: number; // ms epoch
  usedAt: number | null;
}

// Keyed by setupTokenHash for O(1) lookup. Value carries userId for the
// reverse check.
const pending = new Map<string, PendingEnrollment>();

// Periodic cleanup so the Map doesn't grow unboundedly.
let lastCleanup = Date.now();
const CLEANUP_INTERVAL_MS = 5 * 60 * 1000;

function maybeCleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  for (const [hash, entry] of pending) {
    if (entry.expiresAt < now || entry.usedAt !== null) {
      pending.delete(hash);
    }
  }
}

function sha256(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

/**
 * Generate a fresh TOTP secret + a one-time setup token bound to `userId`.
 * The secret is NOT stored here — the caller returns it to the client.
 * We store only hashes (defense in depth: a memory dump can't recover
 * either secret).
 *
 * Returns:
 *   - secret: the raw base32 TOTP secret (caller returns to client for QR)
 *   - setupToken: the raw one-time token (caller returns to client)
 *
 * The client must send BOTH secret + setupToken to /api/auth/2fa/verify.
 */
export function issue2faSetupToken(userId: string, secret: string): {
  secret: string;
  setupToken: string;
  expiresAt: number;
} {
  maybeCleanup();

  // Bound memory: if we somehow have >MAX_CONCURRENT_TOKENS pending, evict
  // the oldest. This should never happen in normal use (5-min TTL + cleanup
  // keeps the Map tiny), but defense in depth.
  if (pending.size > MAX_CONCURRENT_TOKENS) {
    let oldestHash: string | null = null;
    let oldestTime = Infinity;
    for (const [hash, entry] of pending) {
      if (entry.expiresAt < oldestTime) {
        oldestTime = entry.expiresAt;
        oldestHash = hash;
      }
    }
    if (oldestHash) pending.delete(oldestHash);
  }

  const setupToken = randomBytes(32).toString("hex");
  const expiresAt = Date.now() + SETUP_TOKEN_TTL_MS;
  const entry: PendingEnrollment = {
    userId,
    secretHash: sha256(secret),
    setupTokenHash: sha256(setupToken),
    expiresAt,
    usedAt: null,
  };
  pending.set(entry.setupTokenHash, entry);

  return { secret, setupToken, expiresAt };
}

export interface Verify2faSetupResult {
  ok: boolean;
  reason?: "token_not_found" | "token_used" | "token_expired" | "user_mismatch" | "secret_mismatch";
}

/**
 * Validate a setup token presented by /api/auth/2fa/verify. On success,
 * mark the token as used so it can never be replayed.
 *
 * Checks (in order):
 *   1. Token hash exists in the pending map.
 *   2. Token has not been used (usedAt === null).
 *   3. Token has not expired.
 *   4. The userId on the request matches the userId bound to the token.
 *   5. The secret on the request matches the secret hash bound to the token
 *      (defense in depth: prevents an attacker from substituting their own
 *      secret while reusing a stolen token).
 *
 * On success, marks the entry used and returns { ok: true }. The caller
 * then persists mfaSecret + mfaEnabled on the User row.
 */
export function verify2faSetupToken(
  userId: string,
  secret: string,
  setupToken: string
): Verify2faSetupResult {
  maybeCleanup();

  const tokenHash = sha256(setupToken);
  const entry = pending.get(tokenHash);
  if (!entry) {
    return { ok: false, reason: "token_not_found" };
  }
  if (entry.usedAt !== null) {
    return { ok: false, reason: "token_used" };
  }
  if (entry.expiresAt < Date.now()) {
    // Evict expired entry.
    pending.delete(tokenHash);
    return { ok: false, reason: "token_expired" };
  }
  if (entry.userId !== userId) {
    return { ok: false, reason: "user_mismatch" };
  }
  if (entry.secretHash !== sha256(secret)) {
    return { ok: false, reason: "secret_mismatch" };
  }

  // Mark as used — one-time enforcement.
  entry.usedAt = Date.now();
  pending.set(tokenHash, entry);
  return { ok: true };
}

/**
 * Test-only helper: clear all pending tokens. Never call from production.
 */
export function __clear2faSetupTokensForTests(): void {
  pending.clear();
  lastCleanup = Date.now();
}
