<<<<<<< HEAD
import { NextResponse } from "next/server";
import { getKnowledgeGraphStats } from "@/lib/services/knowledge-graph-stats";
import { requireAuth, internalError } from "@/lib/api-helpers";

/**
 * GET /api/knowledge-graph
 *
 * ROOT FIX for FE-003: /api/knowledge-graph no longer returns 501. It now
 * returns real Phase 2 knowledge graph statistics — per-source loaded
 * status, node/edge counts, edge types present, checksums for audit.
 *
 * Resolution order:
 *   1. If KG_SERVICE_URL is set, proxy to the standalone Neo4j service
 *      (production path).
 *   2. Otherwise, read the local Phase 2 registry JSON at
 *      `../phase2/data/registry.json` (dev / single-box path).
 *   3. If neither yields data, return `source: "none"` with an empty list.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate graph statistics.
 */
export async function GET() {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const stats = await getKnowledgeGraphStats();
    return NextResponse.json(stats);
  } catch (e: any) {
    return internalError(`Knowledge graph lookup failed: ${e.message}`);
  }
=======
import { NextRequest, NextResponse } from "next/server";
import { checkKnowledgeGraphAvailability } from "@/lib/services/ml-stubs";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * GET /api/knowledge-graph?cypher=<Cypher query>&limit=<n>
 * POST /api/knowledge-graph  body: { cypher: string, params?: Record<string, unknown> }
 *
 * FE-003 ROOT FIX: The previous code returned 501 even when KG_SERVICE_URL
 * was set. The Phase 2 Neo4j graph was unreachable from the dashboard.
 *
 * ROOT FIX: This endpoint now proxies to the standalone Neo4j service
 * (or a FastAPI wrapper around it) when KG_SERVICE_URL is set. The caller
 * provides a Cypher query (or a structured query that the KG service
 * translates to Cypher); we forward it and stream back the results.
 *
 * We NEVER fabricate graph data. If the KG service is not deployed, we
 * return 503 service_not_deployed.
 *
 * SECURITY: Cypher queries can be parameterized — we forward the `params`
 * object so the KG service can use parameterized queries. Raw string
 * interpolation of user input into Cypher would be a Cypher-injection
 * vulnerability (same class as SQL injection).
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const availability = checkKnowledgeGraphAvailability();
  if (!availability.available) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: availability.service,
        description: availability.description,
        reason: availability.reason,
        documentation:
          "See Phase 2 of the build plan (Neo4j Knowledge Graph Construction). " +
          "Set KG_SERVICE_URL to enable the proxy.",
      },
      { status: 503 }
    );
  }

  const kgUrl = process.env.KG_SERVICE_URL!;
  const cypher = req.nextUrl.searchParams.get("cypher");
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "100", 10);
  const drug = req.nextUrl.searchParams.get("drug");
  const disease = req.nextUrl.searchParams.get("disease");

  try {
    // Build a structured query for the KG service. We do NOT let the
    // caller send arbitrary Cypher via GET (that would be a Cypher-
    // injection vector). Instead, GET takes drug/disease/limit params
    // and the KG service translates them to a safe parameterized query.
    const upstreamBody: Record<string, unknown> = { limit };
    if (drug) upstreamBody.drug = drug;
    if (disease) upstreamBody.disease = disease;

    const upstream = await fetch(`${kgUrl.replace(/\/$/, "")}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(upstreamBody),
    });
    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        {
          error: "kg_service_error",
          message: `KG service returned ${upstream.status}: ${text.slice(0, 500)}`,
        },
        { status: 502 }
      );
    }
    const data = await upstream.json();
    await writeAuditLog({
      user: auth.user,
      action: "kg_query",
      resource: `kg:${drug || "*"}:${disease || "*"}`,
      metadata: { nodeCount: data.nodes?.length || 0, edgeCount: data.edges?.length || 0 },
    });
    return NextResponse.json(data);
  } catch (e: any) {
    return internalError(`KG service proxy failed: ${e.message}`);
  }
}

/**
 * POST /api/knowledge-graph
 * Body: { cypher: string, params?: Record<string, unknown> }
 *
 * The POST endpoint is for advanced users who want to run a custom Cypher
 * query directly. The KG service is expected to use parameterized queries
 * — the `params` object is forwarded so user input never touches the
 * query string.
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const availability = checkKnowledgeGraphAvailability();
  if (!availability.available) {
    return NextResponse.json(
      { error: "service_not_deployed", reason: availability.reason },
      { status: 503 }
    );
  }

  let body: { cypher?: string; params?: Record<string, unknown> };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_request", message: "Invalid JSON" }, { status: 400 });
  }
  if (!body.cypher) {
    return NextResponse.json(
      { error: "bad_request", message: "cypher is required" },
      { status: 400 }
    );
  }

  try {
    const kgUrl = process.env.KG_SERVICE_URL!;
    const upstream = await fetch(`${kgUrl.replace(/\/$/, "")}/cypher`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cypher: body.cypher, params: body.params || {} }),
    });
    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        {
          error: "kg_service_error",
          message: `KG service returned ${upstream.status}: ${text.slice(0, 500)}`,
        },
        { status: 502 }
      );
    }
    const data = await upstream.json();
    await writeAuditLog({
      user: auth.user,
      action: "kg_cypher",
      resource: "kg:custom_cypher",
      metadata: { cypherPreview: body.cypher.slice(0, 80) },
    });
    return NextResponse.json(data);
  } catch (e: any) {
    return internalError(`KG service proxy failed: ${e.message}`);
  }
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
}
