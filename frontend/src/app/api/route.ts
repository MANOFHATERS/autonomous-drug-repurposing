import { NextResponse } from "next/server";
import { requireAuth } from "@/lib/api-helpers";

/**
 * GET /api
 *
 * BE-001 ROOT FIX: The previous implementation returned { message: "Hello, world!" }
 * — a placeholder from create-next-app with no authentication. This leaked the API
 * existence to any probe (no auth required) and served zero purpose.
 *
 * Root fix: Replace with a real health check that requires authentication.
 * Returns 401 for unauthenticated requests (same as any other protected endpoint).
 * Returns minimal health metadata for authenticated users — enough to confirm
 * the API is reachable and operational, but no sensitive data.
 *
 * Security: requireAuth() is called FIRST — the endpoint returns 401 before
 * any processing if the caller has no valid session. This prevents API
 * enumeration by unauthenticated probes.
 */
export async function GET() {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  return NextResponse.json({
    status: "ok",
    service: "drugos-api",
    version: "1.0.0",
    authenticated: true,
    userId: auth.user.userId,
    timestamp: new Date().toISOString(),
  });
}
