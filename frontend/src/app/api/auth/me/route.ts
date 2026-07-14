import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUser, signAccessToken, setAuthCookies } from "@/lib/auth/server";
import { db } from "@/lib/db";
import { badRequest, writeAuditLog } from "@/lib/api-helpers";
// BE-029 ROOT FIX (Team Member 12): Zod-validated PATCH body.
import { validateBody, AuthMePatchBody } from "@/lib/zod-schemas";

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
  // BE-039 ROOT FIX (Team Member 12): the previous code did TWO DB
  // queries — `db.user.findUnique(...)` + `db.organizationMember.findMany(...)`.
  // For 1000 researchers doing 100 page views/day, that's 200K extra
  // queries/day just for /api/auth/me. We collapse them into ONE query
  // using Prisma's `include` to eager-load the user's org memberships.
  // The `include` performs a single JOIN on the server side, halving
  // the DB round-trips for every authenticated page load.
  //
  // FE-060 preservation: we still use `select` on the nested
  // `organization` to fetch only id/name/slug/plan (not the entire
  // Organization record).
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
      // BE-039: eager-load memberships in the SAME query. The nested
      // `select` mirrors the previous separate findMany's select.
      organizationMemberships: {
        select: {
          role: true,
          organization: {
            select: { id: true, name: true, slug: true, plan: true },
          },
        },
      },
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
  // BE-039: the memberships are now nested under `organizationMemberships`
  // (the implicit relation name from the User → OrganizationMember back-relation).
  // Defensive: if the Prisma client doesn't return the relation (e.g. an
  // older client version, or a test mock that doesn't populate it), fall
  // back to an empty array rather than throwing on `.map()`.
  const memberships = user.organizationMemberships ?? [];
  const body = {
    user: {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      title: user.title,
      bio: user.bio,
      status: user.status,
      emailVerified: user.emailVerified,
      academicVerified: user.academicVerified,
      mfaEnabled: user.mfaEnabled,
      lastLoginAt: user.lastLoginAt,
      createdAt: user.createdAt,
    },
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

  // BE-079 ROOT FIX: Added activeOrganizationId to the PATCH body so users
  // can switch their active org. Previously, activeOrganizationId was always
  // the first org (from JWT, set at login), with no way to switch. All
  // queries were scoped to the first org they joined, preventing multi-org
  // users from working across organizations.
  let body: { name?: string; title?: string; bio?: string; activeOrganizationId?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  // BE-029 ROOT FIX: schema-validate the body. The AuthMePatchBody schema
  // enforces: name (1..200 chars if present), title (≤200), bio (≤2000),
  // activeOrganizationId (non-empty string OR null). The previous code's
  // `typeof body.name === "string"` check accepted ANY string length — a
  // 1MB name would have been written to the DB. The schema caps lengths
  // at parse time.
  const parsed = validateBody(AuthMePatchBody, body);
  if (!parsed.ok) return parsed.response;
  body = parsed.data as { name?: string; title?: string; bio?: string; activeOrganizationId?: string };

  // BE-079: If activeOrganizationId is provided, validate it BEFORE
  // updating any profile fields. The user must be a member of the
  // target org. Invalid orgId → 400 (don't partially update).
  if (body.activeOrganizationId !== undefined) {
    const targetOrgId = body.activeOrganizationId;
    // null means "clear active org" — allowed.
    if (targetOrgId !== null) {
      const membership = await db.organizationMember.findFirst({
        where: { userId: authUser.userId, organizationId: targetOrgId },
        select: { id: true },
      });
      if (!membership) {
        return NextResponse.json(
          { error: "forbidden", message: "You are not a member of the specified organization." },
          { status: 403 }
        );
      }
    }
  }

  const data: { name?: string; title?: string | null; bio?: string | null } = {};
  if (typeof body.name === "string") {
    // BE-029: schema already enforces 1..200 chars, but we still trim
    // and re-check post-trim in case the trimmed result is empty.
    const trimmed = body.name.trim();
    if (trimmed.length < 1) return badRequest("Name cannot be empty");
    data.name = trimmed;
  }
  if (typeof body.title === "string") {
    data.title = body.title.trim().slice(0, 200) || null;
  }
  if (typeof body.bio === "string") {
    data.bio = body.bio.trim().slice(0, 2000) || null;
  }

  // BE-079: Track whether we're doing an org switch (separate from profile update)
  const switchingOrg = body.activeOrganizationId !== undefined;

  if (Object.keys(data).length === 0 && !switchingOrg) {
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

  // BE-079: If the user is switching their active org, issue a new
  // access token with the updated orgId and set it as a cookie. This
  // ensures subsequent requests are scoped to the newly-selected org.
  let newAccessToken: string | null = null;
  if (switchingOrg && body.activeOrganizationId !== undefined) {
    const newOrgId = body.activeOrganizationId;  // can be null (clear active org)
    newAccessToken = signAccessToken({
      userId: authUser.userId,
      email: authUser.email,
      role: authUser.role,
      orgId: newOrgId || undefined,
    });
    // Set the new access token cookie. We keep the existing refresh token
    // (org switching doesn't require re-authentication).
    const { cookies } = await import("next/headers");
    const cookieStore = await cookies();
    const refreshToken = cookieStore.get("drugos_refresh")?.value;
    if (refreshToken) {
      await setAuthCookies(newAccessToken, refreshToken);
    }
    await writeAuditLog({
      user: { ...authUser, orgId: newOrgId || undefined },
      action: "active_org_switched",
      resource: `user:${authUser.userId}`,
      metadata: { previousOrgId: authUser.orgId, newOrgId: newOrgId },
    });
  }

  const actionTypes: string[] = [];
  if (Object.keys(data).length > 0) actionTypes.push("profile_update");
  if (switchingOrg) actionTypes.push("active_org_switched");

  await writeAuditLog({
    user: authUser,
    action: actionTypes.join(","),
    resource: `user:${updated.id}`,
    metadata: { fields: Object.keys(data), switchedOrg: switchingOrg },
  });

  return NextResponse.json({
    user: updated,
    // BE-079: Return the new activeOrganizationId so the client can
    // update its local state without re-fetching /api/auth/me.
    activeOrganizationId: switchingOrg
      ? (body.activeOrganizationId || null)
      : (authUser.orgId || null),
    orgSwitched: switchingOrg,
  });
}
