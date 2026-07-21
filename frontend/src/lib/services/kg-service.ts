/**
 * Knowledge Graph service — backend-proxy-only (Teammate 8 ROOT FIX).
 *
 * ROOT FIX (forensic, root-level): the previous version of this file
 * called the Phase 2 KG service DIRECTLY via `KG_SERVICE_URL`. This
 * bypassed the backend FastAPI service entirely, meaning:
 *
 *   1. NO AUTH — Phase 2's /cypher endpoint has NO JWT verification.
 *      Any network caller (including a malicious script in a pharma
 *      partner's browser tab) could run arbitrary read-only Cypher
 *      against Neo4j. Exfiltration of cross-tenant data was possible.
 *   2. NO RATE LIMITING — a single runaway Cypher query could
 *      saturate the Neo4j connection pool (100 connections) and DoS
 *      the entire Phase 2 KG service for ALL users.
 *   3. NO AUDIT LOGGING — KG calls were not recorded in the audit
 *      log, breaking the compliance trail required for pharma IT
 *      procurement.
 *   4. PORT COLLISION — `KG_SERVICE_URL` defaulted to port 8001,
 *      which COLLIDES with the backend FastAPI service's default
 *      port. Local-dev setups that ran both services on the same
 *      host saw "address already in use" errors.
 *
 * ROOT FIX: this file is the SINGLE source of truth for KG calls
 * from the frontend. All KG operations go through the Next.js API
 * routes at `/api/kg/*` which proxy to the backend FastAPI service
 * on port 8000. The backend then proxies to the Phase 2 KG service
 * on port 8001, enforcing JWT auth, rate limiting, and audit logging
 * at the backend boundary.
 *
 *   Browser → /api/kg/stats    (Next.js API route)
 *          → http://localhost:8000/kg/stats  (backend FastAPI proxy)
 *          → http://localhost:8001/kg/stats  (Phase 2 KG service)
 *
 * The Next.js API routes at /api/kg/* handle:
 *   - Browser auth (NextAuth.js session cookie → JWT in Authorization header)
 *   - Audit logging to the frontend's Prisma DB
 *
 * The backend FastAPI handles:
 *   - JWT verification (shared secret with NextAuth)
 *   - Rate limiting (10 req/min for /cypher, 100 req/min for /kg/stats)
 *   - Org-scoped header forwarding (X-Org-Id)
 *   - Cypher whitelist enforcement (defense-in-depth)
 *
 * SCIENTIFIC INTEGRITY: KG statistics must reflect the actual graph
 * state. This file NEVER reads a local registry JSON and NEVER
 * fabricates graph statistics. If the backend is unreachable, the
 * functions return a clear `source: "none"` response with a message
 * telling the operator how to start the backend.
 */

import {
  KgStatsResponseSchema,
  KgQueryResponseSchema,
  KgCypherResponseSchema,
  CANONICAL_NODE_TYPES,
  CANONICAL_NODE_TYPE_SET,
  type KgStatsResponse,
  type KgQueryResponse,
  type KgCypherResponse,
  type GraphSourceStat,
  type CanonicalNodeType,
  validateMlResponse,
} from "@/lib/ml-contracts";

// ---------------------------------------------------------------------------
// Public types (kept stable for existing callers)
// ---------------------------------------------------------------------------

export type { KgStatsResponse, KgQueryResponse, KgCypherResponse, GraphSourceStat };
export type { CanonicalNodeType };

// Re-export the canonical node type constants for callers that need them.
export { CANONICAL_NODE_TYPES, CANONICAL_NODE_TYPE_SET };

/**
 * Backward-compat alias for the response shape. The previous
 * `knowledge-graph-stats.ts` exported `KnowledgeGraphStatsResponse`;
 * callers that import that name should continue to work.
 */
export type KnowledgeGraphStatsResponse = KgStatsResponse;

// ---------------------------------------------------------------------------
// Backend URL resolution
// ---------------------------------------------------------------------------
// Teammate 8 ROOT FIX: the frontend now calls the BACKEND FastAPI
// service via /api/kg/* Next.js API routes (same-origin relative URL).
// The Next.js route then proxies to the backend FastAPI on port 8000.
// The KG_SERVICE_URL env var is NO LONGER used by the frontend — it
// is now a BACKEND env var (the backend uses it to find the Phase 2
// service). Removing KG_SERVICE_URL from the frontend eliminates the
// temptation to bypass the backend's auth/rate-limit/audit layer.
//
// The ``API_BASE`` is a relative URL (``/api``) so the browser uses
// the same origin as the frontend (no CORS preflight, cookies are
// sent automatically with ``credentials: 'include'``). The Next.js
// API route at ``/api/kg/stats`` proxies to
// ``http://localhost:8000/kg/stats`` server-side (no CORS because
// it's a Node.js → Python call, not a browser → Python call).
const API_BASE = "/api";

// Service name used in MlContractError messages (kept for backward
// compat with existing audit-log entries).
const SERVICE_NAME = "phase2_kg";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get KG statistics (node/edge counts, per-source breakdown, type counts).
 *
 * Calls `GET /api/kg/stats` (Next.js API route) which proxies to the
 * backend FastAPI service at `http://localhost:8000/kg/stats`, which
 * in turn proxies to the Phase 2 KG service at `http://localhost:8001/kg/stats`.
 *
 * Returns `source: "none"` if the backend is unreachable.
 *
 * Teammate 8 ROOT FIX (canonicalNodeCount): the response now includes
 * ``canonicalNodeCount`` — the count of CANONICAL-type nodes only
 * (Compound, Protein, Pathway, Disease, ClinicalOutcome). The Phase 2
 * service computes this server-side (phase2/service.py:
 * _compute_canonical_node_count); the backend passes it through
 * unchanged. If the backend's response omits ``canonicalNodeCount``
 * (e.g., older Phase 2 deployment), this function derives it
 * client-side by summing the canonical entries in ``nodeTypeCounts``.
 */
export async function getKnowledgeGraphStats(): Promise<KgStatsResponse> {
  // Teammate 8 ROOT FIX: call the BACKEND proxy via the Next.js API
  // route at /api/kg/stats. This is a RELATIVE URL — the browser
  // resolves it against the frontend's origin, so there is no CORS
  // preflight and the NextAuth session cookie is sent automatically
  // (credentials: 'include'). The Next.js route handles auth (verifies
  // the session, mints a JWT) and proxies to the backend.
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/kg/stats`, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch (e) {
    // Network error — the Next.js frontend itself is down or the
    // browser is offline. Surface as a none response with a clear
    // message (do NOT throw — the dashboard should still render).
    const msg = e instanceof Error ? e.message : String(e);
    return {
      sources: [],
      nodeCount: 0,
      edgeCount: 0,
      nodeTypeCounts: {},
      edgeTypeCounts: {},
      nonCanonicalNodeCounts: {},
      source: "none",
      generatedAt: new Date().toISOString(),
      note:
        `Frontend → /api/kg/stats network error: ${msg}. The Next.js ` +
        `frontend server may be down or the browser may be offline. ` +
        `Teammate 8 ROOT FIX: the frontend no longer calls the Phase 2 ` +
        `KG service directly — it calls the backend FastAPI proxy via ` +
        `/api/kg/* Next.js routes.`,
    };
  }

  if (!response.ok) {
    // 4xx/5xx — surface as a none response with the error message.
    // The backend returns 503 when the Phase 2 service is down, 429
    // when rate limited, 401 when unauthenticated, 403 when no org.
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // Response had no JSON body — use the status text.
    }
    const errMsg =
      typeof (errorBody as { detail?: unknown })?.detail === "string"
        ? (errorBody as { detail: string }).detail
        : typeof (errorBody as { message?: unknown })?.message === "string"
          ? (errorBody as { message: string }).message
          : response.statusText;
    return {
      sources: [],
      nodeCount: 0,
      edgeCount: 0,
      nodeTypeCounts: {},
      edgeTypeCounts: {},
      nonCanonicalNodeCounts: {},
      source: "none",
      generatedAt: new Date().toISOString(),
      note: `Backend /api/kg/stats returned ${response.status}: ${errMsg}`,
    };
  }

  // Parse + transform the response. The backend proxies the Phase 2
  // service's response UNCHANGED, so we still need to handle BOTH
  // snake_case (legacy) and camelCase (canonical) fields — the Phase 2
  // service emits both for backward compat.
  const raw = (await response.json()) as Record<string, unknown>;
  const nodeTypes = (raw.node_types ?? raw.nodeTypeCounts ?? raw.nodeTypeCounts ?? {}) as Record<string, number>;
  const edgeTypes = (raw.edge_types ?? raw.edgeTypeCounts ?? raw.edgeTypeCounts ?? {}) as Record<string, number>;
  const sourcesRead = Array.isArray(raw.sources_read)
    ? raw.sources_read
    : Array.isArray(raw.sources)
      ? raw.sources
      : [];

  // Split canonical vs non-canonical node counts. CANONICAL_NODE_TYPE_SET
  // was FIXED in Teammate 8 — it now uses "ClinicalOutcome" (SINGULAR),
  // matching the Phase 2 KG label vocabulary. The previous plural form
  // ("ClinicalOutcomes") silently dropped all ClinicalOutcome nodes
  // from the canonical count.
  const canonicalNodeTypeCounts: Record<string, number> = {};
  const nonCanonicalNodeCounts: Record<string, number> = {};
  for (const [type, count] of Object.entries(nodeTypes)) {
    if (CANONICAL_NODE_TYPE_SET.has(type)) {
      canonicalNodeTypeCounts[type] = count;
    } else {
      nonCanonicalNodeCounts[type] = count;
    }
  }

  // nodeCount: prefer the server-emitted value (the Phase 2 service
  // computes this from Neo4j's MATCH (n) RETURN count(n) — the
  // authoritative total). Fall back to the sum of canonical types
  // (legacy behavior from the old knowledge-graph-stats.ts).
  const nodeCount =
    typeof raw.nodeCount === "number"
      ? raw.nodeCount
      : typeof raw.node_count === "number"
        ? raw.node_count
        : Object.values(canonicalNodeTypeCounts).reduce((s, n) => s + n, 0);

  // Teammate 8 ROOT FIX: canonicalNodeCount — prefer the server-emitted
  // value (Phase 2 service computes this via _compute_canonical_node_count).
  // Fall back to the client-side sum if the server didn't emit it
  // (older Phase 2 deployments).
  const canonicalNodeCount =
    typeof raw.canonicalNodeCount === "number"
      ? raw.canonicalNodeCount
      : typeof raw.canonical_node_count === "number"
        ? raw.canonical_node_count
        : Object.values(canonicalNodeTypeCounts).reduce((s, n) => s + n, 0);

  const edgeCount =
    typeof raw.edgeCount === "number"
      ? raw.edgeCount
      : typeof raw.edge_count === "number"
        ? raw.edge_count
        : Object.values(edgeTypes).reduce((s, n) => s + n, 0);

  // Map the Python sources_read array (list of source name strings) OR
  // the canonical sources array (list of {name, loaded} objects) into
  // the frontend's GraphSourceStat shape.
  const sources: GraphSourceStat[] = sourcesRead.map((name: unknown) => {
    // If the entry is already a GraphSourceStat object, pass through.
    if (typeof name === "object" && name !== null && "name" in name) {
      const obj = name as { name: unknown; loaded?: unknown };
      return {
        name: String(obj.name),
        loaded: typeof obj.loaded === "boolean" ? obj.loaded : true,
      };
    }
    // Bare source-name string.
    return { name: String(name), loaded: true };
  });

  // SH-026 ROOT FIX: preserve the ``source`` enum ("neo4j" | "in_memory")
  // from the Phase 2 service. Never collapse to "kg_service" (the
  // previous tautology bug).
  const rawSource =
    typeof raw.source === "string" ? raw.source : "";
  const rawBackend =
    typeof raw.backend === "string" ? raw.backend : "";
  const source: string =
    rawSource === "neo4j" || rawSource === "in_memory"
      ? rawSource
      : rawBackend === "neo4j"
        ? "neo4j"
        : "in_memory";
  // ``backend`` kept for the note (backward-compat diagnostic label).
  const backend = rawBackend || rawSource || "kg_service";

  const transformed: KgStatsResponse = {
    sources,
    nodeCount,
    canonicalNodeCount,
    edgeCount,
    nodeTypeCounts: canonicalNodeTypeCounts,
    edgeTypeCounts: edgeTypes,
    nonCanonicalNodeCounts,
    source,
    // SH-026: prefer the server-authoritative timestamp.
    generatedAt:
      typeof raw.generatedAt === "string"
        ? raw.generatedAt
        : typeof raw.last_updated === "string"
          ? raw.last_updated
          : new Date().toISOString(),
    note:
      typeof raw.note === "string"
        ? raw.note
        : `Served from Phase 2 KG service via backend proxy (backend=${backend}).`,
  };

  // Validate the final shape against the Zod schema (defense-in-depth —
  // catches any drift between the backend response and the contract).
  return KgStatsResponseSchema.parse(transformed);
}

/**
 * Explore the subgraph around a drug or disease node.
 *
 * Calls `POST /api/kg/explore` (Next.js API route) which proxies to
 * the backend FastAPI service at `http://localhost:8000/kg/explore`,
 * which in turn proxies to the Phase 2 KG service at
 * `http://localhost:8001/kg/explore`.
 */
export async function exploreKnowledgeGraph(opts: {
  drug?: string;
  disease?: string;
  limit?: number;
}): Promise<KgQueryResponse> {
  const body: Record<string, unknown> = { limit: opts.limit ?? 50 };
  if (opts.drug) body.drug = opts.drug;
  if (opts.disease) body.disease = opts.disease;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/kg/explore`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(
      `Frontend → /api/kg/explore network error: ${msg}. ` +
        `Teammate 8 ROOT FIX: the frontend no longer calls the Phase 2 ` +
        `KG service directly.`,
    );
  }

  if (!response.ok) {
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // ignore
    }
    const errMsg =
      typeof (errorBody as { detail?: unknown })?.detail === "string"
        ? (errorBody as { detail: string }).detail
        : response.statusText;
    throw new Error(
      `Backend /api/kg/explore returned ${response.status}: ${errMsg}`,
    );
  }

  const json = await response.json();
  return validateMlResponse(
    SERVICE_NAME,
    "/kg/explore",
    KgQueryResponseSchema,
    json,
  );
}

/**
 * Structured query — get the subgraph centered on a drug/disease.
 *
 * Calls `POST /api/kg/explore` (same as exploreKnowledgeGraph — the
 * backend's /kg/explore endpoint accepts both ?drug=&disease= query
 * params and a JSON body). Kept as a separate function for backward
 * compat with callers that expect the ``queryKnowledgeGraph`` name.
 */
export async function queryKnowledgeGraph(opts: {
  drug?: string;
  disease?: string;
  limit?: number;
}): Promise<KgQueryResponse> {
  // Delegate to exploreKnowledgeGraph (Teampate 8 unified the two
  // paths — they both call POST /api/kg/explore now).
  return exploreKnowledgeGraph(opts);
}

/**
 * Raw Cypher passthrough — role-gated on the route side.
 *
 * Calls `POST /api/kg/cypher` (Next.js API route) which proxies to
 * the backend FastAPI service at `http://localhost:8000/cypher`,
 * which in turn proxies to the Phase 2 KG service at
 * `http://localhost:8001/cypher`. The backend enforces a 10 req/min
 * per-user rate limit; the Phase 2 service applies a read-only
 * Cypher whitelist (MATCH/OPTIONAL MATCH/WITH/RETURN/WHERE only)
 * and a 30s timeout + 1000-row cap.
 *
 * SECURITY: the caller MUST validate the Cypher query is read-only
 * BEFORE calling this function. The backend's rate limit and the
 * Phase 2 service's whitelist are defense-in-depth, not the primary
 * gate. The Next.js API route at /api/kg/cypher enforces role-based
 * access (data_scientist / pi / developer only).
 */
export async function executeCypher(opts: {
  cypher: string;
  params?: Record<string, unknown>;
  timeoutMs?: number;
}): Promise<KgCypherResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/kg/cypher`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        query: opts.cypher,
        params: opts.params || {},
        // Note: the timeoutMs is enforced server-side by the backend's
        // 30s hard timeout. We don't pass it in the body because the
        // backend ignores client-side timeout hints (a malicious
        // client could otherwise disable the timeout).
      }),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(
      `Frontend → /api/kg/cypher network error: ${msg}`,
    );
  }

  if (!response.ok) {
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // ignore
    }
    const errMsg =
      typeof (errorBody as { detail?: unknown })?.detail === "string"
        ? (errorBody as { detail: string }).detail
        : response.statusText;
    throw new Error(
      `Backend /api/kg/cypher returned ${response.status}: ${errMsg}`,
    );
  }

  const json = await response.json();
  return validateMlResponse(
    SERVICE_NAME,
    "/cypher",
    KgCypherResponseSchema,
    json,
  );
}

/**
 * Check whether a drug or disease name appears in the KG.
 *
 * Uses /api/kg/explore with a limit of 1 to check existence without
 * fetching the full subgraph. Returns true if the backend finds at
 * least one node matching the name.
 */
export async function validateEntityInKg(
  name: string,
  kind: "drug" | "disease",
): Promise<{ ok: boolean; reason?: string }> {
  const trimmed = name.trim();
  if (!trimmed) {
    return { ok: false, reason: `${kind} name is empty` };
  }

  try {
    // Use exploreKnowledgeGraph with limit=1 to check existence.
    const result = await exploreKnowledgeGraph({
      [kind]: trimmed,
      limit: 1,
    });
    const nodes = Array.isArray(result?.nodes) ? result.nodes : [];
    if (nodes.length === 0) {
      return {
        ok: false,
        reason: `${kind.charAt(0).toUpperCase() + kind.slice(1)} "${trimmed}" was not found in the knowledge graph.`,
      };
    }
    return { ok: true };
  } catch (e) {
    // BE-015 v123 FORENSIC ROOT FIX: fail-closed. The previous code
    // returned { ok: true } on any error — silently bypassing the
    // MANDATORY KG validation that the evidence-package route relies
    // on. An error means the backend is unreachable OR the response
    // was malformed — either way, we cannot confirm the entity exists
    // in the KG, so we MUST refuse.
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(
      `kg-service: validateEntityInKg for ${kind}="${trimmed}" threw (backend unreachable): ${msg}`,
    );
    // Honor the explicit dev bypass (local dev only — never in prod).
    if (process.env.DRUGOS_ALLOW_KG_BYPASS === "1") {
      return { ok: true, reason: "kg_bypass_dev_mode" };
    }
    return {
      ok: false,
      reason: "kg_service_unavailable",
    };
  }
}

/**
 * Health check — useful for the /api/system/status route.
 *
 * Calls `GET /api/kg/health` (Next.js API route) which proxies to
 * the backend FastAPI service's /health endpoint.
 */
export async function checkKgHealth(): Promise<{
  configured: boolean;
  reachable: boolean;
  neo4jConfigured: boolean;
  version?: string;
}> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/kg/health`, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch {
    return { configured: false, reachable: false, neo4jConfigured: false };
  }
  if (!response.ok) {
    return { configured: true, reachable: false, neo4jConfigured: false };
  }
  const body = (await response.json()) as Record<string, unknown>;
  return {
    configured: true,
    reachable: true,
    neo4jConfigured: Boolean(body?.neo4j_configured),
    version: typeof body?.version === "string" ? body.version : undefined,
  };
}
