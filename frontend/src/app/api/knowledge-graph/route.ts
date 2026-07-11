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
}
