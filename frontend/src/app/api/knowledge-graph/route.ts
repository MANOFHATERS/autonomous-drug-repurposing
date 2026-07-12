import { NextRequest, NextResponse } from "next/server";
import { checkKnowledgeGraphAvailability } from "@/lib/services/ml-stubs";
import {
  requireAuth,
  requireRole,
  internalError,
  writeAuditLog,
  badRequest,
} from "@/lib/api-helpers";
// FE-008 ROOT FIX: shared validator extracted so unit tests can exercise it
// without spinning up the route handler.
import { validateReadOnlyCypher } from "./cypher-validator";

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
 * FE-008 ROOT FIX: The POST endpoint previously accepted arbitrary Cypher
 * from ANY authenticated user (including read-only viewers) and forwarded
 * it verbatim to the downstream KG service. This allowed:
 *   - Cross-tenant data exfiltration (a viewer could MATCH any data the
 *     KG service had, including other orgs' data if the KG service was
 *     not multi-tenant-isolated).
 *   - Destructive writes if the KG service's Neo4j user had write
 *     privileges (CREATE/DELETE/SET/REMOVE/DROP).
 *   - Resource exhaustion via Cartesian-product queries like
 *     `MATCH (n) RETURN n, count(*)`.
 *
 * Fix (defense in depth — every layer is independent):
 *   1. ROLE GATE: only data-scientist / admin / owner can call POST.
 *      Viewers and billing roles cannot submit raw Cypher.
 *   2. CYPHER WHITELIST: only read-only MATCH / OPTIONAL MATCH / WITH /
 *      RETURN / WHERE / ORDER BY / SKIP / LIMIT / COUNT / DISTINCT
 *      statements are allowed. Any CREATE/DELETE/SET/REMOVE/MERGE/DROP/
 *      CALL/etc. → 400 rejected before the upstream call is made.
 *   3. TENANT FORWARDING: user_id and org_id are forwarded as parameters
 *      so the KG service can enforce row-level security on its side.
 *   4. COST LIMIT: a hard 30s timeout and a max 1000-row cap on the
 *      upstream call (the KG service is expected to honor a `limit`
 *      parameter; we also enforce a client-side ceiling).
 */

// Maximum number of rows the proxy will accept from the KG service. Even
// if the KG service misbehaves, we never stream more than this back to
// the client.
const KG_MAX_ROWS = 1000;

// Hard timeout for the upstream call. Neo4j queries that take longer than
// this are almost always Cartesian products or full-graph scans.
const KG_UPSTREAM_TIMEOUT_MS = 30_000;

export async function POST(req: NextRequest) {
  // FE-008 ROOT FIX layer 1: ROLE GATE. requireAuth first, then require
  // one of the analytics roles. requireRole implicitly allows admin/owner.
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const roleCheck = await requireRole(auth.user, "data-scientist", "pi", "developer");
  if (roleCheck.user === null) return roleCheck.response;

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
  if (!body.cypher || typeof body.cypher !== "string") {
    return badRequest("cypher (string) is required");
  }

  // FE-008 ROOT FIX layer 2: CYPHER WHITELIST. Reject anything that is
  // not a read-only MATCH/OPTIONAL MATCH/WITH/RETURN query.
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

  // FE-008 ROOT FIX layer 3: TENANT FORWARDING. Inject the caller's
  // user_id and org_id into the params so the KG service can enforce
  // row-level security on its side (Neo4j 5+ supports row-level access
  // rules). We also inject a hard row cap as a defense-in-depth against
  // runaway queries.
  const safeParams: Record<string, unknown> = {
    ...(body.params || {}),
    _user_id: auth.user.userId,
    _org_id: auth.user.orgId || null,
    _max_rows: KG_MAX_ROWS,
  };

  try {
    const kgUrl = process.env.KG_SERVICE_URL!;
    // FE-008 ROOT FIX layer 4: COST LIMIT. Abort signal enforces a hard
    // 30s ceiling on the upstream call.
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

    // FE-008 ROOT FIX layer 4 (result cap): if the KG service returns a
    // list of records, cap it client-side so a misbehaving service can't
    // flood the client.
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
  } catch (e: any) {
    if (e?.name === "AbortError") {
      return NextResponse.json(
        {
          error: "kg_timeout",
          message: `KG service did not respond within ${KG_UPSTREAM_TIMEOUT_MS / 1000}s. The query is likely too expensive — add a LIMIT clause or narrow the MATCH pattern.`,
        },
        { status: 504 }
      );
    }
    return internalError(`KG service proxy failed: ${e.message}`);
  }
}
