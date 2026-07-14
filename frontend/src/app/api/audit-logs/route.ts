import { NextRequest, NextResponse } from "next/server";
import { requireAdmin } from "@/lib/api-helpers";
import { db } from "@/lib/db";

/**
 * GET /api/audit-logs
 *
 * FE-005 ROOT FIX: Cross-tenant data leak.
 *
 * Previously this handler called db.auditLog.findMany with no
 * organizationId filter — an admin of Org A could read every audit log
 * system-wide (logins by users in Org B, billing actions in Org C, etc.).
 * The AuditLog model itself had no organizationId column at all.
 *
 * Fix:
 *   1. AuditLog now has an `organizationId` column (see Prisma schema).
 *   2. writeAuditLog stamps every row with the actor's orgId.
 *   3. This route filters by orgId for everyone EXCEPT the platform owner
 *      role, who can see system-wide logs (for incident response).
 *
 * Owner bypass is intentional: the platform owner is the superuser that
 * runs the SaaS itself and needs cross-tenant visibility for security
 * investigations. Admins are scoped to their own org.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;

  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "100", 10);
  const action = req.nextUrl.searchParams.get("action");

  // BE-002 ROOT FIX: Only platformOwner role can read cross-tenant logs
  // (they are the true platform superuser). The org "owner" role is scoped
  // to their own org like admin — they do NOT get system-wide access.
  const isPlatformOwner = auth.user.role === "platformOwner";
  const orgFilter = isPlatformOwner
    ? {}
    : { organizationId: auth.user.orgId ?? "__NO_ORG__" };

  const where = {
    ...orgFilter,
    ...(action ? { action } : {}),
  };

  const logs = await db.auditLog.findMany({
    where,
    orderBy: { createdAt: "desc" },
    take: limit,
  });
  const total = await db.auditLog.count({ where });
  return NextResponse.json({
    items: logs,
    total,
    // Tell the client whether this view is org-scoped or system-wide.
    scope: isPlatformOwner ? "system" : "organization",
    organizationId: auth.user.orgId ?? null,
  });
}
