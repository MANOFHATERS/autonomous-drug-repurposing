import { NextRequest, NextResponse } from "next/server";
import { requireRoleOrSend, badRequest, internalError, requireCsrfOrSend } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";

/**
 * ROOT FIX for FE-020 (billing/subscription accepts any authenticated user).
 *
 * Previously: GET and POST only called `requireAuth()`, so a `viewer` could
 * read the org's subscription details and — worse — `POST { planId:
 * "enterprise" }` to change the org's plan. This is a privilege escalation
 * with real financial consequences (invoice generation in `changePlan`).
 *
 * ROOT FIX: both endpoints now require `owner`, `admin`, or `billing`.
 * The `billing` role is the standard finance-team role; `admin` and
 * `owner` retain oversight. `viewer`, `researcher`, `pi`, `developer`,
 * `business-dev`, and `data-scientist` cannot read or change the
 * subscription.
 *
 * RBAC matrix (see src/lib/rbac.ts): the `subscription`, `usage`,
 * `invoices`, and `deals` sidebar sections are already gated to
 * `["owner", "admin", "billing"]` — this route now matches that gate.
 */

export async function GET() {
  const auth = await requireRoleOrSend("owner", "admin", "billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  const sub = await getOrganizationSubscription(auth.user.orgId);
  return NextResponse.json({ subscription: sub, plans: PLANS });
}

export async function POST(req: NextRequest) {
  // CSRF — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const auth = await requireRoleOrSend("owner", "admin", "billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  let body: { planId: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.planId) return badRequest("planId is required");
  if (!PLANS.find((p) => p.id === body.planId)) {
    return badRequest(`Unknown planId: ${body.planId}`);
  }
  try {
    await changePlan(auth.user.orgId, body.planId);
    return NextResponse.json({ ok: true });
  } catch (e: any) {
    return internalError(e.message);
  }
}
