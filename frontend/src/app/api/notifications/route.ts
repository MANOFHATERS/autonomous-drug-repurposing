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
 *
 * BE-065 ROOT FIX: The previous code performed 3 separate DB queries on
 * every notification poll: findMany (notifications), count (total), count
 * (unread). At 1000 researchers polling every 60s, that's 3000 queries/min
 * just for notifications. The fix uses a single groupBy query to compute
 * both total and unread counts in one round-trip, cutting the query load
 * by 33%.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const page = parsePagination(req.nextUrl.searchParams);
  const where = { userId: auth.user.userId };

  // BE-065: Single round-trip for both total and unread counts using
  // Prisma groupBy. The groupBy aggregates readAt into two buckets:
  //   - readAt = null  → unread notifications
  //   - readAt != null → read notifications
  // Total is the sum of both bucket counts.
  const [items, readBuckets] = await Promise.all([
    db.notification.findMany({
      where,
      orderBy: { createdAt: "desc" },
      take: page.limit,
      skip: page.offset,
    }),
    db.notification.groupBy({
      by: ["readAt"],
      where,
      _count: { id: true },
    }),
  ]);

  // Compute total and unread from the grouped buckets.
  let total = 0;
  let unread = 0;
  for (const bucket of readBuckets) {
    const count = bucket._count.id;
    total += count;
    if (bucket.readAt === null) {
      unread += count;
    }
  }

  return NextResponse.json({ ...buildPaginatedResponse(items, total, page), unread });
}
