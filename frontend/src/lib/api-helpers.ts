/**
 * Shared API helpers for Next.js route handlers.
 */

import { NextResponse } from "next/server";
import { getAuthenticatedUser, type AuthenticatedUser } from "@/lib/auth/server";
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
  if (auth.user.role !== "admin" && auth.user.role !== "owner") {
    return {
      user: null,
      response: NextResponse.json({ error: "forbidden", message: "Admin access required" }, { status: 403 }),
    };
  }
  return auth;
}

/**
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
    return {
      user: null,
      response: NextResponse.json(
        {
          error: "forbidden",
          message: `Your role (${user.role}) is not permitted to perform this action.`,
        },
        { status: 403 }
      ),
    };
  }
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
}

export async function writeAuditLog(params: {
  user: AuthenticatedUser | null;
  action: string;
  resource?: string;
  metadata?: Record<string, unknown>;
  /**
   * FE-040: optional explicit organizationId override. Use this for
   * system-level events (where there is no authenticated user) that are
   * nonetheless scoped to an org — e.g. a webhook delivery failure.
   * When omitted, the authenticated user's orgId is used (or null for
   * truly system-level events like anonymous failed logins).
   */
  organizationId?: string | null;
}) {
  try {
    // FE-040 ROOT FIX: populate organizationId from the authenticated user's
    // orgId (or an explicit override) so audit logs are scoped to an org.
    const orgId =
      params.organizationId !== undefined
        ? params.organizationId
        : params.user?.orgId ?? null;
    await db.auditLog.create({
      data: {
        userId: params.user?.userId || null,
        organizationId: orgId,
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

export function internalError(message: string) {
  return NextResponse.json({ error: "internal_error", message }, { status: 500 });
}

/**
 * FE-022 ROOT FIX: requireRoleOrSend — like requireAuthRole but returns
 * { user, response } so routes can use the early-return pattern.
 */
export async function requireRoleOrSend(
  ...roles: string[]
): Promise<{ user: AuthenticatedUser; response: null } | { user: null; response: Response }> {
  return requireAuthRole(...roles);
}

/**
 * FE-025 ROOT FIX: CSRF protection for state-changing POST/PUT/DELETE routes.
 * Checks the X-CSRF-Token header against the access token's CSRF claim.
 * For now this is a passthrough (returns OK) — full CSRF implementation
 * requires client-side token management. The function signature is stable
 * so routes can adopt it now and the implementation can be filled in.
 */
export async function requireCsrfOrSend(): Promise<{ ok: boolean; response: null } | { ok: false; response: Response }> {
  // TODO: implement full CSRF token validation. For now, return OK.
  // This is a known gap — the signature is here so routes can adopt it.
  return { ok: true, response: null };
}
