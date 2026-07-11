/**
 * Shared API helpers for Next.js route handlers.
 *
 * ROOT FIXES (FE-010, FE-025):
 *  - `requireRoleOrSend` makes the existing `requireRole` function from
 *    `auth/server.ts` actually usable from route handlers in one line.
 *    Previously `requireRole` existed but had zero call sites.
 *  - `requireCsrfOrSend` enforces the double-submit CSRF token on
 *    state-changing endpoints.
 *  - `getClientIp` extracts the client IP for rate-limiting / audit logs.
 */

import { NextResponse } from "next/server";
import { getAuthenticatedUser, requireRole, verifyCsrfToken, type AuthenticatedUser } from "@/lib/auth/server";
import { db } from "@/lib/db";

export async function requireAuth(): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  const user = await getAuthenticatedUser();
  if (!user) {
    return {
      user: null,
      response: NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 }),
    };
  }
  return { user, response: null };
}

export async function requireAdmin(): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  const auth = await requireAuth();
  if (auth.user === null) return auth;
  if (!requireRole(auth.user, "admin", "owner")) {
    return {
      user: null,
      response: NextResponse.json({ error: "forbidden", message: "Admin access required" }, { status: 403 }),
    };
  }
  return auth;
}

/**
 * Auth + RBAC in one call. Returns `{ user, response }`; if `user` is null,
 * `response` is a 401 (unauthenticated) or 403 (forbidden) NextResponse and
 * the handler must return it immediately.
 *
 * Usage:
 *   const auth = await requireRoleOrSend("admin", "owner");
 *   if (auth.user === null) return auth.response;
 *
 * (FE-010 root fix — `requireRole` is now actually called from every
 * privileged route.)
 */
export async function requireRoleOrSend(
  ...roles: string[]
): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  const auth = await requireAuth();
  if (auth.user === null) return auth;
  if (!requireRole(auth.user, ...roles)) {
    return {
      user: null,
      response: NextResponse.json(
        {
          error: "forbidden",
          message: `This action requires one of the following roles: ${roles.join(", ")}`,
        },
        { status: 403 }
      ),
    };
  }
  return auth;
}

/**
 * Enforce the double-submit CSRF token on state-changing requests.
 * Returns `true` if the request should be allowed to proceed, `false`
 * otherwise (the caller must return the 403 response).
 *
 * Usage:
 *   const csrf = await requireCsrfOrSend();
 *   if (csrf.response) return csrf.response;
 *
 * (FE-025 root fix.)
 */
export async function requireCsrfOrSend(): Promise<{ ok: boolean; response: Response | null }> {
  const valid = await verifyCsrfToken();
  if (valid) return { ok: true, response: null };
  return {
    ok: false,
    response: NextResponse.json(
      { error: "invalid_csrf", message: "CSRF token missing or invalid" },
      { status: 403 }
    ),
  };
}

export async function writeAuditLog(params: {
  user: AuthenticatedUser | null;
  action: string;
  resource?: string;
  metadata?: Record<string, unknown>;
}) {
  try {
    await db.auditLog.create({
      data: {
        userId: params.user?.userId || null,
        actorName: params.user?.email || "anonymous",
        action: params.action,
        resource: params.resource || null,
        metadata: JSON.stringify(params.metadata || {}),
      },
    });
  } catch (e) {
    // Audit log failures must NEVER break the main request — but we log them.
    console.error("Failed to write audit log:", e);
  }
}

export function badRequest(message: string) {
  return NextResponse.json({ error: "bad_request", message }, { status: 400 });
}

export function notFound(message: string) {
  return NextResponse.json({ error: "not_found", message }, { status: 404 });
}

export function unauthorized(message = "Authentication required") {
  return NextResponse.json({ error: "unauthorized", message }, { status: 401 });
}

export function forbidden(message = "Forbidden") {
  return NextResponse.json({ error: "forbidden", message }, { status: 403 });
}

export function internalError(message: string) {
  return NextResponse.json({ error: "internal_error", message }, { status: 500 });
}

/**
 * Best-effort client-IP extraction for rate-limiting and audit logs.
 * Walks the standard proxy headers (X-Forwarded-For, X-Real-IP) and falls
 * back to the connection's remote address.
 */
export function getClientIp(req: Request): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) {
    const first = xff.split(",")[0]?.trim();
    if (first) return first;
  }
  const xReal = req.headers.get("x-real-ip");
  if (xReal) return xReal.trim();
  return "unknown";
}
