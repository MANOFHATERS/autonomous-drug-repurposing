import { NextRequest, NextResponse } from "next/server";
import { requireAuthRole, badRequest, requireCsrfOrSend } from "@/lib/api-helpers";
import { issueApiKey, listApiKeys } from "@/lib/services/api-keys";

/**
 * FE-014 ROOT FIX: Previously GET /api/api-keys called `listApiKeys(orgId)`
 * with NO userId argument, so a developer could see EVERY other developer's
 * and admin's API key prefixes/names/last-used timestamps. The revoke
 * endpoint correctly passed userId for non-admins — the list endpoint was
 * inconsistent and leaked information.
 *
 * Root fix: pass the caller's userId as the owner filter UNLESS the caller
 * is an admin/owner (org-wide oversight). This matches the revoke endpoint's
 * posture exactly.
 *
 * FE-011: CSRF protection applied to POST.
 */
export async function GET() {
  const auth = await requireAuthRole("developer");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  // Non-admins only see their own keys. Admin/owner see all keys in the org.
  const ownerFilter =
    auth.user.role === "admin" || auth.user.role === "owner"
      ? undefined
      : auth.user.userId;
  const keys = await listApiKeys(auth.user.orgId, ownerFilter);
  return NextResponse.json({ items: keys });
}

export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuthRole("developer");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  let body: { name: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.name || body.name.trim().length < 1) {
    return badRequest("Key name is required");
  }
  const created = await issueApiKey(auth.user.orgId, auth.user.userId, body.name.trim());
  return NextResponse.json(created, { status: 201 });
}
