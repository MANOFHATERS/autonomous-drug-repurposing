import { NextRequest, NextResponse } from "next/server";
import { requireAuth, badRequest } from "@/lib/api-helpers";
import { parsePagination, buildPaginatedResponse } from "@/lib/pagination";
import { db } from "@/lib/db";

/**
 * GET /api/team — list the members of the current user's organization.
 *
 * Returns each member's name, email, role, status, and last-login timestamp.
 * Useful for the Team Members page in the app shell.
 *
 * FE-047 ROOT FIX: previously returned ALL members with no limit at all —
 * an org with thousands of members would have rendered the entire membership
 * on every page load. Now accepts `limit` (capped at 100) and `offset`
 * query params and returns the standard paginated envelope.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  if (!auth.user.orgId) return badRequest("No active organization");

  const page = parsePagination(req.nextUrl.searchParams);
  const where = { organizationId: auth.user.orgId };

  const [memberships, total] = await Promise.all([
    db.organizationMember.findMany({
      where,
      include: {
        user: {
          select: {
            id: true,
            email: true,
            name: true,
            role: true,
            title: true,
            bio: true,
            status: true,
            lastLoginAt: true,
            createdAt: true,
          },
        },
      },
      orderBy: { joinedAt: "asc" },
      take: page.limit,
      skip: page.offset,
    }),
    db.organizationMember.count({ where }),
  ]);

  const items = memberships.map((m) => ({
    id: m.user.id,
    name: m.user.name || m.user.email,
    email: m.user.email,
    role: m.user.role,
    orgRole: m.role, // owner | admin | member | viewer | billing
    title: m.user.title,
    bio: m.user.bio,
    status: m.user.status,
    lastLoginAt: m.user.lastLoginAt,
    joinedAt: m.joinedAt,
  }));

  return NextResponse.json(buildPaginatedResponse(items, total, page));
}
