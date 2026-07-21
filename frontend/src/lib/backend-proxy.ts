/**
 * Backend proxy helper — Teammate 8 ROOT FIX.
 *
 * Shared utility for Next.js API routes at /api/kg/* that need to proxy
 * to the backend FastAPI service. Centralizes:
 *   1. Backend URL resolution (BACKEND_URL env var, default localhost:8000)
 *   2. JWT minting (reuses the frontend's NextAuth JWT secret)
 *   3. Org-id header forwarding (X-Org-Id for tenant scoping)
 *   4. Error translation (backend 503 → 503, 429 → 429, etc.)
 *
 * WHY PROXY THROUGH NEXT.JS (not call backend directly from browser)?
 *   - The backend FastAPI service runs on port 8000 — a SEPARATE origin
 *     from the Next.js frontend (port 3000). Direct browser → backend
 *     calls would require CORS configuration on the backend AND would
 *     expose the backend to the public internet (security risk — the
 *     backend has /cypher which executes arbitrary read-only Cypher).
 *   - The Next.js API route runs SERVER-SIDE (Node.js), so it can call
 *     the backend on localhost:8000 WITHOUT CORS and WITHOUT exposing
 *     the backend to the public internet. The backend only needs to be
 *     reachable from the Next.js server (same host or private network).
 *   - The Next.js route handles browser auth (NextAuth session cookie)
 *     and mints a fresh JWT for the backend call. The backend trusts
 *     the JWT because it shares the JWT_SECRET with the frontend.
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth, writeAuditLog } from "@/lib/api-helpers";
import { signAccessToken } from "@/lib/auth/server";

/**
 * Resolve the backend FastAPI service URL.
 *
 * Priority:
 *   1. ``BACKEND_URL`` env var (Teammate 8 canonical name — set in production)
 *   2. ``BACKEND_SERVICE_URL`` env var (Teammate 4 alias — kept for backward compat)
 *   3. ``DRUGOS_BACKEND_URL`` env var (alias — kept for forward compat)
 *   4. ``http://localhost:8000`` (default — local dev)
 *
 * The default port (8000) was changed from 8001 in Teammate 8 to
 * eliminate the collision with the Phase 2 KG service (also on 8001).
 */
export function resolveBackendUrl(): string {
  const explicit =
    process.env.BACKEND_URL ||
    process.env.BACKEND_SERVICE_URL ||  // Teammate 4 alias
    process.env.DRUGOS_BACKEND_URL ||
    "http://localhost:8000";
  // Strip trailing slash so URL concatenation is predictable.
  return explicit.replace(/\/+$/, "");
}

/**
 * Build the headers for a backend proxy call.
 *
 * The ``Authorization: Bearer <jwt>`` header carries a freshly-minted
 * access token (signed with the frontend's JWT_SECRET, which the
 * backend shares). The backend's ``verify_jwt`` validates this token.
 *
 * The ``X-Org-Id`` header carries the caller's active org ID — the
 * backend's ``verify_org_id`` reads this as a fallback when the JWT
 * doesn't include the ``org_id`` claim (it does, but defense-in-depth).
 */
function buildBackendHeaders(user: {
  userId: string;
  email: string;
  role: string;
  platformRole?: string;
  orgId?: string;
}): Record<string, string> {
  const jwt = signAccessToken(user);
  const headers: Record<string, string> = {
    Authorization: `Bearer ${jwt}`,
    Accept: "application/json",
  };
  if (user.orgId) {
    headers["X-Org-Id"] = user.orgId;
  }
  return headers;
}

/**
 * Result of a backend proxy call — either a successful JSON response
 * or an error suitable for returning to the browser.
 */
export type BackendProxyResult =
  | { ok: true; status: number; body: unknown }
  | { ok: false; status: number; body: { error: string; message: string } };

/**
 * Proxy a request to the backend FastAPI service.
 *
 * Usage:
 *   const result = await proxyToBackend({
 *     method: "GET",
 *     path: "/kg/stats",
 *     user: auth.user,
 *   });
 *   if (!result.ok) {
 *     return NextResponse.json(result.body, { status: result.status });
 *   }
 *   return NextResponse.json(result.body);
 *
 * The function handles:
 *   - JWT minting + Authorization header
 *   - X-Org-Id header forwarding
 *   - 30s timeout (matches the backend's hard timeout)
 *   - Error translation (network errors → 503, backend 4xx/5xx → passthrough)
 *
 * The caller is responsible for:
 *   - Auth gating (call requireAuth() BEFORE proxyToBackend)
 *   - Audit logging (call writeAuditLog() AFTER a successful proxy)
 *   - Request body validation (use Zod schemas BEFORE proxyToBackend)
 */
export async function proxyToBackend(opts: {
  method: "GET" | "POST";
  path: string; // e.g. "/kg/stats" (must start with "/")
  user: {
    userId: string;
    email: string;
    role: string;
    platformRole?: string;
    orgId?: string;
  };
  body?: unknown; // JSON-serializable request body (POST only)
  timeoutMs?: number; // default 30000 (matches backend hard timeout)
}): Promise<BackendProxyResult> {
  const backendUrl = resolveBackendUrl();
  const url = `${backendUrl}${opts.path}`;
  const headers = buildBackendHeaders(opts.user);
  const timeoutMs = opts.timeoutMs ?? 30_000;

  // Use AbortController to enforce the client-side timeout. fetch()'s
  // signal option is the standard way to abort an in-flight request.
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const fetchOpts: RequestInit = {
      method: opts.method,
      headers,
      signal: controller.signal,
    };
    if (opts.method === "POST" && opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchOpts.body = JSON.stringify(opts.body);
    }

    const response = await fetch(url, fetchOpts);

    // Parse the response body. The backend always returns JSON (even
    // for errors — FastAPI's HTTPException.detail is JSON-serializable).
    let parsedBody: unknown;
    try {
      parsedBody = await response.json();
    } catch {
      // Non-JSON response (shouldn't happen with FastAPI, but defense).
      parsedBody = {
        error: "invalid_backend_response",
        message: `Backend returned non-JSON body (status ${response.status}).`,
      };
    }

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        body: parsedBody as { error: string; message: string },
      };
    }
    return {
      ok: true,
      status: response.status,
      body: parsedBody,
    };
  } catch (e) {
    // Network error, timeout, or abort. Map to a 503 (Service
    // Unavailable) — the backend is unreachable, not broken.
    const isTimeout =
      e instanceof Error && (e.name === "AbortError" || /timeout/i.test(e.message));
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ok: false,
      status: isTimeout ? 504 : 503,
      body: {
        error: isTimeout ? "backend_timeout" : "backend_unreachable",
        message: isTimeout
          ? `Backend did not respond within ${timeoutMs}ms. The Phase 2 KG service may be slow or down.`
          : `Backend at ${backendUrl} is unreachable: ${msg}`,
      },
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Helper for routes that just want to proxy GET + return the response.
 *
 * Combines requireAuth + proxyToBackend + audit log + response in one
 * call. Routes that need custom logic (e.g. role gating, body
 * validation) should call proxyToBackend directly.
 */
export async function proxyGetToBackend(opts: {
  path: string;
  auditAction: string;
  auditResource: string;
  timeoutMs?: number;
}): Promise<Response> {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const result = await proxyToBackend({
    method: "GET",
    path: opts.path,
    user: auth.user,
    timeoutMs: opts.timeoutMs,
  });

  // Audit log (best-effort — don't fail the request if logging fails).
  try {
    await writeAuditLog({
      user: auth.user,
      action: opts.auditAction,
      resource: opts.auditResource,
      metadata: {
        ok: result.ok,
        status: result.status,
      },
    });
  } catch {
    // ignore audit log failures
  }

  return NextResponse.json(result.body, { status: result.status });
}

/**
 * Helper for routes that just want to proxy POST + return the response.
 */
export async function proxyPostToBackend(opts: {
  path: string;
  body: unknown;
  auditAction: string;
  auditResource: string;
  timeoutMs?: number;
}): Promise<Response> {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const result = await proxyToBackend({
    method: "POST",
    path: opts.path,
    user: auth.user,
    body: opts.body,
    timeoutMs: opts.timeoutMs,
  });

  try {
    await writeAuditLog({
      user: auth.user,
      action: opts.auditAction,
      resource: opts.auditResource,
      metadata: {
        ok: result.ok,
        status: result.status,
      },
    });
  } catch {
    // ignore audit log failures
  }

  return NextResponse.json(result.body, { status: result.status });
}

/**
 * Re-export NextResponse for convenience in route files.
 */
export { NextResponse };
export type { NextRequest };
