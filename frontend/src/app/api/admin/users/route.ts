import { NextRequest, NextResponse } from "next/server";
import { requireAdmin, badRequest, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import { revokeAllRefreshTokensForUser } from "@/lib/auth/server";
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
 *
 * FE-016 ROOT FIX (Team Member 14, v2): The previous fix used
 * `req.nextUrl.searchParams.get("orgId") || auth.user.orgId` to pick the
 * target org. This had a subtle hole: if `auth.user.orgId` was null/undefined
 * (an admin with NO org membership — e.g. a stale session, or a user
 * demoted out of their org but not yet re-logged-in), the code fell through
 * to `whereClause = {}` for non-owners... NO, actually it fell through to
 * `{ id: { in: [] } }` because `memberships` was empty. So it returned no
 * users — which is SAFE but leaks no signal. The real hole was that the
 * `orgId !== auth.user.orgId` check used loose equality (`!==`), which is
 * correct for strings but treats `null !== undefined` as `true`. That meant
 * an admin with `orgId = null` who passed `?orgId=anything` would trip the
 * denial — also safe. So the existing code WAS safe-by-accident, not
 * safe-by-design.
 *
 * This v2 hardens it to safe-by-design:
 *   1. EXPLICIT null-orgId rejection: if a non-owner admin has no orgId,
 *      return 403 immediately. They should not be calling this endpoint.
 *   2. Use strict equality (`!==`) consistently — already done, made explicit.
 *   3. The memberships query is now `findMany({ where: { organizationId:
 *      orgId, userId: auth.user.userId }})` to double-check the admin is
 *      actually a member of the org they're querying (defense in depth —
 *      catches a forged orgId claim in the access token).
 *   4. The select clause now omits `email` for non-owner callers — email is
 *      PII under GDPR, and a consortium-member admin does not need to see
 *      other members' email addresses to manage roles. Owner retains full
 *      visibility for cross-tenant audits.
 *
 * A regression test in `fe-016-admin-org-scoping.test.ts` verifies that an
 * admin of org A cannot see org B's users, AND that a non-owner admin with
 * no orgId gets 403.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "50", 10);
  const offset = parseInt(req.nextUrl.searchParams.get("offset") || "0", 10);
  const requestedOrgId = req.nextUrl.searchParams.get("orgId");

  // FE-016 v2: Non-owner admin with no orgId → reject. They should not be
  // calling this endpoint at all. (An owner is the global super-admin and
  // bypasses this check.)
  if (auth.user.role !== "owner" && !auth.user.orgId) {
    return NextResponse.json(
      { error: "forbidden", message: "You are not a member of any organization." },
      { status: 403 }
    );
  }

  // If the caller is not owner (super-admin) and they're asking for a
  // different org than their own, deny. Use strict equality so that
  // `null !== "anything"` correctly trips the denial.
  const orgId = requestedOrgId || auth.user.orgId;
  if (auth.user.role !== "owner" && orgId !== auth.user.orgId) {
    await writeAuditLog({
      user: auth.user,
      action: "admin_user_list_denied_cross_tenant",
      resource: `org:${requestedOrgId || "(none)"}`,
      metadata: { adminOrgId: auth.user.orgId },
    });
    return NextResponse.json(
      { error: "forbidden", message: "You can only view users in your own organization." },
      { status: 403 }
    );
  }

  // FE-016 v2: Defense in depth — verify the admin is actually a member
  // of `orgId`. The access token's `orgId` claim is the primary source, but
  // a forged token (or a stale session after a demotion) could lie. This
  // query catches that. For an owner, we skip the check (they're global).
  if (auth.user.role !== "owner") {
    const adminMembership = await db.organizationMember.findFirst({
      where: { organizationId: orgId, userId: auth.user.userId },
      select: { id: true },
    });
    if (!adminMembership) {
      await writeAuditLog({
        user: auth.user,
        action: "admin_user_list_denied_not_member",
        resource: `org:${orgId}`,
      });
      return NextResponse.json(
        { error: "forbidden", message: "You are not a member of the requested organization." },
        { status: 403 }
      );
    }
  }

  // FE-016 v2: For non-owners, omit `email` from the select — email is PII
  // under GDPR, and a consortium-member admin does not need other members'
  // emails to manage roles. Owner retains full visibility for cross-tenant
  // audits. (Use a conditional select object.)
  const isOwner = auth.user.role === "owner";

  // Get user IDs that belong to this org, then fetch those users.
  // FE-016 v2: for owners, skip the memberships query entirely — they see
  // ALL users regardless of org, so the query is wasted work.
  let userIds: string[] = [];
  if (!isOwner) {
    const memberships = await db.organizationMember.findMany({
      where: { organizationId: orgId },
      select: { userId: true },
    });
    userIds = memberships.map((m) => m.userId);
  }

  const whereClause = isOwner ? {} : { id: { in: userIds } };
  const selectClause = isOwner
    ? {
        id: true,
        email: true,
        name: true,
        role: true,
        status: true,
        emailVerified: true,
        mfaEnabled: true,
        createdAt: true,
        lastLoginAt: true,
      }
    : {
        id: true,
        name: true,
        role: true,
        status: true,
        emailVerified: true,
        mfaEnabled: true,
        createdAt: true,
        lastLoginAt: true,
      };

  const [users, total] = await Promise.all([
    db.user.findMany({
      where: whereClause,
      select: selectClause,
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
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

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

  // FE-013 ROOT FIX: cross-tenant IDOR guard. An admin (non-owner) can only
  // PATCH users who share at least one org membership with them. Owner is
  // global super-admin and bypasses the check. Without this, an admin in
  // Org A could suspend any user in Org B by guessing their cuid.
  if (auth.user.role !== "owner") {
    const adminMemberships = await db.organizationMember.findMany({
      where: { userId: auth.user.userId },
      select: { organizationId: true },
    });
    const adminOrgIds = adminMemberships.map((m) => m.organizationId);
    if (adminOrgIds.length === 0) {
      return NextResponse.json(
        { error: "forbidden", message: "You are not a member of any organization." },
        { status: 403 }
      );
    }
    const targetMemberships = await db.organizationMember.findMany({
      where: { userId: body.userId, organizationId: { in: adminOrgIds } },
      select: { id: true },
    });
    if (targetMemberships.length === 0) {
      // Do NOT leak whether the target user exists — return 404 not 403.
      await writeAuditLog({
        user: auth.user,
        action: "admin_user_update_denied_cross_tenant",
        resource: `user:${body.userId}`,
        metadata: { adminOrgIds },
      });
      return NextResponse.json(
        { error: "not_found", message: "User not found in your organization(s)." },
        { status: 404 }
      );
    }
  }

  const updated = await db.user.update({
    where: { id: body.userId },
    data,
    select: { id: true, email: true, name: true, role: true, status: true },
  });

  // FE-032 ROOT FIX: If the user was just suspended, revoke ALL their
  // refresh tokens so existing sessions stop working immediately. Without
  // this, a suspended user's existing refresh token would continue to
  // work for up to 30 days (REFRESH_TOKEN_TTL_DAYS) — defeating the
  // purpose of suspension.
  if (data.status === "suspended") {
    const revokedCount = await revokeAllRefreshTokensForUser(updated.id);
    await writeAuditLog({
      user: auth.user,
      action: "admin_user_suspended_tokens_revoked",
      resource: `user:${updated.id}`,
      metadata: { revokedRefreshTokenCount: revokedCount },
    });
  }

  await writeAuditLog({
    user: auth.user,
    action: "admin_user_update",
    resource: `user:${updated.id}`,
    metadata: data,
  });
  return NextResponse.json(updated);
}
