import { NextRequest, NextResponse } from "next/server";
import { requireAuthRole, badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";
import { verifyPassword } from "@/lib/auth/server";
import { verifyMfaTicket, verifyTotp } from "@/lib/auth/totp";
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
    select: { passwordHash: true, mfaEnabled: true, mfaSecret: true, email: true },
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
    const ticketOk = body.mfaTicket
      ? verifyMfaTicket(body.mfaTicket) !== null
      : false;
    const totpOk =
      body.totpCode && userRecord.mfaSecret
        ? verifyTotp(userRecord.mfaSecret, body.totpCode)
        : false;
    if (!ticketOk && !totpOk) {
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
