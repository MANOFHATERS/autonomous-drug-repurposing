import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { db } from "@/lib/db";
import { badRequest, writeAuditLog } from "@/lib/api-helpers";

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
  const memberships = await db.organizationMember.findMany({
    where: { userId: user.id },
    include: { organization: true },
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
    return badRequest("No updatable fields provided (name, title, bio)");
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
