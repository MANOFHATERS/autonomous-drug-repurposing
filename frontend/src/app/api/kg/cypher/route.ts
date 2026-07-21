/**
 * POST /api/kg/cypher — Teammate 8 ROOT FIX.
 *
 * Proxies raw Cypher passthrough requests from the browser to the
 * backend FastAPI service at http://localhost:8000/cypher, which in
 * turn proxies to the Phase 2 KG service at http://localhost:8001/cypher.
 *
 * Request body: { query: string, params?: dict, max_rows?: int }
 * Response 200: { records: [...], row_count: int, truncated: bool,
 *                 max_rows: int, backend: 'neo4j', timeout_seconds: 30 }
 * Response 429: Rate limit exceeded (after 10 req/min per user).
 *
 * SECURITY LAYERS (defense in depth):
 *   1. ROLE GATE (this route): only data_scientist / pi / developer /
 *      admin / owner can call POST. A read-only viewer cannot.
 *   2. CYPHER WHITELIST (backend): only read-only MATCH/OPTIONAL
 *      MATCH/WITH/RETURN/WHERE statements are allowed.
 *   3. RATE LIMIT (backend): 10 req/min per user.
 *   4. SERVER TIMEOUT (Phase 2): hard 30s server-side timeout.
 *   5. ROW CAP (Phase 2): hard 1000-row cap.
 *
 * The previous architecture called Phase 2's /cypher DIRECTLY from
 * the browser (via KG_SERVICE_URL), bypassing layers 1, 2, and 3.
 * A malicious user could run unlimited arbitrary Cypher queries
 * (including expensive ones that would DoS Neo4j) with NO auth gate.
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth, requireRole, writeAuditLog } from "@/lib/api-helpers";
import { proxyToBackend } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  // Layer 1: auth.
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // Layer 1b: role gate — only researcher+ roles can run Cypher.
  const roleCheck = await requireRole(
    auth.user,
    "data_scientist",
    "pi",
    "developer",
  );
  if (roleCheck.user === null) return roleCheck.response;

  // Parse + validate the body.
  let body: { query?: string; params?: Record<string, unknown>; max_rows?: number };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Invalid JSON body." },
      { status: 400 },
    );
  }
  if (typeof body.query !== "string" || body.query.trim().length === 0) {
    return NextResponse.json(
      { error: "bad_request", message: "Body must include a non-empty 'query' string." },
      { status: 400 },
    );
  }

  // The backend's /cypher endpoint accepts {query, params, max_rows}.
  // We forward the body unchanged — the backend will validate the
  // Cypher whitelist and apply the rate limit.
  const result = await proxyToBackend({
    method: "POST",
    path: "/cypher",
    user: auth.user,
    body,
    timeoutMs: 30_000, // matches backend's hard timeout
  });

  // Audit log (best-effort). Always log Cypher calls — they're
  // security-sensitive (read-only but can exfiltrate data).
  try {
    await writeAuditLog({
      user: auth.user,
      action: "kg_cypher_proxy",
      resource: "backend:/cypher",
      metadata: {
        ok: result.ok,
        status: result.status,
        cypherPreview: body.query.slice(0, 80),
      },
    });
  } catch {
    // ignore audit log failures
  }

  return NextResponse.json(result.body, { status: result.status });
}
