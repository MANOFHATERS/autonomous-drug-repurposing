import { NextRequest, NextResponse } from "next/server";
<<<<<<< HEAD
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
=======
import { requireAuthRole, badRequest, internalError } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs

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
 */
export async function GET() {
<<<<<<< HEAD
  const auth = await requireRoleOrSend("owner", "admin", "billing");
=======
  const auth = await requireAuthRole("billing");
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  const sub = await getOrganizationSubscription(auth.user.orgId);
  return NextResponse.json({ subscription: sub, plans: PLANS });
}

export async function POST(req: NextRequest) {
<<<<<<< HEAD
  // CSRF — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const auth = await requireRoleOrSend("owner", "admin", "billing");
=======
  const auth = await requireAuthRole("billing");
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
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
