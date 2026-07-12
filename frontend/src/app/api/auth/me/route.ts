import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { db } from "@/lib/db";
import { badRequest, writeAuditLog } from "@/lib/api-helpers";

/**
 * GET /api/auth/me — return the current user's profile + organization
 * memberships. Returns 401 if no valid session.
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
    return NextResponse.json({ error: "not_found" }, { status: 404 });
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
  return NextResponse.json({
    user,
    organizations: memberships.map((m) => ({
      id: m.organization.id,
      name: m.organization.name,
      slug: m.organization.slug,
      plan: m.organization.plan,
      role: m.role,
    })),
    activeOrganizationId: authUser.orgId || memberships[0]?.organization.id || null,
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
