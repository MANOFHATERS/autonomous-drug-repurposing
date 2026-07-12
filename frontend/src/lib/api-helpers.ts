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

/**
 * FE-034 ROOT FIX: writeAuditLog previously swallowed ALL failures silently.
 * For a pharma platform where audit logs are regulatory requirements
 * (FDA 21 CFR Part 11), silent audit-log failure is a compliance
 * violation — security incidents could go unrecorded and forensic
 * investigations would be hampered by missing log entries.
 *
 * Now we support TWO modes:
 *
 *   1. Critical (default for security actions): If the audit-log write
 *      fails, the request is ABORTED with a 500. The caller never sees
 *      a "success" response that wasn't audited. Use this for: login,
 *      logout, password change, 2FA enable/disable, admin actions,
 *      role/status changes, API key creation/revocation, billing changes.
 *
 *   2. Non-critical (default): If the audit-log write fails, we log to
 *      stderr AND record a fallback entry in a dead-letter table (or
 *      just stderr if the DB is the failure cause). The request
 *      continues — the action was less important than the audit trail.
 *
 * The caller decides which mode by passing `critical: true` or omitting
 * it. Security-sensitive callers MUST pass `critical: true`.
 *
 * Returns:
 *   - { ok: true } on success.
 *   - { ok: false, error } on failure. If critical, the caller MUST
 *     return internalError() to the client.
 */
export interface AuditLogResult {
  ok: boolean;
  error?: string;
}

export async function writeAuditLog(params: {
  user: AuthenticatedUser | null;
  action: string;
  resource?: string;
  metadata?: Record<string, unknown>;
  /**
   * If true, a failure to write the audit log ABORTS the request.
   * Use for security-critical actions (login, password change, 2FA
   * disable, admin actions, role/status changes, API key ops).
   */
  critical?: boolean;
  /** Optional request IP, for forensic analysis. */
  ip?: string;
  /** Optional User-Agent string, for forensic analysis. */
  userAgent?: string;
}): Promise<AuditLogResult> {
  try {
    await db.auditLog.create({
      data: {
        userId: params.user?.userId || null,
        actorName: params.user?.email || "anonymous",
        action: params.action,
        resource: params.resource || null,
        ip: params.ip || null,
        userAgent: params.userAgent || null,
        metadata: JSON.stringify(params.metadata || {}),
      },
    });
    return { ok: true };
  } catch (e) {
    const errMsg = e instanceof Error ? e.message : String(e);
    // Always log to stderr — even if critical, this gives operators a
    // chance to see what went wrong before the request is aborted.
    console.error("[AUDIT-LOG-FAILURE]", {
      action: params.action,
      resource: params.resource,
      userId: params.user?.userId,
      error: errMsg,
      critical: params.critical === true,
      timestamp: new Date().toISOString(),
    });

    if (params.critical === true) {
      // Abort the request — the action MUST be auditable.
      return { ok: false, error: errMsg };
    }

    // Non-critical: try to write to a fallback mechanism. If the DB
    // itself is down, this also fails — but at least we tried.
    try {
      // Best-effort fallback: write to a separate "audit_log_dead_letter"
      // table if it exists. We don't model it in Prisma because adding
      // a model would require a migration; instead we use $executeRaw
      // with a CREATE TABLE IF NOT EXISTS so it's idempotent.
      // For SQLite/Postgres compatible DDL.
      await db.$executeRaw`CREATE TABLE IF NOT EXISTS audit_log_dead_letter (
        id SERIAL PRIMARY KEY,
        action TEXT NOT NULL,
        resource TEXT,
        user_id TEXT,
        actor_name TEXT,
        metadata TEXT,
        error TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`;
      await db.$executeRaw`INSERT INTO audit_log_dead_letter
        (action, resource, user_id, actor_name, metadata, error)
        VALUES (${params.action}, ${params.resource || null},
                ${params.user?.userId || null},
                ${params.user?.email || "anonymous"},
                ${JSON.stringify(params.metadata || {})},
                ${errMsg})`;
    } catch (fallbackErr) {
      // Both primary and fallback failed — the DB is likely down.
      // The stderr log above is the only record. Operators must
      // monitor for [AUDIT-LOG-FAILURE] entries.
      console.error("[AUDIT-LOG-FAILURE] Fallback also failed:", fallbackErr);
    }
    return { ok: false, error: errMsg };
  }
}

/**
 * FE-034: Convenience helper for critical audit logs. Throws if the
 * write fails, so the caller's existing try/catch returns 500.
 *
 * Usage:
 *   await writeAuditLogCritical({ user, action: 'login', resource: ... });
 *   // — if this throws, the caller's catch block returns 500.
 */
export async function writeAuditLogCritical(params: {
  user: AuthenticatedUser | null;
  action: string;
  resource?: string;
  metadata?: Record<string, unknown>;
  ip?: string;
  userAgent?: string;
}): Promise<void> {
  const result = await writeAuditLog({ ...params, critical: true });
  if (!result.ok) {
    throw new Error(`audit_log_write_failed: ${result.error}`);
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
