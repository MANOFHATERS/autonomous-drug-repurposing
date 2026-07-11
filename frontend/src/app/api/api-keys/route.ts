import { NextRequest, NextResponse } from "next/server";
<<<<<<< HEAD
import { requireRoleOrSend, badRequest, requireCsrfOrSend } from "@/lib/api-helpers";
import { issueApiKey, listApiKeys } from "@/lib/services/api-keys";

/**
 * ROOT FIX for FE-021 (api-keys accepts any authenticated user — no role check).
 *
 * Previously: GET and POST only called `requireAuth()`, so a `viewer` could
 * issue API keys (which expose the rawKey once) and list every API key in
 * the org. API keys grant programmatic access to the developer platform,
 * so this is a privilege escalation.
 *
 * ROOT FIX: both endpoints now require `developer`, `admin`, or `owner`.
 * The `developer` role is the standard programmatic-access role; `admin`
 * and `owner` retain oversight. `viewer`, `researcher`, `pi`,
 * `business-dev`, and `billing` cannot issue or list API keys.
 *
 * RBAC matrix (see src/lib/rbac.ts): the `api-keys` sidebar section is
 * already gated to `["developer", "admin", "owner"]` — this route now
 * matches that gate.
 */

export async function GET() {
  const auth = await requireRoleOrSend("developer", "admin", "owner");
=======
import { requireAuthRole, badRequest } from "@/lib/api-helpers";
import { issueApiKey, listApiKeys } from "@/lib/services/api-keys";

/** FE-010 ROOT FIX: API key management restricted to developer/admin/owner. */
export async function GET() {
  const auth = await requireAuthRole("developer");
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");
  // FE-022 partial: list only the calling user's keys unless they are
  // admin/owner, who can see all keys in the org.
  const includeAll = auth.user.role === "admin" || auth.user.role === "owner";
  const keys = await listApiKeys(auth.user.orgId, includeAll ? undefined : auth.user.userId);
  return NextResponse.json({ items: keys });
}

export async function POST(req: NextRequest) {
<<<<<<< HEAD
  // CSRF — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const auth = await requireRoleOrSend("developer", "admin", "owner");
=======
  const auth = await requireAuthRole("developer");
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
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
  if (body.name.trim().length > 100) {
    return badRequest("Key name must be 100 characters or fewer");
  }
  const created = await issueApiKey(auth.user.orgId, auth.user.userId, body.name.trim());
  return NextResponse.json(created, { status: 201 });
}
