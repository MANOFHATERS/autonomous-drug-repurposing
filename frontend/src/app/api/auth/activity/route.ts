import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";

/**
 * GET /api/auth/activity?limit=20&offset=0
 *
 * Returns the most recent audit-log entries authored BY the currently
 * authenticated user. Unlike /api/audit-logs (which is admin-only and
 * returns all entries across the org), this endpoint is scoped to the
 * calling user and is used by the Security Settings screen to show
 * "recent account activity" — logins, password changes, 2FA enrollments.
 *
 * FE-052 ROOT FIX: Previously `take: 20` was hardcoded with no offset
 * param — a user with 1000 audit log entries could never see entries
 * 21-1000. Now accepts `limit` (1..100, default 20) and `offset`
 * (>= 0, default 0) query params, and returns a `total` count so the
 * UI can render pagination controls.
 */
const MAX_LIMIT = 100;
const DEFAULT_LIMIT = 20;

export async function GET(req: NextRequest) {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", message: "Authentication required" },
      { status: 401 }
    );
  }

  // Parse + clamp query params. Non-numeric / negative values fall back
  // to defaults rather than 400-ing — this matches typical REST pagination
  // ergonomics and keeps the UI resilient to bad query strings.
  const { searchParams } = new URL(req.url);
  const rawLimit = Number(searchParams.get("limit"));
  const rawOffset = Number(searchParams.get("offset"));
  const limit = Number.isFinite(rawLimit) && rawLimit > 0
    ? Math.min(Math.floor(rawLimit), MAX_LIMIT)
    : DEFAULT_LIMIT;
  const offset = Number.isFinite(rawOffset) && rawOffset >= 0
    ? Math.floor(rawOffset)
    : 0;

  const [items, total] = await Promise.all([
    db.auditLog.findMany({
      where: { userId: user.userId },
      orderBy: { createdAt: "desc" },
      take: limit,
      skip: offset,
    }),
    db.auditLog.count({ where: { userId: user.userId } }),
  ]);

  return NextResponse.json({
    items,
    total,
    limit,
    offset,
    // Whether there are more entries available beyond the current page.
    hasMore: offset + items.length < total,
  });
}
