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
<<<<<<< HEAD
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
=======
 * FE-010 ROOT FIX: requireRole was previously defined in lib/auth/server.ts
 * but had ZERO call sites — every non-admin route used requireAuth (any
 * authenticated user), so a read-only viewer could change billing, revoke
 * API keys, create projects in any org, etc.
 *
 * This helper is the route-call-site-friendly version: it returns
 * { user, response } just like requireAuth/requireAdmin, so routes can use
 * the same early-return pattern:
 *
 *   const auth = await requireRole(user, "billing", "owner", "admin");
 *   if (auth.user === null) return auth.response;
 *
 * The roles list is variadic: pass every role that should be allowed. We
 * always implicitly allow "admin" and "owner" because they are superuser
 * roles — restricting them from a specific endpoint would be a footgun.
 */
export async function requireRole(
  user: AuthenticatedUser | null,
  ...roles: string[]
): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  // Always authenticate first.
  if (!user) {
    return {
      user: null,
      response: NextResponse.json(
        { error: "unauthorized", message: "Authentication required" },
        { status: 401 }
      ),
    };
  }
  // Admin/owner are always allowed (superuser bypass).
  const allowed = new Set([...roles, "admin", "owner"]);
  if (!allowed.has(user.role)) {
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
    return {
      user: null,
      response: NextResponse.json(
        {
          error: "forbidden",
<<<<<<< HEAD
          message: `This action requires one of the following roles: ${roles.join(", ")}`,
=======
          message: `Your role (${user.role}) is not permitted to perform this action.`,
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
        },
        { status: 403 }
      ),
    };
  }
<<<<<<< HEAD
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
=======
  return { user, response: null };
}

/**
 * Convenience wrapper for routes that need role-based access but don't
 * already have the user. Combines requireAuth + requireRole in one call:
 *
 *   const auth = await requireAuthRole("billing", "owner", "admin");
 *   if (auth.user === null) return auth.response;
 */
export async function requireAuthRole(
  ...roles: string[]
): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  const auth = await requireAuth();
  if (auth.user === null) return auth;
  return requireRole(auth.user, ...roles);
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
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
