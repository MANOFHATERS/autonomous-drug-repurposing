import { NextRequest, NextResponse } from "next/server";
import { requireAdmin, isPlatformSuperuser } from "@/lib/api-helpers";
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
 *   3. This route filters by orgId for everyone EXCEPT the platform
 *      platformOwner role, who can see system-wide logs (for incident
 *      response).
 *
 * BE-002 ROOT FIX: previously, the `owner` role was treated as the
 * platform superuser — any user promoted to `owner` could read every
 * audit log across all tenants. This was a multi-tenant data breach.
 * The fix introduces a distinct `platformOwner` role that is settable
 * ONLY via direct DB access (no API route can grant it). The `owner`
 * role is now org-scoped just like `admin`.
 *
 * platformOwner bypass is intentional: the platformOwner is the SaaS
 * operator's staff and needs cross-tenant visibility for security
 * investigations. Admins and org owners are scoped to their own org.
 *
 * Query params:
 *   - limit: max rows to return (default 100, capped at 1000).
 *   - action: filter by audit action (e.g. "login", "billing_change").
 *   - dead_letter: if "true", return dead-letter entries instead of
 *     primary audit logs (BE-003). Only platformOwner can access this —
 *     dead-letter entries may contain cross-tenant error details.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;

  const limit = Math.min(parseInt(req.nextUrl.searchParams.get("limit") || "100", 10), 1000);
  const action = req.nextUrl.searchParams.get("action");
  const deadLetter = req.nextUrl.searchParams.get("dead_letter") === "true";

  // BE-002: only `platformOwner` bypasses org scoping. `owner` and `admin`
  // are both org-scoped now. This closes the multi-tenant data breach.
  const isSuperuser = isPlatformSuperuser(auth.user);

  // BE-003: dead-letter entries are ONLY visible to platformOwner. They
  // may contain cross-tenant error details (e.g. the userId of a user in
  // another org whose audit log write failed). Org-scoped admins should
  // NOT see another org's failed audit writes.
  if (deadLetter && !isSuperuser) {
    return NextResponse.json(
      {
        error: "forbidden",
        message:
          "Dead-letter audit entries are only visible to the platformOwner role. " +
          "Contact your SaaS operator if you need to inspect dead-letter entries.",
      },
      { status: 403 }
    );
  }

  // Build the where-clause. platformOwner is the only role that can read
  // cross-tenant logs (they are the platform superuser). Everyone else
  // (admin, owner, billing, etc.) is restricted to their own org.
  const orgFilter = isSuperuser
    ? {}
    : { organizationId: auth.user.orgId ?? "__NO_ORG__" };

  const where = {
    ...orgFilter,
    ...(action ? { action } : {}),
  };

  if (deadLetter) {
    // BE-003: return dead-letter entries. platformOwner only.
    const logs = await db.auditLogDeadLetter.findMany({
      where,
      orderBy: { createdAt: "desc" },
      take: limit,
    });
    const total = await db.auditLogDeadLetter.count({ where });
    return NextResponse.json({
      items: logs,
      total,
      scope: "system",
      deadLetter: true,
      organizationId: auth.user.orgId ?? null,
    });
  }

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
    scope: isSuperuser ? "system" : "organization",
    organizationId: auth.user.orgId ?? null,
  });
}
