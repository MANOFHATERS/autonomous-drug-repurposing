import { NextRequest, NextResponse } from "next/server";
import { requireAuthRole, badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";
import { verifyPassword } from "@/lib/auth/server";
import { verifyMfaTicket, verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import {
  checkTotpRateLimit,
  recordFailedTotp,
  clearTotpAttempts,
} from "@/lib/auth/rate-limit";
import { db } from "@/lib/db";

/**
 * FE-020 ROOT FIX: Previously used requireAuth (any authenticated user),
 * NOT role-restricted. A viewer or researcher could change the org's
 * subscription plan — including upgrading to enterprise (which generates
 * an invoice) or downgrading to free (denial-of-service mid-research).
 *
 * The RBAC file (lib/rbac.ts) lists subscription: ["owner", "admin", "billing"]
 * but that was only enforced on the UI sidebar, not the API. The API is the
 * real security boundary — UI filtering is just UX.
 *
 * Root fix: requireAuthRole("billing", "admin", "owner") — admin and owner
 * are implicitly allowed by the helper's superuser bypass.
 *
 * FE-039 ROOT FIX: Plan changes are now a financial action and require
 * RE-AUTHENTICATION. A stolen session cookie (e.g. via XSS, leaked logs,
 * shared machine) used to be enough to upgrade to enterprise (triggering
 * a sales-workflow invoice) or downgrade to free (disrupting active
 * research by enforcing the 10 evidence packages / month limit). The fix
 * mirrors the OWASP "step-up authentication" guidance for high-impact
 * actions: the caller must POST `currentPassword` (verified via
 * verifyPassword against the user's stored passwordHash) AND, if the user
 * has 2FA enabled, a fresh `mfaTicket` (issued by /api/auth/2fa/begin
 * after a successful TOTP verification within the last 5 minutes) or a
 * direct `totpCode` (verified live against the user's mfaSecret). All
 * plan changes — successful or failed — are written to the audit log at
 * high severity.
 *
 * FE-014 ROOT FIX (Team Member 14): The previous FE-039 fix added the
 * 2FA challenge for plan changes but used `verifyTotp()` (no replay
 * protection) and did NOT call `checkTotpRateLimit` / `recordFailedTotp`.
 * A 6-digit TOTP has 1M combinations; at 1000 req/s an attacker with a
 * phished password could brute-force the code in ~17 minutes and
 * downgrade the org to free tier (DoS) or upgrade to enterprise
 * (triggering invoices). An intercepted code could also be replayed
 * within the 30s window. The fix wires in the SAME primitives already
 * used by /api/auth/2fa/login-verify and (after FE-012) /api/auth/2fa/disable:
 *   1. `checkTotpRateLimit` BEFORE the TOTP verify — 5 wrong codes per
 *      5 minutes locks 2FA for 15 minutes.
 *   2. `verifyTotpWithReplayCheck` INSTEAD OF `verifyTotp` — rejects
 *      codes whose counter is <= the user's stored `lastTotpCounter`.
 *   3. `recordFailedTotp` AFTER a wrong code — advances the lockout
 *      counter so the gate actually trips.
 *   4. Atomic `updateMany` with `where: { lastTotpCounter: { lt:
 *      counter } }` after success — prevents concurrent replays.
 * The mfaTicket path is unaffected (the ticket is one-time-use JWT with
 * a 5-minute expiry, set by /api/auth/2fa/login-verify which already
 * does replay-protected TOTP verification).
 */
export async function GET() {
  const auth = await requireAuthRole("billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  const sub = await getOrganizationSubscription(auth.user.orgId);
  return NextResponse.json({ subscription: sub, plans: PLANS });
}

export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuthRole("billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");

  let body: {
    planId: string;
    /** FE-039: current password (re-auth) — required for plan changes. */
    currentPassword?: string;
    /** FE-039: fresh TOTP code, accepted iff user has mfaEnabled. */
    totpCode?: string;
    /** FE-039: OR a fresh mfaTicket JWT issued after recent TOTP verify. */
    mfaTicket?: string;
  };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.planId) return badRequest("planId is required");
  if (!body.currentPassword) {
    return badRequest("currentPassword is required to change the billing plan (re-authentication)");
  }

  // FE-039 STEP 1: re-verify the user's password.
  const userRecord = await db.user.findUnique({
    where: { id: auth.user.userId },
    // FE-014: select lastTotpCounter so we can pass it to
    // verifyTotpWithReplayCheck and atomically advance it after success.
    select: { passwordHash: true, mfaEnabled: true, mfaSecret: true, email: true, lastTotpCounter: true },
  });
  if (!userRecord) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const passwordOk = await verifyPassword(body.currentPassword, userRecord.passwordHash);
  if (!passwordOk) {
    await writeAuditLog({
      user: auth.user,
      action: "billing_plan_change_reauth_failed",
      resource: `subscription:${auth.user.orgId}`,
      metadata: { planId: body.planId, reason: "invalid_password" },
    });
    return NextResponse.json(
      { error: "forbidden", message: "Current password is incorrect." },
      { status: 403 }
    );
  }

  // FE-039 STEP 2: if the user has 2FA enabled, require a fresh TOTP code
  // OR a fresh mfaTicket. This is the "2FA challenge" for the financial action.
  if (userRecord.mfaEnabled) {
    // FE-014 ROOT FIX: If the caller supplied a totpCode (rather than an
    // mfaTicket), enforce the per-user TOTP brute-force gate. The
    // mfaTicket path is exempt because the ticket is already one-time-use
    // and was issued by /api/auth/2fa/login-verify which ALREADY ran the
    // TOTP rate-limited + replay-protected check when the ticket was
    // minted. Charging the totpCode path against the same limiter closes
    // the brute-force hole that the FE-039 fix left open.
    if (body.totpCode && userRecord.mfaSecret) {
      const totpLock = checkTotpRateLimit(auth.user.userId);
      if (totpLock.locked) {
        await writeAuditLog({
          user: auth.user,
          action: "billing_plan_change_mfa_locked",
          resource: `subscription:${auth.user.orgId}`,
          metadata: { planId: body.planId, retryAfterSeconds: totpLock.retryAfterSeconds },
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

      // FE-014 ROOT FIX: Replay-protected TOTP verification. The previous
      // `verifyTotp()` call accepted any code valid for the ±30s window —
      // including codes already used elsewhere. `verifyTotpWithReplayCheck`
      // rejects any code whose counter is <= the user's stored
      // `lastTotpCounter`.
      const totpResult = verifyTotpWithReplayCheck(
        userRecord.mfaSecret,
        body.totpCode,
        userRecord.lastTotpCounter
      );
      if (!totpResult.ok) {
        const afterFail = recordFailedTotp(auth.user.userId);
        await writeAuditLog({
          user: auth.user,
          action:
            totpResult.reason === "replayed"
              ? "billing_plan_change_mfa_code_replayed"
              : "billing_plan_change_mfa_failed",
          resource: `subscription:${auth.user.orgId}`,
          metadata: {
            planId: body.planId,
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
            ? "This 2FA code has already been used. Wait for the next 30-second window."
            : `Invalid 2FA code. ${afterFail.attemptsRemaining} attempt(s) remaining before 2FA is locked.`;
        return NextResponse.json(
          {
            error: totpResult.reason === "replayed" ? "code_replayed" : "invalid_mfa",
            message,
            attemptsRemaining: afterFail.attemptsRemaining,
          },
          { status: 403 }
        );
      }

      // FE-014: Atomically advance lastTotpCounter so the same code
      // cannot be replayed on a second plan-change attempt.
      if (userRecord.lastTotpCounter === null) {
        await db.user.update({
          where: { id: auth.user.userId },
          data: { lastTotpCounter: totpResult.counter },
        });
      } else {
        await db.user.updateMany({
          where: { id: auth.user.userId, lastTotpCounter: { lt: totpResult.counter } },
          data: { lastTotpCounter: totpResult.counter },
        });
      }
      clearTotpAttempts(auth.user.userId);
      // TOTP verified — skip the mfaTicket path below.
    } else {
      const ticketOk = body.mfaTicket
        ? verifyMfaTicket(body.mfaTicket) !== null
        : false;
      if (!ticketOk) {
        await writeAuditLog({
          user: auth.user,
          action: "billing_plan_change_mfa_failed",
          resource: `subscription:${auth.user.orgId}`,
          metadata: { planId: body.planId, reason: "invalid_mfa" },
        });
        return NextResponse.json(
          { error: "forbidden", message: "A valid 2FA code (totpCode or mfaTicket) is required to change the billing plan." },
          { status: 403 }
        );
      }
    }
  }

  try {
    await changePlan(auth.user.orgId, body.planId);
    await writeAuditLog({
      user: auth.user,
      action: "billing_plan_change",
      resource: `subscription:${auth.user.orgId}`,
      metadata: { planId: body.planId },
    });
    return NextResponse.json({ ok: true });
  } catch (e: any) {
    return internalError(e.message);
  }
}
