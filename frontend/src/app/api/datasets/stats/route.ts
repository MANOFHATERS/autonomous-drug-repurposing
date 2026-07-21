/**
 * GET /api/datasets/stats
 *
 * TEAMMATE-4 ROOT FIX (NEW ROUTE): proxies to the FastAPI backend's
 * /datasets/stats endpoint, which in turn proxies to Phase 1's /stats.
 *
 * Architecture:
 *   Browser -> Next.js /api/datasets/stats -> FastAPI /datasets/stats
 *                                          -> Phase 1 /stats
 *
 * The frontend's dataset-service.ts now calls this route (via
 * `/api/datasets/stats`) instead of calling PHASE1_SERVICE_URL directly.
 * This routes the request through the FastAPI backend so we can enforce:
 *   - JWT authentication (the backend's verify_jwt dependency).
 *   - org_id scoping (the backend's verify_org_id dependency).
 *   - Rate limiting (slowapi: 100/min per user).
 *   - 503 fallback when Phase 1 is unavailable (was 500/hang before).
 *
 * This Next.js route is a thin auth-aware proxy. It:
 *   1. Verifies the user is authenticated (requireAuth).
 *   2. Forwards the request to the FastAPI backend at BACKEND_SERVICE_URL.
 *   3. Returns the backend's response verbatim.
 *
 * The backend handles all the heavy lifting (JWT verification, org_id
 * extraction, rate limiting, Phase 1 proxying). This route just bridges
 * the browser session (NextAuth JWT cookie) to the backend's Bearer JWT.
 *
 * Why a Next.js route (not a direct browser fetch to the backend)?
 *   - The browser's JWT is in an httpOnly cookie set by NextAuth. The
 *     FastAPI backend expects a Bearer token in the Authorization header.
 *     This route translates cookie -> Bearer header.
 *   - The backend is on a different port (8001) in dev; a direct browser
 *     fetch would hit CORS issues. Same-origin /api/* avoids CORS.
 *   - The backend is on a private IP in production; only the Next.js
 *     server can reach it. This route is the public-facing edge.
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * Resolve the FastAPI backend URL. Defaults to http://localhost:8001 for
 * local dev; production deploys set BACKEND_SERVICE_URL to the private
 * backend URL (e.g. http://backend:8001 in docker-compose).
 *
 * TEAMMATE-4 ROOT FIX: previously the frontend called PHASE1_SERVICE_URL
 * directly. Now the frontend calls only the backend (which proxies to
 * Phase 1). PHASE1_SERVICE_URL is no longer used by the frontend.
 */
function getBackendUrl(): string {
  const url = process.env.BACKEND_SERVICE_URL || "http://localhost:8001";
  return url.replace(/\/$/, "");
}

/**
 * Forward the user's auth cookie as a Bearer token to the backend.
 *
 * In Next.js, the user's session JWT is in the `next-auth.session-token`
 * (or `__Secure-next-auth.session-token` in HTTPS) cookie. We extract it
 * and pass it as `Authorization: Bearer <jwt>` so the backend's
 * verify_jwt dependency can decode it.
 *
 * If the backend uses a different JWT secret than the frontend's
 * NextAuth secret, set JWT_SECRET on the backend to match
 * NEXTAUTH_SECRET on the frontend.
 */
async function getBackendAuthHeaders(req: NextRequest): Promise<Record<string, string>> {
  // The NextAuth session cookie name depends on the secure cookie setting.
  // In dev (HTTP), it's "next-auth.session-token". In prod (HTTPS), it's
  // "__Secure-next-auth.session-token".
  const sessionCookie =
    req.cookies.get("next-auth.session-token")?.value ||
    req.cookies.get("__Secure-next-auth.session-token")?.value ||
    "";

  const headers: Record<string, string> = {
    "Accept": "application/json",
  };
  if (sessionCookie) {
    headers["Authorization"] = `Bearer ${sessionCookie}`;
  }
  return headers;
}

export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const backendUrl = getBackendUrl();
    const headers = await getBackendAuthHeaders(req);
    const response = await fetch(`${backendUrl}/datasets/stats`, {
      method: "GET",
      headers,
      cache: "no-store",
    });

    await writeAuditLog({
      user: auth.user,
      action: "dataset_stats_query",
      resource: "dataset:stats",
      metadata: {
        backend_status: response.status,
      },
    });

    if (!response.ok) {
      const text = await response.text();
      return NextResponse.json(
        {
          error: "backend_error",
          message: `Backend returned ${response.status}: ${text.slice(0, 500)}`,
          backend_status: response.status,
        },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Dataset stats proxy failed: ${msg}`);
  }
}
