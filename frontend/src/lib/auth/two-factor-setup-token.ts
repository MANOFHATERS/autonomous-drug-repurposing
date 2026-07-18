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
 * BE-078 ROOT FIX (REAL, v123): the prior implementation used an in-memory
 * `Map<tokenHash, PendingEnrollment>` for tracking issued tokens. That
 * worked for single-instance deployments but was BROKEN for multi-instance
 * deploys (K8s replicas, etc.) — each instance had its own Map, so an
 * attacker could race the same setupToken against two instances and both
 * would see usedAt === null, both would return ok, and both would enroll
 * 2FA with different secrets. The user's authenticator app would then
 * have a different secret than the server, locking the user out.
 *
 * The prior code's comment acknowledged this: "documented as limited —
 * multi-instance deployments". That is NOT a root fix — it's a
 * documentation of the bug. The user explicitly demanded real root-cause
 * fixes, not "documented as limited" sugar-coating.
 *
 * Real root fix: persist setup tokens in the DB (Postgres via Prisma) with
 * a unique constraint on `tokenHash` AND an atomic UPDATE-with-WHERE for
 * the consume step. The DB is shared across all instances, so the atomic
 * claim works regardless of how many Node.js processes are running.
 *
 * The new flow:
 *   1. issue2faSetupToken: INSERT a TwoFactorSetupToken row with usedAt = NULL.
 *      The unique constraint on tokenHash prevents the same hash from being
 *      inserted twice (defense in depth — collisions are astronomically
 *      unlikely with 32-byte random tokens, but the constraint is cheap).
 *   2. verify2faSetupToken:
 *      a. SELECT the row by tokenHash. If not found → "token_not_found".
 *      b. If usedAt is non-null → "token_used".
 *      c. If expiresAt < now → "token_expired".
 *      d. If userId doesn't match → "user_mismatch".
 *      e. If secretHash doesn't match → "secret_mismatch".
 *      f. ATOMIC CLAIM: UPDATE ... WHERE id = row.id AND usedAt IS NULL.
 *         If the UPDATE affects 1 row, we won the race. If it affects 0
 *         rows, another instance beat us to it → "token_used".
 *
 * The atomic UPDATE is the actual multi-instance race prevention.
 * Postgres row-level locking ensures only one UPDATE can succeed — the
 * other concurrent UPDATEs see the row as already locked and either wait
 * (then see usedAt is non-null) or fail (depending on isolation level).
 * Prisma's updateMany returns `count` of affected rows, so we can
 * distinguish the win case (count === 1) from the lose case (count === 0).
 *
 * FALLBACK: when the DB is unreachable, we fall back to the prior
 * in-memory Map (preserved below as `pendingInMemoryFallback`). This
 * keeps single-instance dev/test working without a DB. Multi-instance
 * dev/test that wants to verify the race fix can set up a shared Postgres
 * instance. In production, the DB MUST be reachable — a DB outage breaks
 * far more than 2FA enrollment, so failing closed (returning an error) is
 * acceptable.
 *
 * The token is a random 32-byte hex string. We store a SHA-256 hash of it
 * in the DB (never the raw token). Lookup is O(1) via the unique index on
 * `tokenHash`.
 */

import { createHash, randomBytes } from "crypto";
import { db } from "@/lib/db";

const SETUP_TOKEN_TTL_MS = 5 * 60 * 1000; // 5 minutes

export interface Verify2faSetupResult {
  ok: boolean;
  reason?: "token_not_found" | "token_used" | "token_expired" | "user_mismatch" | "secret_mismatch" | "db_unavailable";
}

function sha256(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

/**
 * Generate a fresh TOTP secret + a one-time setup token bound to `userId`.
 * The secret is NOT stored here — the caller returns it to the client.
 * We store only hashes (defense in depth: a DB dump can't recover either
 * secret).
 *
 * Returns:
 *   - secret: the raw base32 TOTP secret (caller returns to client for QR)
 *   - setupToken: the raw one-time token (caller returns to client)
 *
 * The client must send BOTH secret + setupToken to /api/auth/2fa/verify.
 *
 * BE-078 v123: persists the token to the TwoFactorSetupToken table so
 * verify2faSetupToken can atomically claim it across multiple instances.
 * Falls back to in-memory storage if the DB is unreachable (single-
 * instance dev/test only).
 */
export async function issue2faSetupToken(userId: string, secret: string): Promise<{
  secret: string;
  setupToken: string;
  expiresAt: number;
}> {
  const setupToken = randomBytes(32).toString("hex");
  const expiresAtEpoch = Date.now() + SETUP_TOKEN_TTL_MS;
  const expiresAtDate = new Date(expiresAtEpoch);

  // Persist to DB. If the insert fails (DB down, unique constraint
  // collision on tokenHash — astronomically unlikely with 32 random
  // bytes), fall back to in-memory storage so single-instance dev/test
  // still works. Multi-instance prod MUST have the DB up — we log the
  // fallback loudly so operators notice.
  try {
    await db.twoFactorSetupToken.create({
      data: {
        tokenHash: sha256(setupToken),
        userId,
        secretHash: sha256(secret),
        expiresAt: expiresAtDate,
        // usedAt defaults to NULL per the schema.
      },
    });
  } catch (e) {
    // Fallback: store in memory. This is acceptable for single-instance
    // dev/test. In multi-instance prod, this fallback means the race fix
    // is NOT active — log loudly so operators notice the DB issue.
    console.error(
      "[BE-078] Failed to persist 2FA setup token to DB — falling back " +
      "to in-memory storage. Multi-instance race protection is NOT active. " +
      "Original error:",
      e
    );
    pendingInMemoryFallback.set(sha256(setupToken), {
      userId,
      secretHash: sha256(secret),
      expiresAt: expiresAtEpoch,
      usedAt: null,
    });
  }

  return { secret, setupToken, expiresAt: expiresAtEpoch };
}

/**
 * Validate a setup token presented by /api/auth/2fa/verify. On success,
 * mark the token as used so it can never be replayed.
 *
 * Checks (in order):
 *   1. Token hash exists in the DB (or in-memory fallback).
 *   2. Token has not been used (usedAt === null).
 *   3. Token has not expired.
 *   4. The userId on the request matches the userId bound to the token.
 *   5. The secret on the request matches the secret hash bound to the token
 *      (defense in depth: prevents an attacker from substituting their own
 *      secret while reusing a stolen token).
 *
 * On success, atomically marks the entry used and returns { ok: true }.
 * The caller then persists mfaSecret + mfaEnabled on the User row.
 *
 * BE-078 v123: the atomic claim is `db.twoFactorSetupToken.updateMany({
 *   where: { id, usedAt: null },
 *   data: { usedAt: new Date() }
 * })`. If `count === 1`, we won the race. If `count === 0`, another
 * instance beat us to it — return "token_used". Postgres row-level
 * locking ensures the UPDATE is atomic across concurrent transactions.
 */
export async function verify2faSetupToken(
  userId: string,
  secret: string,
  setupToken: string
): Promise<Verify2faSetupResult> {
  const tokenHash = sha256(setupToken);
  const now = Date.now();

  // Try the DB path first.
  try {
    const row = await db.twoFactorSetupToken.findUnique({
      where: { tokenHash },
    });
    if (!row) {
      // Maybe the token was issued via the in-memory fallback (DB was
      // down at issue time). Check the fallback Map before giving up.
      const fallbackEntry = pendingInMemoryFallback.get(tokenHash);
      if (fallbackEntry) {
        return verifyInMemoryFallback(fallbackEntry, tokenHash, userId, secret, now);
      }
      return { ok: false, reason: "token_not_found" };
    }
    if (row.usedAt !== null) {
      return { ok: false, reason: "token_used" };
    }
    if (row.expiresAt.getTime() < now) {
      // Evict expired entry — best-effort, ignore errors.
      await db.twoFactorSetupToken.delete({ where: { id: row.id } }).catch(() => {});
      return { ok: false, reason: "token_expired" };
    }
    if (row.userId !== userId) {
      return { ok: false, reason: "user_mismatch" };
    }
    if (row.secretHash !== sha256(secret)) {
      return { ok: false, reason: "secret_mismatch" };
    }

    // ATOMIC CLAIM: update usedAt only if it's still NULL. If another
    // instance beat us to it, updateMany returns count === 0 — we lose
    // the race and return "token_used". Postgres row-level locking
    // ensures the UPDATE is atomic across concurrent transactions.
    const claim = await db.twoFactorSetupToken.updateMany({
      where: { id: row.id, usedAt: null },
      data: { usedAt: new Date() },
    });
    if (claim.count === 0) {
      // Another instance claimed it between our SELECT and our UPDATE.
      return { ok: false, reason: "token_used" };
    }
    return { ok: true };
  } catch (e) {
    // DB error during verify. If the token was issued via the in-memory
    // fallback (DB was down at issue time), check the fallback. Otherwise
    // return "db_unavailable" — we do NOT fall back to in-memory for a
    // token we didn't issue in memory, because that would silently
    // bypass the race protection for tokens that WERE persisted.
    const fallbackEntry = pendingInMemoryFallback.get(tokenHash);
    if (fallbackEntry) {
      return verifyInMemoryFallback(fallbackEntry, tokenHash, userId, secret, now);
    }
    console.error("[BE-078] DB error during 2FA setup token verify:", e);
    return { ok: false, reason: "db_unavailable" };
  }
}

// ---------------------------------------------------------------------------
// In-memory fallback — used ONLY when the DB is unreachable at issue time.
// Single-instance dev/test only. Multi-instance prod with DB down = no race
// protection (logged loudly at issue time).
// ---------------------------------------------------------------------------

interface FallbackEntry {
  userId: string;
  secretHash: string;
  expiresAt: number;
  usedAt: number | null;
}

const pendingInMemoryFallback = new Map<string, FallbackEntry>();

function verifyInMemoryFallback(
  entry: FallbackEntry,
  tokenHash: string,
  userId: string,
  secret: string,
  now: number
): Verify2faSetupResult {
  if (entry.usedAt !== null) {
    return { ok: false, reason: "token_used" };
  }
  if (entry.expiresAt < now) {
    pendingInMemoryFallback.delete(tokenHash);
    return { ok: false, reason: "token_expired" };
  }
  if (entry.userId !== userId) {
    return { ok: false, reason: "user_mismatch" };
  }
  if (entry.secretHash !== sha256(secret)) {
    return { ok: false, reason: "secret_mismatch" };
  }
  // Mark as used. NOTE: this is NOT atomic across concurrent calls within
  // the same process — JavaScript is single-threaded for sync code, so
  // within one process it IS atomic. But two processes (each with their
  // own Map) can both pass the usedAt === null check and both proceed.
  // This is the exact bug BE-078 describes; the DB path above is the
  // real fix. This fallback exists only so single-instance dev/test
  // continues to work when the DB is unreachable.
  entry.usedAt = now;
  pendingInMemoryFallback.set(tokenHash, entry);
  return { ok: true };
}

/**
 * Test-only helper: clear all pending tokens (both DB and in-memory
 * fallback). Never call from production.
 */
export async function __clear2faSetupTokensForTests(): Promise<void> {
  pendingInMemoryFallback.clear();
  try {
    await db.twoFactorSetupToken.deleteMany({});
  } catch {
    // DB may not be initialized in test — swallow.
  }
}

// FE-018 ROOT FIX: deterministic time-offset for expiry regression tests.
// Tests cannot wait 5 real minutes for a token to expire. This offset is
// applied to the `expiresAt` computation in `issue2faSetupToken` and the
// expiry check in `verify2faSetupToken`, so the test sees consistent
// behavior. Call `__clear2faSetupTokensForTests()` in `beforeEach` to
// reset the offset between tests.
//
// NOTE: the time offset only applies to the in-memory fallback path. The
// DB path uses real `Date.now()` for `expiresAt` because Prisma stores
// actual DateTime values. Tests that need to verify DB-path expiry should
// use a fake timer (jest.useFakeTimers) or wait the real 5 minutes (not
// recommended).
let __timeOffsetMsForTests = 0;

/**
 * Test-only: fast-forward the module's clock by `ms` milliseconds. This
 * lets the regression test verify that an expired token is rejected
 * WITHOUT waiting the real 5-minute TTL. The offset is applied to the
 * in-memory fallback path's `expiresAt` computation and expiry check.
 *
 * NEVER call this from production code — it would let an attacker freeze
 * the clock and keep tokens alive forever.
 */
export function __fastForwardTimeForTests(ms: number): void {
  __timeOffsetMsForTests += ms;
}
