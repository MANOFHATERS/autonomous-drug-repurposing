import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser, verifyPassword } from "@/lib/auth/server";
import { verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import {
  checkTotpRateLimit,
  recordFailedTotp,
  clearTotpAttempts,
} from "@/lib/auth/rate-limit";

/**
 * POST /api/auth/2fa/disable
 * Body: { currentPassword: string, totpCode: string }
 *
 * FE-005 ROOT FIX: The previous implementation disabled 2FA with NO
 * re-authentication — it just trusted the authenticated session. The
 * code comment even admitted it: "for this development build we trust
 * the authenticated session."
 *
 * If an attacker steals a session cookie (XSS, network sniffing on HTTP,
 * dev-tools on a shared computer), they can disable 2FA in one POST and
 * then the account is password-only — compounding the FE-004 2FA bypass.
 *
 * Root fix: require BOTH the current password AND a valid current TOTP
 * code before clearing mfaSecret/mfaEnabled. This means:
 *   - A stolen session cookie alone is NOT enough to disable 2FA.
 *   - A stolen password alone is NOT enough (attacker still needs TOTP).
 *   - Only someone with BOTH factors can disable 2FA.
 *
 * Edge case: if the user has lost their authenticator device, they must
 * go through an admin-mediated recovery flow (out of scope here — that's
 * a separate /api/auth/2fa/recover endpoint with its own audit trail).
 *
 * FE-012 ROOT FIX (Team Member 14): This endpoint accepted a 6-digit
 * TOTP code with NO rate limit. A 6-digit code has only 1,000,000
 * combinations; at 1000 req/s an attacker with a phished password could
 * brute-force the entire keyspace in ~17 minutes — well within the
 * 30-second TOTP window (the attacker simply retries with the next
 * window if the current one expires before they sweep it). The fix
 * wires in the EXISTING `checkTotpRateLimit` + `recordFailedTotp`
 * primitives from `rate-limit.ts` — 5 wrong codes per 5 minutes locks
 * 2FA for 15 minutes. This mirrors the protection already present on
 * `/api/auth/2fa/login-verify` (FE-003 root fix). The primitive was
 * defined but NEVER called from this route — closing that gap.
 *
 * FE-013 ROOT FIX (Team Member 14): This endpoint called `verifyTotp()`
 * which checks if the TOTP code is valid for the current ±30s window
 * but does NOT track whether that code has already been used. An
 * attacker who intercepts a single code (e.g. via XSS reading the
 * response body, or a malicious reverse-proxy phishing site) could
 * reuse it within the 30-second window to disable 2FA. The lib has
 * had `verifyTotpWithReplayCheck()` since the FE-033 root fix — it
 * rejects any code whose counter is <= the user's `lastTotpCounter` —
 * but this route never called it. The fix replaces `verifyTotp()`
 * with `verifyTotpWithReplayCheck()` and atomically advances
 * `lastTotpCounter` via `updateMany` with `where: { lastTotpCounter:
 * { lt: counter } }` so concurrent verifications of the same code
 * cannot both succeed (standard RFC 6238 §5.2 race prevention).
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", message: "Authentication required" },
      { status: 401 }
    );
  }

  let body: { currentPassword?: string; totpCode?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const currentPassword = body.currentPassword || "";
  const totpCode = (body.totpCode || "").trim();
  if (!currentPassword) return badRequest("currentPassword is required to disable 2FA");
  if (!/^\d{6}$/.test(totpCode)) return badRequest("A 6-digit TOTP code is required");

  try {
    const dbUser = await db.user.findUnique({
      where: { id: user.userId },
      // FE-013: select lastTotpCounter so we can pass it to
      // verifyTotpWithReplayCheck and atomically advance it after success.
      select: { id: true, email: true, passwordHash: true, mfaEnabled: true, mfaSecret: true, lastTotpCounter: true },
    });
    if (!dbUser) {
      return NextResponse.json(
        { error: "not_found", message: "User not found" },
        { status: 404 }
      );
    }
    if (!dbUser.mfaEnabled || !dbUser.mfaSecret) {
      return NextResponse.json(
        { error: "mfa_not_enabled", message: "2FA is not enabled on this account." },
        { status: 400 }
      );
    }

    // Verify current password.
    const passwordOk = await verifyPassword(currentPassword, dbUser.passwordHash);
    if (!passwordOk) {
      return NextResponse.json(
        { error: "invalid_password", message: "Current password is incorrect." },
        { status: 403 }
      );
    }

    // FE-012 ROOT FIX: Per-user TOTP brute-force gate. The 6-digit TOTP
    // keyspace is only 1M codes; without a per-user attempt cap an
    // attacker with a phished password can sweep it in ~17 minutes at
    // 1000 req/s. The primitive existed in rate-limit.ts but was never
    // wired into this route. We check BEFORE the TOTP verify so a locked
    // user cannot burn attempts, and we RECORD a failure AFTER a wrong
    // code so the counter actually advances.
    const totpLock = checkTotpRateLimit(user.userId);
    if (totpLock.locked) {
      await writeAuditLog({
        user,
        action: "2fa_disable_locked",
        resource: user.userId,
        metadata: { retryAfterSeconds: totpLock.retryAfterSeconds },
      });
      return NextResponse.json(
        {
          error: "totp_locked",
          message: `Too many incorrect 2FA codes. Try again in ${Math.ceil(totpLock.retryAfterSeconds / 60)} minute(s).`,
          retryAfterSeconds: totpLock.retryAfterSeconds,
        },
        { status: 429, headers: { "Retry-After": String(totpLock.retryAfterSeconds) } }
      );
    }

    // FE-013 ROOT FIX: Replay-protected TOTP verification. The previous
    // call to `verifyTotp()` accepted any code valid for the ±30s window
    // — including codes already used to log in. An XSS attacker who
    // intercepted a code could disable 2FA with it within the same
    // 30-second window. `verifyTotpWithReplayCheck` rejects any code
    // whose counter is <= the user's stored `lastTotpCounter`.
    const totpResult = verifyTotpWithReplayCheck(
      dbUser.mfaSecret,
      totpCode,
      dbUser.lastTotpCounter
    );
    if (!totpResult.ok) {
      // FE-012: record the failed attempt so the lockout counter
      // advances. This is SEPARATE from the password failedLoginCount
      // — both must trip independently.
      const afterFail = recordFailedTotp(user.userId);
      await writeAuditLog({
        user,
        action: totpResult.reason === "replayed" ? "2fa_disable_code_replayed" : "2fa_disable_failed",
        resource: user.userId,
        metadata: {
          reason: totpResult.reason,
          attemptsRemaining: afterFail.attemptsRemaining,
        },
      });
      if (afterFail.locked) {
        return NextResponse.json(
          {
            error: "totp_locked",
            message: `Invalid 6-digit code. 2FA is now locked for ${Math.ceil(afterFail.retryAfterSeconds / 60)} minute(s) due to too many failed 2FA attempts.`,
            attemptsRemaining: 0,
            retryAfterSeconds: afterFail.retryAfterSeconds,
          },
          { status: 429, headers: { "Retry-After": String(afterFail.retryAfterSeconds) } }
        );
      }
      const message =
        totpResult.reason === "replayed"
          ? "This code has already been used. Wait for the next 30-second window."
          : `Invalid 6-digit code. ${afterFail.attemptsRemaining} attempt(s) remaining before 2FA is locked.`;
      return NextResponse.json(
        {
          error: totpResult.reason === "replayed" ? "code_replayed" : "invalid_code",
          message,
          attemptsRemaining: afterFail.attemptsRemaining,
        },
        { status: 403 }
      );
    }

    // FE-013: Atomically advance lastTotpCounter so the same code cannot
    // be replayed. The `updateMany` with `where: { lastTotpCounter: { lt:
    // counter } }` ensures that if two concurrent verifications of the
    // same code race, only one persists the update — the other is a
    // no-op. This is the standard RFC 6238 §5.2 replay-protection race
    // prevention, mirroring the pattern in /api/auth/2fa/login-verify.
    if (dbUser.lastTotpCounter === null) {
      await db.user.update({
        where: { id: user.userId },
        data: { lastTotpCounter: totpResult.counter },
      });
    } else {
      await db.user.updateMany({
        where: { id: user.userId, lastTotpCounter: { lt: totpResult.counter } },
        data: { lastTotpCounter: totpResult.counter },
      });
    }

    // FE-012: Successful verification — clear the per-user TOTP attempt
    // counter so a user who eventually gets it right doesn't carry a
    // partial lock forward.
    clearTotpAttempts(user.userId);

    // Both factors verified — safe to disable.
    await db.user.update({
      where: { id: user.userId },
      data: { mfaSecret: null, mfaEnabled: false, lastTotpCounter: null },
    });
    // FE-034: 2FA disable is security-critical — must be auditable.
    const audit = await writeAuditLog({
      user,
      action: "2fa_disable",
      resource: user.userId,
      critical: true,
    });
    if (!audit.ok) {
      // The 2FA WAS disabled, but the audit log failed. This is a
      // security incident — re-enable 2FA (forcing the user to set it
      // up again) and return an error.
      await db.user.update({
        where: { id: user.userId },
        data: { mfaEnabled: false }, // secret already cleared; user must re-enroll
      });
      return internalError("2FA disabled but audit log failed. Please re-enable 2FA from your security settings.");
    }
    return NextResponse.json({ ok: true, enabled: false });
  } catch (e) {
    console.error("2FA disable failed:", e);
    return internalError("Failed to disable 2FA.");
  }
}
