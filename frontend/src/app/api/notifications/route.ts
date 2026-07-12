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
  const [items, total, unread] = await Promise.all([
    db.notification.findMany({
      where,
      orderBy: { createdAt: "desc" },
      take: page.limit,
      skip: page.offset,
    }),
    db.notification.count({ where }),
    db.notification.count({ where: { userId: auth.user.userId, readAt: null } }),
  ]);
  return NextResponse.json({ ...buildPaginatedResponse(items, total, page), unread });
}
