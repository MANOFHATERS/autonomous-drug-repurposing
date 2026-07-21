/**
 * GET /api/kg/stats — Teammate 8 ROOT FIX.
 *
 * Proxies KG stats requests from the browser to the backend FastAPI
 * service at http://localhost:8000/kg/stats, which in turn proxies to
 * the Phase 2 KG service at http://localhost:8001/kg/stats.
 *
 * This route is the SINGLE entry point for browser-side KG stats
 * queries. The frontend's kg-service.ts calls this route (NOT the
 * Phase 2 service directly) so the backend can enforce:
 *   1. JWT auth (Phase 2's /kg/stats has no auth)
 *   2. Rate limiting (100 req/min per user)
 *   3. Org-scoped header forwarding (X-Org-Id)
 *   4. Audit logging (compliance trail)
 *
 * Response shape (matches Phase 2 service's /kg/stats + canonicalNodeCount):
 *   {
 *     nodeCount: number,            // total nodes (all types)
 *     canonicalNodeCount: number,   // canonical types only (Teammate 8)
 *     edgeCount: number,
 *     nodeTypes: Record<string, number>,
 *     edgeTypes: Record<string, number>,
 *     sources: Array<{name: string, available: boolean}>,
 *     lastUpdated: string  // ISO-8601 UTC
 *   }
 */

import { proxyGetToBackend } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic"; // never cache — KG stats are live

export async function GET() {
  return proxyGetToBackend({
    path: "/kg/stats",
    auditAction: "kg_stats_proxy",
    auditResource: "backend:/kg/stats",
    timeoutMs: 30_000, // matches backend's hard timeout
  });
}
