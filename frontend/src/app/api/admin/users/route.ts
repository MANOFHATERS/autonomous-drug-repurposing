import { NextRequest, NextResponse } from "next/server";
import { requireAdmin, badRequest, requireCsrfOrSend } from "@/lib/api-helpers";
import { db } from "@/lib/db";

/**
 * ROOT FIX for FE-016 (admin/users PATCH accepts arbitrary role/status values).
 *
 * Previously: the PATCH handler took `body.role` and `body.status` and wrote
 * them straight to the DB. An admin could set `role: "superadmin"` or
 * `status: "foobar"` — neither value is in the application's RBAC matrix,
 * so subsequent role checks would silently deny the user everything (or, if
 * a future check used `includes`, silently grant everything).
 *
 * ROOT FIX: validate `role` and `status` against explicit allowlists. Same
 * for the `limit` / `offset` query params on GET — clamp them so a request
 * like `?limit=99999999` does not allocate a 100-MB result set.
 */

const ALLOWED_ROLES = new Set([
  "viewer",
  "researcher",
  "data-scientist",
  "pi",
  "business-dev",
  "developer",
  "billing",
  "admin",
  "owner",
]);

const ALLOWED_STATUSES = new Set(["active", "suspended", "pending_approval"]);

function clampInt(raw: string | null, def: number, min: number, max: number): number {
  if (!raw) return def;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) return def;
  return Math.max(min, Math.min(max, n));
}

export async function GET(req: NextRequest) {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;
  const limit = clampInt(req.nextUrl.searchParams.get("limit"), 50, 1, 500);
  const offset = clampInt(req.nextUrl.searchParams.get("offset"), 0, 0, 10_000);
  const [users, total] = await Promise.all([
    db.user.findMany({
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        status: true,
        emailVerified: true,
        createdAt: true,
        lastLoginAt: true,
      },
      orderBy: { createdAt: "desc" },
      take: limit,
      skip: offset,
    }),
    db.user.count(),
  ]);
  return NextResponse.json({ items: users, total });
}

export async function PATCH(req: NextRequest) {
  // CSRF — FE-025.
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;
  let body: { userId: string; role?: string; status?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON");
  }
  if (!body.userId || typeof body.userId !== "string") {
    return badRequest("userId is required");
  }

  const data: { role?: string; status?: string } = {};
  if (body.role !== undefined) {
    if (!ALLOWED_ROLES.has(body.role)) {
      return badRequest(`Invalid role. Allowed: ${[...ALLOWED_ROLES].join(", ")}`);
    }
    data.role = body.role;
  }
  if (body.status !== undefined) {
    if (!ALLOWED_STATUSES.has(body.status)) {
      return badRequest(`Invalid status. Allowed: ${[...ALLOWED_STATUSES].join(", ")}`);
    }
    data.status = body.status;
  }
  if (Object.keys(data).length === 0) {
    return badRequest("At least one of 'role' or 'status' must be supplied");
  }

  // Guardrail: prevent an admin from demoting themselves or removing their
  // own admin status — a classic self-lockout.
  if (body.userId === auth.user.userId && data.role && data.role !== "admin" && data.role !== "owner") {
    return NextResponse.json(
      { error: "self_demote", message: "You cannot remove your own admin/owner role." },
      { status: 400 }
    );
  }

  const updated = await db.user.update({
    where: { id: body.userId },
    data,
    select: { id: true, email: true, name: true, role: true, status: true },
  });
  await (await import("@/lib/api-helpers")).writeAuditLog({
    user: auth.user,
    action: "admin_user_update",
    resource: `user:${updated.id}`,
    metadata: data,
  });
  return NextResponse.json(updated);
}
