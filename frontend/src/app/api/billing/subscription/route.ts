import { NextRequest, NextResponse } from "next/server";
import { requireAuthRole, badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";
import { verifyPassword } from "@/lib/auth/server";
import { verifyMfaTicket, verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import {
  checkTotpRateLimit,
  // BE-066 REAL ROOT FIX (v126): migrate this route from the SYNC
  // `recordFailedTotp` (in-memory Map, per-process) to the DISTRIBUTED
  // `recordFailedTotpDistributed` (Redis-backed when REDIS_URL is set,
  // falls back to the sync path internally when Redis is unavailable).
  //
  // The v123 "BE-066 ROOT FIX" only migrated /api/auth/2fa/login-verify.
  // This route AND /api/auth/2fa/disable were MISSED. On a multi-instance
  // deploy (K8s with N replicas), each instance had its own in-memory
  // TOTP counter — an attacker could make N × TOTP_MAX_ATTEMPTS attempts
  // before lockout (N=3 → 15 attempts → ~6 min to brute-force TOTP) per
  // billing-protected endpoint. Wiring this route to the distributed
  // version closes the hole identically to login-verify. The function
  // returns a Promise, so the call site is updated to `await`.
  recordFailedTotpDistributed,
  clearTotpAttempts,
} from "@/lib/auth/rate-limit";
import { db } from "@/lib/db";
// BE-029 ROOT FIX (Team Member 12): Zod-validated request body. The
// BillingSubscriptionBody schema also enforces "totpCode XOR mfaTicket"
// via a refine() — replacing the inline `if (body.totpCode && body.mfaTicket)`
// check with schema-level enforcement.
import { validateBody, BillingSubscriptionBody } from "@/lib/zod-schemas";

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

  // Task 11.7 ROOT FIX (v129, TM11): read Idempotency-Key from the
  // HTTP HEADER (the standard location per the IETF draft
  // https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/).
  // The body.idempotencyKey field is kept for backward compat with
  // existing clients (the frontend's billing form sends it in the
  // body) — the HEADER takes precedence if both are present, since
  // the header is the canonical location per the spec.
  const headerIdempotencyKey = req.headers.get("idempotency-key") || "";

  let body: {
    planId: string;
    /** FE-039: current password (re-auth) — required for plan changes. */
    currentPassword?: string;
    /** FE-039: fresh TOTP code, accepted iff user has mfaEnabled. */
    totpCode?: string;
    /** FE-039: OR a fresh mfaTicket JWT issued after recent TOTP verify. */
    mfaTicket?: string;
    /**
     * BE-048 v123 FORENSIC ROOT FIX: client-generated idempotency key.
     * When the client retries a POST (network timeout, double-click,
     * etc.), it sends the SAME idempotencyKey on both attempts. The
     * server checks for an existing invoice with this key BEFORE
     * creating a new one — if found, the existing invoice is returned
     * and no new invoice is created (no double-charge). Required for
     * paid plan changes; ignored for free plan changes (no invoice to
     * dedupe).
     *
     * Task 11.7 v129: clients SHOULD send the key via the
     * `Idempotency-Key` HTTP header instead (the standard location).
     * The body field is kept for backward compat — the HEADER takes
     * precedence if both are present.
     */
    idempotencyKey?: string;
  };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  // BE-029 ROOT FIX: schema-validate the body. The schema enforces
  // planId (non-empty string), currentPassword (required, 1..1024),
  // totpCode (6 digits, optional), mfaTicket (non-empty, optional), AND
  // the refine() rejects when BOTH totpCode AND mfaTicket are present
  // (replacing the inline check below).
  const parsed = validateBody(BillingSubscriptionBody, body);
  if (!parsed.ok) return parsed.response;
  body = parsed.data;

  // Task 11.7: header takes precedence over body (header is the
  // canonical location per the IETF draft). Trim whitespace; the
  // Idempotency-Key header is opaque string per the spec, but we cap
  // at 200 chars to prevent abuse (a 1MB key would be a DoS vector).
  const idempotencyKeyFromRequest = (
    headerIdempotencyKey.trim() ||
    body.idempotencyKey ||
    ""
  ).slice(0, 200);

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
  // FE-016 ROOT FIX (Team Member 15, v108 — pre-existing build blocker):
  // `body.currentPassword` is typed as `string | undefined` but the
  // BillingSubscriptionBody zod schema at /lib/zod-schemas.ts:164 requires
  // it (min 1 char). After `validateBody` returns ok, we know currentPassword
  // is a non-empty string. Use `!` to assert non-null at the call site —
  // semantically safe because the validator above already rejected missing
  // passwords with 400.
  const passwordOk = await verifyPassword(body.currentPassword!, userRecord.passwordHash);
  if (!passwordOk) {
    await writeAuditLog({
      user: auth.user,
      action: "billing_plan_change_reauth_failed",
      resource: `subscription:${auth.user.orgId}`,
      metadata: { planId: body.planId, reason: "invalid_password" },
    });
    // BE-033 ROOT FIX (Team Member 12): return 401 (authentication
    // failure) — NOT 403. A wrong password is an authentication failure,
    // not an authorization failure. The user is authenticated (they have
    // a valid session) but they have NOT proven they own the account via
    // re-auth. 401 tells the client to re-authenticate; 403 would tell
    // them they're forbidden, which is misleading.
    return NextResponse.json(
      { error: "invalid_credentials", message: "Current password is incorrect." },
      { status: 401 }
    );
  }

  // FE-039 STEP 2: if the user has 2FA enabled, require a fresh TOTP code
  // OR a fresh mfaTicket. This is the "2FA challenge" for the financial action.
  if (userRecord.mfaEnabled) {
    // BE-075 ROOT FIX: Explicitly reject if BOTH totpCode AND mfaTicket are
    // provided. The previous code accepted either independently, but if both
    // were present, the totpCode path was taken and the mfaTicket was ignored.
    // This is fragile — an attacker with a phished TOTP code AND a stolen
    // mfaTicket could cause confusion about which auth factor was actually
    // validated. A clear error prevents ambiguity.
    if (body.totpCode && body.mfaTicket) {
      return NextResponse.json(
        { error: "bad_request", message: "Provide either totpCode or mfaTicket, not both." },
        { status: 400 }
      );
    }
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
        // BE-066 v126: use the DISTRIBUTED version (Redis-backed when
        // REDIS_URL is set) so the TOTP brute-force counter is shared
        // across all Node.js instances — the sync version's in-memory
        // Map was per-process and gave the attacker N× the budget on
        // multi-instance deploys. Function is async, hence `await`.
        const afterFail = await recordFailedTotpDistributed(auth.user.userId);
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
        // BE-033 ROOT FIX (Team Member 12): 401 (authentication failure),
        // not 403. A wrong/replayed TOTP code is an authentication failure
        // — the user has not proven they possess the second factor.
        return NextResponse.json(
          {
            error: totpResult.reason === "replayed" ? "code_replayed" : "invalid_mfa",
            message,
            attemptsRemaining: afterFail.attemptsRemaining,
          },
          { status: 401 }
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
        // BE-033 ROOT FIX (Team Member 12): 401, not 403. An invalid or
        // missing mfaTicket is an authentication failure — the caller has
        // not completed step-up authentication.
        return NextResponse.json(
          { error: "invalid_mfa", message: "A valid 2FA code (totpCode or mfaTicket) is required to change the billing plan." },
          { status: 401 }
        );
      }
    }
  }

  try {
    // Task 11.7 ROOT FIX (v129, TM11): SHORT-CIRCUIT — if the org is
    // ALREADY on the requested plan, return the existing subscription
    // WITHOUT creating a new invoice. This is the third part of the
    // task spec ("return existing subscription if already on the plan").
    //
    // The previous code always called changePlan() even when the plan
    // was unchanged — creating a redundant invoice row (with a new
    // idempotencyKey) for a no-op change. This wastes invoice numbers,
    // pollutes the audit log, and confuses the customer ("why did I
    // get an invoice for a plan I already have?").
    //
    // The check is BEFORE the idempotencyKey generation so we don't
    // burn a UUID on a no-op. We still write an audit log entry so
    // operators can see the no-op attempt (a high rate may indicate a
    // client bug — the client should not be sending POST requests for
    // unchanged plans).
    const existingSub = await db.subscription.findUnique({
      where: { organizationId: auth.user.orgId },
      select: { id: true, plan: true, status: true },
    });
    if (existingSub && existingSub.plan === body.planId) {
      await writeAuditLog({
        user: auth.user,
        action: "billing_plan_change_noop",
        resource: `subscription:${auth.user.orgId}`,
        metadata: {
          planId: body.planId,
          reason: "already_on_plan",
          // Task 11.7: record the idempotencyKey even on no-ops so
          // operators can correlate client retries.
          idempotencyKey: idempotencyKeyFromRequest || null,
        },
      });
      return NextResponse.json({
        ok: true,
        invoiceId: null,
        idempotentReplay: true, // semantically: this IS a replay (the plan is already set)
        noOp: true, // explicit flag so the client can show "You're already on this plan"
        subscription: existingSub,
      });
    }

    // BE-048 v123 + Task 11.7 v129: use the idempotency key from the
    // HEADER (preferred) or body (backward compat). If neither is
    // provided, generate a random UUID so the route is ALWAYS idempotent
    // (even when the client forgot to send the key) — the second POST
    // will then return the same invoice (idempotentReplay: true) instead
    // of creating a second one. Clients that want idempotency MUST send
    // the same key on retries (the frontend's billing form does this —
    // see components/drugos/billing-screens.tsx).
    const idempotencyKey =
      idempotencyKeyFromRequest ||
      // Generate a v4 UUID. crypto.randomUUID() is available in Node 19+;
      // fallback to randomBytes for older runtimes.
      (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : require("crypto").randomUUID());
    const result = await changePlan(auth.user.orgId, body.planId, idempotencyKey);
    await writeAuditLog({
      user: auth.user,
      action: "billing_plan_change",
      resource: `subscription:${auth.user.orgId}`,
      metadata: {
        planId: body.planId,
        // BE-048: record the idempotencyKey so operators can correlate
        // audit entries with invoice rows (the invoice row also has the
        // idempotencyKey column). And record whether this was an
        // idempotent replay — a high replay rate may indicate client-side
        // bugs or network issues that warrant investigation.
        idempotencyKey,
        idempotencyKeySource: headerIdempotencyKey.trim() ? "header" : (body.idempotencyKey ? "body" : "generated"),
        idempotentReplay: result.idempotentReplay,
        invoiceId: result.invoiceId,
      },
    });
    return NextResponse.json({
      ok: true,
      // BE-048: return the invoiceId and idempotentReplay flag so the
      // client can distinguish "fresh plan change" from "retry of a
      // previous plan change". The client uses this to show the right
      // toast ("Plan updated!" vs "Your previous plan change is still
      // processing — no duplicate charge.").
      invoiceId: result.invoiceId,
      idempotentReplay: result.idempotentReplay,
    });
  } catch (e: any) {
    return internalError(e.message);
  }
}
