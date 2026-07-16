import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/api-helpers";
import { buildPaginatedResponse } from "@/lib/pagination";
import { db } from "@/lib/db";
// TASK-272: Zod validation on query params.
import { NotificationsQuery } from "@/lib/zod-schemas";

/**
 * GET /api/notifications
 *
 * Returns the authenticated user's notifications, wired to the REAL
 * Notification Prisma model (db.notification.findMany). Task 263's
 * "currently returns mock notifications" finding was a stale
 * description — the previous fix already wired the route to the real
 * table. This commit adds:
 *
 *   1. TASK-272: Zod validation on query params (limit, offset). The
 *      previous code used `parsePagination` which accepted NaN —
 *      `?limit=abc` would silently return all rows (no cap).
 *
 *   2. TASK-280: returns 503 when the notifications table is
 *      unreachable, so the monitoring layer can detect the outage.
 *
 * Query params (Zod-validated):
 *   - limit: max rows to return (default 50, capped at 100).
 *   - offset: pagination offset (default 0).
 *
 * BE-065: Single round-trip for both total and unread counts using
 * Prisma groupBy. The groupBy aggregates readAt into two buckets:
 *   - readAt = null  → unread notifications
 *   - readAt != null → read notifications
 * Total is the sum of both bucket counts.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // TASK-272: validate query params with Zod. The schema coerces strings
  // to numbers and applies bounds. Falls back to defaults if params are
  // absent; returns 400 on validation failure.
  const parseResult = NotificationsQuery.safeParse({
    limit: req.nextUrl.searchParams.get("limit") ?? undefined,
    offset: req.nextUrl.searchParams.get("offset") ?? undefined,
  });
  if (!parseResult.success) {
    return NextResponse.json(
      {
        error: "bad_request",
        message: "Invalid query parameters.",
        issues: parseResult.error.issues.map((iss) => ({
          path: iss.path.join("."),
          message: iss.message,
        })),
      },
      { status: 400 }
    );
  }
  const { limit, offset } = parseResult.data;
  const page = { limit, offset };

  const where = { userId: auth.user.userId };

  try {
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
  } catch (e) {
    // TASK-280: return 503 when the notifications table is unreachable
    // so the monitoring layer can detect the outage and alert.
    console.error("[NOTIFICATIONS] DB error:", e);
    return NextResponse.json(
      {
        error: "service_unavailable",
        message: "Notifications database is currently unavailable.",
      },
      { status: 503 }
    );
  }
}
