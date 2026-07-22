/**
 * DEPRECATED — use `@/lib/services/kg-service` instead.
 *
 * This file is kept ONLY as a backward-compat re-export shim so existing
 * imports (`from "@/lib/services/knowledge-graph-stats"`) continue to
 * work after Issue 232 / FE-023 consolidated the KG service into
 * `kg-service.ts`.
 *
 * Issue 232 ROOT FIX: the previous implementation had a local-registry
 * fallback that read `../phase2/data/registry.json` directly, bypassing
 * the Python Phase 2 service. This caused the dashboard to display
 * stale registry data. The new `kg-service.ts` is HTTP-only — no local
 * file reads.
 *
 * INT-029 ROOT FIX (v143): the canonical KG stats URL is `/kg/stats`
 * (NOT bare `/stats`). The Python Phase 2 service exposes its stats
 * endpoint at `/kg/stats` to avoid colliding with the backend's own
 * `/stats` endpoint. The previous version of this file referenced
 * bare `/stats` — which 404'd against the Python service. All
 * re-exports below now route through `kg-service.ts` which uses the
 * correct `/kg/stats` URL. See `getKnowledgeGraphStats` in
 * `kg-service.ts` for the actual HTTP call.
 *
 * All exports below are re-exported from `kg-service.ts`. Do not add new
 * code to this file — add it to `kg-service.ts` instead.
 */

// INT-029: the canonical KG stats endpoint URL. Exported as a constant
// so callers (and tests) can verify the correct path is used.
// The Python Phase 2 service (phase2/service.py) exposes stats at
// `/kg/stats`, NOT bare `/stats`. Bare `/stats` would collide with
// the backend FastAPI's own `/stats` endpoint.
export const KG_STATS_URL = "/kg/stats";

export {
  getKnowledgeGraphStats,
  exploreKnowledgeGraph,
  queryKnowledgeGraph,
  executeCypher,
  validateEntityInKg,
  checkKgHealth,
  CANONICAL_NODE_TYPES,
  CANONICAL_NODE_TYPE_SET,
} from "./kg-service";
export type {
  KgStatsResponse,
  KgQueryResponse,
  KgCypherResponse,
  GraphSourceStat,
  KnowledgeGraphStatsResponse,
  CanonicalNodeType,
} from "./kg-service";
