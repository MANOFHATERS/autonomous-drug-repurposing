import { NextRequest, NextResponse } from "next/server";
import { requireAdmin, badRequest, writeAuditLog } from "@/lib/api-helpers";
import { db } from "@/lib/db";
import {
  ALLOWED_ROLES_ADMIN,
  ALLOWED_USER_STATUSES,
  isValidAdminRole,
  isValidUserStatus,
} from "@/app/api/auth/register/route";

/**
 * GET /api/admin/users
 *
 * FE-006 (related): Previously this endpoint did db.user.findMany with NO
 * org filter, returning ALL users across ALL orgs. A self-registered "admin"
 * (from the FE-006 escalation) could enumerate every user in the system.
 *
 * Root fix: scope by the caller's organization(s). An admin only sees users
 * who are members of an org that the admin is also a member of. Global
 * super-admin (owner role) is the only exception — they see all users.
 * For now we filter by orgId from the caller's session.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "50", 10);
  const offset = parseInt(req.nextUrl.searchParams.get("offset") || "0", 10);
  const orgId = req.nextUrl.searchParams.get("orgId") || auth.user.orgId;

  // If the caller is not owner (super-admin) and they're asking for a
  // different org than their own, deny.
  if (auth.user.role !== "owner" && orgId !== auth.user.orgId) {
    return NextResponse.json(
      { error: "forbidden", message: "You can only view users in your own organization." },
      { status: 403 }
    );
  }

  // Get user IDs that belong to this org, then fetch those users.
  const memberships = await db.organizationMember.findMany({
    where: { organizationId: orgId },
    select: { userId: true },
  });
  const userIds = memberships.map((m) => m.userId);

  const whereClause = auth.user.role === "owner"
    ? {} // owner sees all
    : { id: { in: userIds } };

  const [users, total] = await Promise.all([
    db.user.findMany({
      where: whereClause,
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        status: true,
        emailVerified: true,
        // FE-009 ROOT FIX: surface mfaEnabled so the admin user-management
        // screen can show the real 2FA state per user instead of a fabricated
        // boolean.
        mfaEnabled: true,
        createdAt: true,
        lastLoginAt: true,
      },
      orderBy: { createdAt: "desc" },
      take: limit,
      skip: offset,
    }),
    db.user.count({ where: whereClause }),
  ]);
  return NextResponse.json({ items: users, total });
}

/**
 * PATCH /api/admin/users
 * Body: { userId: string, role?: string, status?: string }
 *
 * FE-016 ROOT FIX: Previously this endpoint did NO validation of the role
 * or status values. An admin could set a user's role to ANY string
 * ("superuser", "godmode", "") and status to anything. The Prisma schema
 * stored role as String (no enum).
 *
 * Root fix: validate role against ALLOWED_ROLES_ADMIN and status against
 * ALLOWED_USER_STATUSES before the update. Reject unknown values with 400.
 */
export async function PATCH(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;
  let body: { userId: string; role?: string; status?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.userId) return badRequest("userId is required");

  const data: { role?: string; status?: string } = {};

  if (body.role !== undefined) {
    if (!isValidAdminRole(body.role)) {
      return badRequest(
        `Invalid role. Must be one of: ${(ALLOWED_ROLES_ADMIN as readonly string[]).join(", ")}`
      );
    }
    data.role = body.role;
  }
  if (body.status !== undefined) {
    if (!isValidUserStatus(body.status)) {
      return badRequest(
        `Invalid status. Must be one of: ${(ALLOWED_USER_STATUSES as readonly string[]).join(", ")}`
      );
    }
    data.status = body.status;
  }

  if (Object.keys(data).length === 0) {
    return badRequest("Nothing to update. Provide role and/or status.");
  }

  // FE-006: prevent privilege escalation to owner unless caller is owner.
  if (body.role === "owner" && auth.user.role !== "owner") {
    return NextResponse.json(
      { error: "forbidden", message: "Only an owner can promote another user to owner." },
      { status: 403 }
    );
  }

  const updated = await db.user.update({
    where: { id: body.userId },
    data,
    select: { id: true, email: true, name: true, role: true, status: true },
  });
  await writeAuditLog({
    user: auth.user,
    action: "admin_user_update",
    resource: `user:${updated.id}`,
    metadata: data,
  });
  return NextResponse.json(updated);
}
