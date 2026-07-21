/**
 * GET /api/kg/health — Teammate 8 ROOT FIX.
 *
 * Proxies the backend FastAPI service's /health endpoint to the
 * browser. Used by the frontend's system status dashboard to show
 * whether the backend is reachable and which downstream services
 * (GT model, RL agent, database) are connected.
 *
 * Auth: any authenticated user (the /health endpoint returns only
 * public status info — no sensitive data).
 */

import { proxyGetToBackend } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic";

export async function GET() {
  return proxyGetToBackend({
    path: "/health",
    auditAction: "backend_health_check",
    auditResource: "backend:/health",
    timeoutMs: 5_000, // health check should be fast
  });
}
