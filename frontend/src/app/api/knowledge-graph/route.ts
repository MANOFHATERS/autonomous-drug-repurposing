import { NextRequest, NextResponse } from "next/server";
import {
  requireAuth,
  requireRole,
  internalError,
  writeAuditLog,
  badRequest,
} from "@/lib/api-helpers";
// FE-002 ROOT FIX (Team Member 13): wire the lib service as a fallback.
//
// Previously: the route checked `process.env.KG_SERVICE_URL` via
// `checkKnowledgeGraphAvailability()` from ml-stubs and returned 503 when
// the env var was unset. The lib service `knowledge-graph-stats.ts`
// (which reads the real Phase 2 registry at `../phase2/data/registry.json`)
// was DEAD CODE — the route never imported or called it.
//
// ROOT FIX: GET without a `cypher` param now calls
// `getKnowledgeGraphStats()` from `knowledge-graph-stats.ts`. That lib
// service is the single source of truth:
//   1. If `KG_SERVICE_URL` is set, it proxies to the standalone Neo4j
//      service (production path).
//   2. Otherwise, it reads the local Phase 2 registry JSON (dev /
//      single-box path). The registry exists with real source stats
//      (SIDER rows, STRING edges, etc.) after the Phase 2 builder runs.
//   3. If neither is available, it returns `source: "none"` with empty
//      lists — NEVER fabricated graph statistics.
//
// The dashboard's KG explorer page therefore works in default deployments
// (no env vars set) as long as the Phase 2 builder has written its
// registry to disk. The 503-only behavior was a credibility killer.
//
// FE-020 (Team Member 15) enhanced the lib service to break down node
// counts by canonical type (Compound, Protein, Pathway, Disease,
// ClinicalOutcomes) — excluding non-canonical types like AdverseEvent
// from `nodeCount`. This route forwards those fields to the dashboard
// unchanged.
//
// FE-008 ROOT FIX: shared validator extracted so unit tests can exercise it
// without spinning up the route handler.
import { validateReadOnlyCypher } from "./cypher-validator";
import { getKnowledgeGraphStats } from "@/lib/services/knowledge-graph-stats";

/**
 * GET /api/knowledge-graph?cypher=<Cypher query>&limit=<n>
 *      /api/knowledge-graph?drug=<drug>&disease=<disease>&limit=<n>   (structured query)
 *      /api/knowledge-graph                                              (stats — no params)
 *
 * Behavior matrix:
 *   - No `cypher`, no `drug`, no `disease`:
 *       Return KG stats (node/edge counts, source list). This is the
 *       dashboard landing path. Uses getKnowledgeGraphStats() — the lib
 *       service that reads the real Phase 2 registry.
 *   - `drug` and/or `disease`:
 *       Structured query. Forwarded to the KG service (if configured) as
 *       a POST /query with a parameterized body. We do NOT let callers
 *       inject raw Cypher via GET.
 *   - `cypher`:
 *       NOT accepted via GET — would be a Cypher-injection vector.
 *       Callers must use POST with the role-gated, whitelisted validator.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const cypher = req.nextUrl.searchParams.get("cypher");
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "100", 10);
  const drug = req.nextUrl.searchParams.get("drug");
  const disease = req.nextUrl.searchParams.get("disease");

  // FE-002: stats path — no structured query params and no cypher.
  // Return real KG stats from the lib service (registry / proxy).
  // This is the path the dashboard's KG explorer landing takes.
  if (!cypher && !drug && !disease) {
    try {
      const stats = await getKnowledgeGraphStats();
      await writeAuditLog({
        user: auth.user,
        action: "kg_stats",
        resource: "kg:stats",
        metadata: {
          backend: stats.source,
          nodeCount: stats.nodeCount,
          edgeCount: stats.edgeCount,
          sourceCount: stats.sources.length,
        },
      });
      // If the lib returned `source: "none"`, return 503 so the dashboard
      // shows a clear "KG not built yet" state. But we NO LONGER return
      // 503 just because the env var is unset — the local registry is a
      // first-class data source.
      if (stats.source === "none") {
        return NextResponse.json(
          {
            error: "service_not_deployed",
            service: "Knowledge Graph Service",
            description:
              "Neo4j-backed multi-modal biomedical knowledge graph (drugs, " +
              "proteins, pathways, diseases, outcomes). Owned by Phase 2 " +
              "of the build plan.",
            reason:
              "Neither KG_SERVICE_URL is set nor a local Phase 2 registry " +
              "was found. Run the Phase 2 KG builder to produce real " +
              "graph statistics, or set KG_SERVICE_URL to proxy to the " +
              "Neo4j service.",
            stats,
            documentation:
              "See Phase 2 of the build plan (Neo4j Knowledge Graph " +
              "Construction). Set KG_SERVICE_URL to enable the proxy, or " +
              "ensure the Phase 2 builder has written its registry to " +
              "phase2/data/registry.json.",
          },
          { status: 503 }
        );
      }
      return NextResponse.json(stats);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      return internalError(`KG stats failed: ${msg}`);
    }
  }

  // Reject raw Cypher via GET — would be a Cypher-injection vector.
  // Callers must use POST /api/knowledge-graph with the role-gated
  // validator (FE-008).
  if (cypher) {
    return NextResponse.json(
      {
        error: "bad_request",
        message:
          "Raw Cypher queries are not accepted via GET (Cypher-injection " +
          "risk). Use POST /api/knowledge-graph with a role-gated, " +
          "whitelisted validator. GET accepts `drug` and `disease` for " +
          "structured queries, or no params for KG stats.",
      },
      { status: 400 }
    );
  }

  // Structured query path — proxy to the KG service. If the service is
  // not configured, return 503 (we cannot answer structured queries from
  // the registry alone — the registry is source-level stats, not a graph
  // query engine).
  const kgUrl = process.env.KG_SERVICE_URL;
  if (!kgUrl) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: "Knowledge Graph Service",
        reason:
          "Structured queries (drug/disease) require the standalone Neo4j " +
          "service. Set KG_SERVICE_URL to enable the proxy. For KG " +
          "statistics (source list, node/edge counts), call GET without " +
          "query params.",
        documentation:
          "See Phase 2 of the build plan (Neo4j Knowledge Graph " +
          "Construction).",
      },
      { status: 503 }
    );
  }

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
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`KG service proxy failed: ${msg}`);
  }
}

/**
 * POST /api/knowledge-graph
 * Body: { cypher: string, params?: Record<string, unknown> }
 *
 * FE-008 ROOT FIX: The POST endpoint previously accepted arbitrary Cypher
 * from ANY authenticated user (including read-only viewers) and forwarded
 * it verbatim to the downstream KG service. This allowed:
 *   - Cross-tenant data exfiltration.
 *   - Destructive writes if the KG service's Neo4j user had write
 *     privileges (CREATE/DELETE/SET/REMOVE/DROP).
 *   - Resource exhaustion via Cartesian-product queries.
 *
 * Fix (defense in depth — every layer is independent):
 *   1. ROLE GATE: only data-scientist / admin / owner can call POST.
 *   2. CYPHER WHITELIST: only read-only MATCH/OPTIONAL MATCH/WITH/RETURN
 *      statements are allowed.
 *   3. TENANT FORWARDING: user_id and org_id are forwarded as parameters
 *      so the KG service can enforce row-level security on its side.
 *   4. COST LIMIT: a hard 30s timeout and a max 1000-row cap on the
 *      upstream call.
 */

const KG_MAX_ROWS = 1000;
const KG_UPSTREAM_TIMEOUT_MS = 30_000;

export async function POST(req: NextRequest) {
  // FE-008 ROOT FIX layer 1: ROLE GATE.
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const roleCheck = await requireRole(auth.user, "data-scientist", "pi", "developer");
  if (roleCheck.user === null) return roleCheck.response;

  // FE-008: KG service must be configured for raw Cypher queries.
  // The local registry cannot answer arbitrary Cypher — it is a
  // source-level stats file, not a graph query engine.
  const kgUrl = process.env.KG_SERVICE_URL;
  if (!kgUrl) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: "Knowledge Graph Service",
        reason:
          "Raw Cypher queries require the standalone Neo4j service. Set " +
          "KG_SERVICE_URL to enable the proxy. For KG statistics, call " +
          "GET /api/knowledge-graph without params.",
        documentation:
          "See Phase 2 of the build plan (Neo4j Knowledge Graph Construction).",
      },
      { status: 503 }
    );
  }

  let body: { cypher?: string; params?: Record<string, unknown> };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_request", message: "Invalid JSON" }, { status: 400 });
  }
  if (!body.cypher || typeof body.cypher !== "string") {
    return badRequest("cypher (string) is required");
  }

  // FE-008 ROOT FIX layer 2: CYPHER WHITELIST.
  const validation = validateReadOnlyCypher(body.cypher);
  if (!validation.ok) {
    await writeAuditLog({
      user: auth.user,
      action: "kg_cypher_rejected",
      resource: "kg:custom_cypher",
      metadata: {
        reason: validation.reason,
        cypherPreview: body.cypher.slice(0, 120),
      },
    });
    return NextResponse.json(
      { error: "cypher_rejected", message: validation.reason },
      { status: 400 }
    );
  }

  // FE-008 ROOT FIX layer 3: TENANT FORWARDING.
  const safeParams: Record<string, unknown> = {
    ...(body.params || {}),
    _user_id: auth.user.userId,
    _org_id: auth.user.orgId || null,
    _max_rows: KG_MAX_ROWS,
  };

  try {
    // FE-008 ROOT FIX layer 4: COST LIMIT (hard timeout).
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), KG_UPSTREAM_TIMEOUT_MS);

    let upstream: Response;
    try {
      upstream = await fetch(`${kgUrl.replace(/\/$/, "")}/cypher`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cypher: body.cypher, params: safeParams }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

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

    // FE-008 ROOT FIX layer 4 (result cap).
    if (Array.isArray(data?.records) && data.records.length > KG_MAX_ROWS) {
      data.records = data.records.slice(0, KG_MAX_ROWS);
      data._truncated = true;
      data._max_rows = KG_MAX_ROWS;
    }
    if (Array.isArray(data?.rows) && data.rows.length > KG_MAX_ROWS) {
      data.rows = data.rows.slice(0, KG_MAX_ROWS);
      data._truncated = true;
      data._max_rows = KG_MAX_ROWS;
    }

    await writeAuditLog({
      user: auth.user,
      action: "kg_cypher",
      resource: "kg:custom_cypher",
      metadata: {
        cypherPreview: body.cypher.slice(0, 80),
        recordCount:
          (data?.records?.length ?? data?.rows?.length ?? 0),
      },
    });
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (e instanceof Error && e.name === "AbortError") {
      return NextResponse.json(
        {
          error: "kg_timeout",
          message: `KG service did not respond within ${KG_UPSTREAM_TIMEOUT_MS / 1000}s. The query is likely too expensive — add a LIMIT clause or narrow the MATCH pattern.`,
        },
        { status: 504 }
      );
    }
    return internalError(`KG service proxy failed: ${msg}`);
  }
}
