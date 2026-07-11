import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";

/**
 * GET /api/auth/activity
 *
 * Returns the most recent audit-log entries authored BY the currently
 * authenticated user. Unlike /api/audit-logs (which is admin-only and
 * returns all entries across the org), this endpoint is scoped to the
 * calling user and is used by the Security Settings screen to show
 * "recent account activity" — logins, password changes, 2FA enrollments.
 */
export async function GET() {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  const items = await db.auditLog.findMany({
    where: { userId: user.userId },
    orderBy: { createdAt: "desc" },
    take: 20,
  });
  return NextResponse.json({ items, total: items.length });
}
