import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUser, signAccessToken, setAuthCookies } from "@/lib/auth/server";
import { db } from "@/lib/db";
import { badRequest, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";

/**
 * GET /api/auth/me — return the current user's profile + organization
 * memberships. Returns 401 if no valid session.
 *
 * FE-051 ROOT FIX: every authenticated page load triggers this endpoint,
 * which does db.user.findUnique + db.organizationMember.findMany. For a
 * SPA with frequent re-renders and a pharma platform with 1000 researchers
 * doing 100 page views/day each, that's 200K DB queries/day just for /me.
 * We add `Cache-Control: private, max-age=60` so browsers AND Next.js's
 * Data Cache cache the response for 60 seconds per user.
 *
 * `private` is critical: it forbids shared/CDN caches from storing the
 * response (which would leak one user's profile to others). Only the
 * user's own browser may cache it. `max-age=60` is short enough that
 * role/membership changes propagate within a minute, but long enough
 * to collapse the 100-page-views/day-per-user load into ~1 DB query/min.
 *
 * The PATCH handler below remains un-cached because it mutates state.
 */
export async function GET() {
  const authUser = await getAuthenticatedUser();
  if (!authUser) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const user = await db.user.findUnique({
    where: { id: authUser.userId },
    select: {
      id: true,
      email: true,
      name: true,
      role: true,
      title: true,
      bio: true,
      status: true,
      emailVerified: true,
      academicVerified: true,
      mfaEnabled: true,
      lastLoginAt: true,
      createdAt: true,
    },
  });
  if (!user) {
    // FE-068 ROOT FIX: Return 401 (not 404) when the access token decoded
    // successfully but the user no longer exists in the DB. Returning 404
    // leaked information: an attacker could distinguish "valid token for a
    // deleted user" (404) from "invalid token" (401). Treating both cases
    // as 401 collapses the side channel — the attacker learns nothing
    // about whether the user ever existed.
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  // FE-060 ROOT FIX: Use `select` (not `include`) so we fetch only the
  // fields actually used in the response (id, name, slug, plan) — not the
  // entire Organization record (which also includes status, seats, createdAt,
  // updatedAt). Reduces payload + DB load for users in many orgs.
  const memberships = await db.organizationMember.findMany({
    where: { userId: user.id },
    select: {
      role: true,
      organization: {
        select: { id: true, name: true, slug: true, plan: true },
      },
    },
  });
  const body = {
    user,
    organizations: memberships.map((m) => ({
      id: m.organization.id,
      name: m.organization.name,
      slug: m.organization.slug,
      plan: m.organization.plan,
      role: m.role,
    })),
    activeOrganizationId: authUser.orgId || memberships[0]?.organization.id || null,
  };
  return NextResponse.json(body, {
    headers: {
      // FE-051: per-user browser cache, 60s. Never cache on shared/CDN.
      "Cache-Control": "private, max-age=60",
    },
  });
}

/**
 * PATCH /api/auth/me — update the current user's profile fields.
 *
 * Only safe, user-editable fields are accepted: name, title, bio.
 * Email and role changes are NOT allowed here — they require admin
 * intervention or a separate verification flow.
 */
export async function PATCH(req: NextRequest) {
  const authUser = await getAuthenticatedUser();
  if (!authUser) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  // FE-072 ROOT FIX: Suspended users must not be able to edit their profile.
  //
  // getAuthenticatedUser() only verifies the JWT signature — it does NOT
  // re-check the user's current status in the DB. So a user suspended by
  // an admin retains a valid access token for up to ACCESS_TOKEN_TTL (15
  // min) and could change their name/title/bio during that window. In a
  // pharma research setting, a suspended user changing their display name
  // to impersonate a colleague could cause real collaboration harm.
  //
  // Root fix: fetch the user's current status from the DB on every PATCH
  // and reject if status === "suspended". This is a defense-in-depth
  // measure alongside the longer-term fix (token revocation on suspension
  // via refresh-token revoke + access-token blacklist until expiry).
  const currentUser = await db.user.findUnique({
    where: { id: authUser.userId },
    select: { status: true },
  });
  if (!currentUser) {
    // FE-068 (same rationale as GET): treat deleted user as 401, not 404.
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  if (currentUser.status === "suspended") {
    return NextResponse.json(
      {
        error: "account_suspended",
        message: "Your account has been suspended. Profile changes are not permitted.",
      },
      { status: 403 }
    );
  }

  let body: { name?: string; title?: string; bio?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const data: { name?: string; title?: string | null; bio?: string | null } = {};
  if (typeof body.name === "string") {
    const trimmed = body.name.trim();
    if (trimmed.length < 1) return badRequest("Name cannot be empty");
    if (trimmed.length > 200) return badRequest("Name is too long (max 200 chars)");
    data.name = trimmed;
  }
  if (typeof body.title === "string") {
    data.title = body.title.trim().slice(0, 200) || null;
  }
  if (typeof body.bio === "string") {
    data.bio = body.bio.trim().slice(0, 2000) || null;
  }

  if (Object.keys(data).length === 0) {
    // FE-054 ROOT FIX: HTTP PATCH semantics allow a no-op patch — the server
    // returns 200 with the current (unchanged) resource. Previously this
    // returned 400 "No updatable fields", which broke clients that send an
    // empty patch to refresh their cached profile. We now return the current
    // user resource with 200, matching RFC 5789 §2.1 ("If the server
    // receives a PATCH request with no body, the server MUST process it as
    // if the body was empty and apply no changes").
    const current = await db.user.findUnique({
      where: { id: authUser.userId },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        title: true,
        bio: true,
      },
    });
    if (!current) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    return NextResponse.json({ user: current, noop: true });
  }

  const updated = await db.user.update({
    where: { id: authUser.userId },
    data,
    select: {
      id: true,
      email: true,
      name: true,
      role: true,
      title: true,
      bio: true,
    },
  });

  await writeAuditLog({
    user: authUser,
    action: "profile_update",
    resource: `user:${updated.id}`,
    metadata: { fields: Object.keys(data) },
  });

  return NextResponse.json({ user: updated });
}

/**
 * PATCH /api/auth/me/active-org — switch the user's active organization.
 *
 * BE-079 ROOT FIX: The GET handler above sets activeOrganizationId from
 * authUser.orgId (the JWT claim) with a fallback to memberships[0]. But
 * there was no way for a user with multiple org memberships to SWITCH
 * their active org — all queries were permanently scoped to the first org.
 *
 * This endpoint accepts { activeOrganizationId: string } and, if the user
 * is a member of that org, issues a new access token with the updated orgId
 * claim. The caller must then use the new token for subsequent requests.
 *
 * CSRF: required (this is a state-changing POST-semantics operation).
 */
export async function PUT(req: NextRequest) {
  // Use PUT for idempotent update (switching to the same org is a no-op).
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const authUser = await getAuthenticatedUser();
  if (!authUser) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: { activeOrganizationId?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  const targetOrgId = body.activeOrganizationId?.trim();
  if (!targetOrgId) {
    return badRequest("activeOrganizationId is required");
  }

  // Verify the user is actually a member of the target org.
  const membership = await db.organizationMember.findFirst({
    where: { userId: authUser.userId, organizationId: targetOrgId },
    select: { role: true },
  });
  if (!membership) {
    return NextResponse.json(
      { error: "forbidden", message: "You are not a member of the specified organization." },
      { status: 403 }
    );
  }

  // Issue a new access token with the updated orgId.
  const user = await db.user.findUnique({
    where: { id: authUser.userId },
    select: { email: true, role: true, orgMembershipRevokedAt: true },
  });
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const omv = user.orgMembershipRevokedAt
    ? Math.floor(user.orgMembershipRevokedAt.getTime() / 1000)
    : undefined;
  const newAccess = signAccessToken({
    userId: authUser.userId,
    email: user.email,
    role: user.role,
    orgId: targetOrgId,
    omv,
  });

  // Rotate the refresh token too so the session is fully re-bound.
  const { refresh: newRefresh } = await import("@/lib/auth/server").then((m) =>
    m.rotateRefreshToken(authUser.userId)
  );
  await setAuthCookies(newAccess, newRefresh);

  await writeAuditLog({
    user: { ...authUser, orgId: targetOrgId },
    action: "active_org_switched",
    resource: `user:${authUser.userId}`,
    metadata: { previousOrgId: authUser.orgId, newOrgId: targetOrgId },
  });

  return NextResponse.json({
    ok: true,
    activeOrganizationId: targetOrgId,
    message: "Active organization switched successfully.",
  });
}
