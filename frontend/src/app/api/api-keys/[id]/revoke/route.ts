import { NextRequest, NextResponse } from "next/server";
import { requireRoleOrSend, notFound, requireCsrfOrSend } from "@/lib/api-helpers";
import { revokeApiKey } from "@/lib/services/api-keys";

/**
 * ROOT FIX for FE-022 (API key revocation not scoped to owning user).
 *
 * A non-admin caller can now revoke ONLY their own keys. Admin/owner can
 * revoke any key in the org.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  // CSRF — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const auth = await requireRoleOrSend("developer", "admin", "owner");
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) {
    return NextResponse.json({ error: "bad_request", message: "No active organization" }, { status: 400 });
  }
  const { id } = await params;
  // Non-admin/owner callers can only revoke their own keys.
  const ownerFilter = (auth.user.role === "admin" || auth.user.role === "owner")
    ? undefined
    : auth.user.userId;
  const ok = await revokeApiKey(auth.user.orgId, id, ownerFilter);
  if (!ok) return notFound("API key not found, already revoked, or not owned by you");
  return NextResponse.json({ ok: true });
}
