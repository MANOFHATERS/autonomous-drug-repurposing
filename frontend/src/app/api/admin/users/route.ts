import { NextRequest, NextResponse } from "next/server";
import { requireAdmin, badRequest, writeAuditLog, requireCsrfOrSend, isPlatformSuperuser } from "@/lib/api-helpers";
import { revokeAllRefreshTokensForUser } from "@/lib/auth/server";
import { db } from "@/lib/db";
// FE-016 ROOT FIX (Team Member 15, v108 — pre-existing build blocker):
// Import the Prisma-generated enum types so the `data` object on line ~202
// can be properly typed. Without these, Prisma's UserUpdateInput rejects
// plain `string` for `role` and `status`, failing the production build.
import type { UserRole, UserStatus } from "@prisma/client";
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

  // BE-002 ROOT FIX: only `platformOwner` (the platform-level superuser)
  // bypasses the org-membership requirement. The `owner` role is now
  // org-scoped (same as `admin`) — a user who created an org gets
  // OrganizationMember.role=owner but User.role=researcher (or whatever
  // they were promoted to). The previous code granted User.role=owner
  // system-wide access, which was a multi-tenant data breach.
  // platformOwner is settable ONLY via direct DB access — no API route
  // can grant it. See isPlatformSuperuser in api-helpers.ts.
  if (!isPlatformSuperuser(auth.user) && !auth.user.orgId) {
    return NextResponse.json(
      { error: "forbidden", message: "You are not a member of any organization." },
      { status: 403 }
    );
  }

  // If the caller is not a platform superuser and they're asking for a
  // different org than their own, deny. Use strict equality so that
  // `null !== "anything"` correctly trips the denial.
  const orgId = requestedOrgId || auth.user.orgId;
  if (!isPlatformSuperuser(auth.user) && orgId !== auth.user.orgId) {
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

  // BE-002: Defense in depth — verify the admin is actually a member
  // of `orgId`. The access token's `orgId` claim is the primary source, but
  // a forged token (or a stale session after a demotion) could lie. This
  // query catches that. For a platformOwner, we skip the check (they're
  // global). owner and admin are org-scoped — they MUST pass this check.
  if (!isPlatformSuperuser(auth.user)) {
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

  // BE-002: For non-platform-superusers, omit `email` from the select —
  // email is PII under GDPR, and a consortium-member admin does not need
  // other members' emails to manage roles. platformOwner retains full
  // visibility for cross-tenant audits (they are the SaaS operator's
  // staff with a legitimate need-to-know across tenants).
  //
  // The `owner` role is NO LONGER treated as a superuser — an org owner
  // sees only their own org's users, same as an admin. This closes the
  // multi-tenant data breach where any user promoted to `owner` could
  // enumerate every user in every org.
  const isSuperuser = isPlatformSuperuser(auth.user);

  // Get user IDs that belong to this org, then fetch those users.
  // BE-002: for platformOwner, skip the memberships query entirely —
  // they see ALL users regardless of org, so the query is wasted work.
  let userIds: string[] = [];
  if (!isSuperuser) {
    const memberships = await db.organizationMember.findMany({
      where: { organizationId: orgId },
      select: { userId: true },
    });
    userIds = memberships.map((m) => m.userId);
  }

  const whereClause = isSuperuser ? {} : { id: { in: userIds } };
  const selectClause = isSuperuser
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

  // FE-016 ROOT FIX (Team Member 15, v108 — pre-existing build blocker):
  // The previous `data` was typed as `{ role?: string; status?: string }`,
  // but Prisma's UserUpdateInput expects `role: UserRole` (an enum) and
  // `status: UserStatus` (an enum). Passing plain `string` made the
  // build fail at "Running TypeScript ..." in `next build`. The runtime
  // validators (isValidAdminRole / isValidUserStatus) already ensure the
  // string values map to valid enum members, so we cast at the assignment
  // site. This is a minimal surgical fix to unblock the production build
  // — the validation logic is unchanged.
  const data: { role?: UserRole; status?: UserStatus } = {};

  if (body.role !== undefined) {
    if (!isValidAdminRole(body.role)) {
      return badRequest(
        `Invalid role. Must be one of: ${(ALLOWED_ROLES_ADMIN as readonly string[]).join(", ")}`
      );
    }
    // isValidAdminRole guarantees body.role is a valid UserRole value.
    data.role = body.role as UserRole;
  }
  if (body.status !== undefined) {
    if (!isValidUserStatus(body.status)) {
      return badRequest(
        `Invalid status. Must be one of: ${(ALLOWED_USER_STATUSES as readonly string[]).join(", ")}`
      );
    }
    // isValidUserStatus guarantees body.status is a valid UserStatus value.
    data.status = body.status as UserStatus;
  }

  if (Object.keys(data).length === 0) {
    return badRequest("Nothing to update. Provide role and/or status.");
  }

  // FE-006 / BE-002: prevent privilege escalation to owner unless caller
  // is a platform superuser (platformOwner). The `owner` role is org-scoped
  // but still admin-level, so promoting someone to owner within your own
  // org is allowed if you're already an admin/owner of that org. Promotion
  // to `platformOwner` is NEVER allowed via API — that role is settable
  // only via direct DB access.
  // FE-016 ROOT FIX (Team Member 15, v108 — pre-existing build blocker):
  // Cast to string for the comparison — body.role is typed as
  // AllowedAdminRole which doesn't include 'platformOwner' (that role is
  // intentionally not in the API-grantable list). The runtime check is
  // still meaningful: if a caller attempts to send role='platformOwner',
  // the isValidAdminRole validator above rejects it with 400 BEFORE
  // reaching this line, but this defensive check guards against any
  // future code path that bypasses the validator.
  if ((body.role as string) === "platformOwner") {
    return NextResponse.json(
      { error: "forbidden", message: "The platformOwner role cannot be granted via the API. It is settable only via direct database access by the SaaS operator." },
      { status: 403 }
    );
  }
  if (body.role === "owner" && !isPlatformSuperuser(auth.user) && auth.user.role !== "owner") {
    return NextResponse.json(
      { error: "forbidden", message: "Only an owner or platform superuser can promote another user to owner." },
      { status: 403 }
    );
  }

  // FE-013 ROOT FIX / BE-002: cross-tenant IDOR guard. An admin or owner
  // (both org-scoped) can only PATCH users who share at least one org
  // membership with them. platformOwner is the ONLY role that bypasses
  // this check (they are the SaaS operator's staff). Without this, an
  // admin in Org A could suspend any user in Org B by guessing their cuid.
  // The previous code granted `owner` the same bypass as platformOwner,
  // which was the multi-tenant data breach.
  if (!isPlatformSuperuser(auth.user)) {
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
