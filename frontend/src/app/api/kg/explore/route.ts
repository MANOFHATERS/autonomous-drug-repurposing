/**
 * POST /api/kg/explore — Teammate 8 ROOT FIX.
 *
 * Proxies KG subgraph exploration requests from the browser to the
 * backend FastAPI service at http://localhost:8000/kg/explore, which
 * in turn proxies to the Phase 2 KG service at
 * http://localhost:8001/kg/explore.
 *
 * Request body: { drug?: string, disease?: string, depth?: number }
 * Response: { nodes: [...], edges: [...], truncated: bool }
 *
 * The Phase 2 service performs a real BFS over the in-memory KG (or a
 * Cypher query against Neo4j) starting from the requested drug or
 * disease node. The ``depth`` parameter controls the BFS depth (1-3
 * hops is typical for researcher dashboard exploration).
 *
 * Auth: any authenticated user (no role gate — explore is read-only).
 * Rate limit: 100 req/min per user (enforced at the backend).
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth } from "@/lib/api-helpers";
import { proxyToBackend } from "@/lib/backend-proxy";
import { writeAuditLog } from "@/lib/api-helpers";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // Parse + validate the body. We accept {drug, disease, depth, limit}
  // — all optional, but at least one of drug/disease should be present
  // (the backend enforces this; we let it return the 400).
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Invalid JSON body." },
      { status: 400 },
    );
  }
  // Basic sanity check — body must be an object.
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return NextResponse.json(
      { error: "bad_request", message: "Body must be a JSON object." },
      { status: 400 },
    );
  }

  const result = await proxyToBackend({
    method: "POST",
    path: "/kg/explore",
    user: auth.user,
    body,
    timeoutMs: 30_000,
  });

  // Audit log (best-effort).
  try {
    await writeAuditLog({
      user: auth.user,
      action: "kg_explore_proxy",
      resource: `backend:/kg/explore:${body.drug ?? "*"}:${body.disease ?? "*"}`,
      metadata: {
        ok: result.ok,
        status: result.status,
        drug: body.drug,
        disease: body.disease,
      },
    });
  } catch {
    // ignore audit log failures
  }

  return NextResponse.json(result.body, { status: result.status });
}
