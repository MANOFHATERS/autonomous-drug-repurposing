import { NextRequest, NextResponse } from "next/server";
import { requireAuthRole, badRequest, internalError } from "@/lib/api-helpers";
import { changePlan, getOrganizationSubscription, PLANS } from "@/lib/services/billing";

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
  const auth = await requireAuthRole("billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  const sub = await getOrganizationSubscription(auth.user.orgId);
  return NextResponse.json({ subscription: sub, plans: PLANS });
}

export async function POST(req: NextRequest) {
  const auth = await requireAuthRole("billing");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  let body: { planId: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.planId) return badRequest("planId is required");
  try {
    await changePlan(auth.user.orgId, body.planId);
    return NextResponse.json({ ok: true });
  } catch (e: any) {
    return internalError(e.message);
  }
}
