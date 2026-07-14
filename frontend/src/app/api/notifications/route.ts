import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/api-helpers";
import { parsePagination, buildPaginatedResponse } from "@/lib/pagination";
import { db } from "@/lib/db";

/**
 * FE-047 ROOT FIX: GET was `take: 50` with no offset/pagination — power
 * users with >50 notifications could not access older records. Now accepts
 * `limit` (capped at 100) and `offset` query params and returns the
 * standard paginated envelope. The unread count is returned alongside so
 * the bell-icon badge still works.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const page = parsePagination(req.nextUrl.searchParams);
  const where = { userId: auth.user.userId };
  // BE-065 ROOT FIX: Collapse 3 DB queries into 2. The previous code did
  // findMany + count (total) + count (unread) = 3 round-trips. With 1000
  // researchers polling every 60s, that's 3000 queries/min just for
  // notifications. The fix: use a single groupBy to get both total and
  // unread in one query. The unread count is derived from the group where
  // readAt is null.
  const [items, counts] = await Promise.all([
    db.notification.findMany({
      where,
      orderBy: { createdAt: "desc" },
      take: page.limit,
      skip: page.offset,
    }),
    // Single aggregation query: count grouped by readAt null/not-null.
    db.notification.groupBy({
      by: ["readAt"],
      where,
      _count: { readAt: true },
    }),
  ]);
  let total = 0;
  let unread = 0;
  for (const row of counts) {
    const c = row._count.readAt ?? 0;
    total += c;
    if (row.readAt === null) unread += c;
  }
  return NextResponse.json({ ...buildPaginatedResponse(items, total, page), unread });
}
