/**
 * Knowledge Graph stats service — Phase 2 handoff.
 *
 * FE-020 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE (forensic): The previous implementation summed each source's
 * `rows` field into `nodeCount`. For SIDER, `rows: 91926` represents
 * AdverseEvent records (side-effect mentions), NOT canonical KG nodes.
 * The dashboard then displayed "91,926 nodes" — misleading a researcher
 * into believing the KG was mostly side-effects. Simultaneously, STRING
 * with `edge_count: 0` and `loaded: false` was reported as "0 STRING
 * edges" with no distinction between "STRING failed to load" vs.
 * "STRING loaded but produced 0 edges".
 *
 * ROOT FIX:
 *   1. `registry.json` now carries `node_type_counts` and `edge_type_counts`
 *      per source, breaking down contributions by canonical type.
 *   2. This service reads the new schema and aggregates by type — producing
 *      `nodeTypeCounts` and `edgeTypeCounts` maps in the response.
 *   3. `nodeCount` is now the sum of CANONICAL node types ONLY
 *      (Compound, Protein, Pathway, Disease, ClinicalOutcomes) — excluding
 *      non-canonical types like AdverseEvent. Non-canonical counts are
 *      surfaced separately under `nonCanonicalNodeCounts` so the dashboard
 *      can display them transparently without conflating them with the
 *      KG's core entity count.
 *   4. `edgeCount` is the sum of all `edge_type_counts` values (edges are
 *      not canonical vs. non-canonical — they all represent graph
 *      relationships, including `(Compound, causes, AdverseEvent)`).
 *   5. The legacy `rows` field is preserved per-source for transparency
 *      but is NOT used to compute `nodeCount` anymore.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate graph statistics. If the
 * registry is missing we return `source: "none"` with an empty list —
 * the dashboard then shows "Knowledge graph has not been built yet"
 * instead of fake node/edge counts.
 */

import { promises as fs } from "fs";
import path from "path";

/**
 * The 5 canonical node types per the project docx (Phase 2 — Knowledge
 * Graph Construction). Any node type NOT in this set is considered
 * non-canonical (e.g. AdverseEvent, Gene) and is excluded from the
 * canonical `nodeCount` — but still surfaced in `nonCanonicalNodeCounts`
 * for full transparency.
 */
export const CANONICAL_NODE_TYPES = [
  "Compound",
  "Protein",
  "Pathway",
  "Disease",
  "ClinicalOutcomes",
] as const;

export type CanonicalNodeType = (typeof CANONICAL_NODE_TYPES)[number];

export interface GraphSourceStat {
  name: string;
  loaded: boolean;
  loadedReason?: string;
  version?: string;
  rows?: number;
  edgeCount?: number;
  sha256?: string;
  producedAt?: string;
  producedBy?: string;
  loadId?: string;
  /** Per-source breakdown of node types contributed (FE-020). */
  nodeTypeCounts?: Record<string, number>;
  /** Per-source breakdown of edge types contributed (FE-020). */
  edgeTypeCounts?: Record<string, number>;
}

export interface KnowledgeGraphStatsResponse {
  sources: GraphSourceStat[];
  /**
   * Sum of canonical node types ONLY (Compound + Protein + Pathway +
   * Disease + ClinicalOutcomes) across all sources. Excludes
   * AdverseEvent and other non-canonical types.
   */
  nodeCount: number;
  /**
   * Sum of all edge_type_counts values across all sources. Edges are
   * not canonical/non-canonical — they all represent real graph
   * relationships.
   */
  edgeCount: number;
  /** Per-type breakdown of canonical node counts (FE-020). */
  nodeTypeCounts: Record<string, number>;
  /** Per-type breakdown of edge counts (FE-020). */
  edgeTypeCounts: Record<string, number>;
  /**
   * Per-type breakdown of NON-canonical node counts (e.g. AdverseEvent).
   * Surfaced for transparency — NOT included in `nodeCount`.
   */
  nonCanonicalNodeCounts: Record<string, number>;
  source: "kg_service" | "local_registry" | "none";
  generatedAt: string;
  note?: string;
}

const DEFAULT_REGISTRY_PATH = path.resolve(
  process.cwd(),
  "..",
  "phase2",
  "data",
  "registry.json"
);

const CANONICAL_SET: ReadonlySet<string> = new Set(CANONICAL_NODE_TYPES);

/**
 * Merge a per-source type-counts map into an accumulator.
 */
function mergeTypeCounts(
  acc: Record<string, number>,
  src: Record<string, number> | undefined
): void {
  if (!src) return;
  for (const [k, v] of Object.entries(src)) {
    if (typeof v === "number" && Number.isFinite(v)) {
      acc[k] = (acc[k] || 0) + v;
    }
  }
}

async function readLocalRegistry(
  registryPath: string
): Promise<KnowledgeGraphStatsResponse | null> {
  let content: string;
  try {
    content = await fs.readFile(registryPath, "utf8");
  } catch {
    return null;
  }
  let body: any;
  try {
    body = JSON.parse(content);
  } catch {
    return null;
  }

  const sources: GraphSourceStat[] = Object.entries(body || {}).map(
    ([name, v]: [string, any]) => ({
      name,
      loaded: !!v?.loaded,
      loadedReason: v?.loaded_reason,
      version: v?.version,
      rows: typeof v?.rows === "number" ? v.rows : undefined,
      edgeCount: typeof v?.edge_count === "number" ? v.edge_count : undefined,
      sha256: v?.sha256,
      producedAt: v?.produced_at || v?.parsed_at,
      producedBy: v?.produced_by,
      loadId: v?.load_id,
      nodeTypeCounts:
        v?.node_type_counts && typeof v.node_type_counts === "object"
          ? { ...v.node_type_counts }
          : undefined,
      edgeTypeCounts:
        v?.edge_type_counts && typeof v.edge_type_counts === "object"
          ? { ...v.edge_type_counts }
          : undefined,
    })
  );

  // FE-020: aggregate node/edge type counts across all sources.
  const nodeTypeCounts: Record<string, number> = {};
  const edgeTypeCounts: Record<string, number> = {};
  for (const s of sources) {
    mergeTypeCounts(nodeTypeCounts, s.nodeTypeCounts);
    mergeTypeCounts(edgeTypeCounts, s.edgeTypeCounts);
  }

  // Split canonical vs non-canonical node counts.
  const canonicalNodeTypeCounts: Record<string, number> = {};
  const nonCanonicalNodeCounts: Record<string, number> = {};
  for (const [type, count] of Object.entries(nodeTypeCounts)) {
    if (CANONICAL_SET.has(type)) {
      canonicalNodeTypeCounts[type] = count;
    } else {
      nonCanonicalNodeCounts[type] = count;
    }
  }

  // FE-020: nodeCount = sum of CANONICAL node types only.
  const nodeCount = Object.values(canonicalNodeTypeCounts).reduce(
    (s, n) => s + n,
    0
  );

  // edgeCount = sum of ALL edge types (canonical + non-canonical edges,
  // e.g. (Compound, causes, AdverseEvent) is a real edge even though
  // AdverseEvent is a non-canonical node).
  const edgeCount = Object.values(edgeTypeCounts).reduce((s, n) => s + n, 0);

  return {
    sources,
    nodeCount,
    edgeCount,
    nodeTypeCounts: canonicalNodeTypeCounts,
    edgeTypeCounts,
    nonCanonicalNodeCounts,
    source: "local_registry",
    generatedAt: new Date().toISOString(),
    note:
      "Served from local Phase 2 registry. Canonical node count excludes " +
      "non-canonical types (e.g. AdverseEvent) per the 5-type contract " +
      "defined in the project docx (Phase 2 — Knowledge Graph Construction).",
  };
}

async function proxyToKgService(
  url: string
): Promise<KnowledgeGraphStatsResponse> {
  const fullUrl = `${url.replace(/\/$/, "")}/stats`;
  const res = await fetch(fullUrl, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`KG service at ${url} returned ${res.status}`);
  }
  const body = await res.json();
  return {
    sources: body?.sources || [],
    nodeCount: body?.nodeCount ?? 0,
    edgeCount: body?.edgeCount ?? 0,
    nodeTypeCounts: body?.nodeTypeCounts || {},
    edgeTypeCounts: body?.edgeTypeCounts || {},
    nonCanonicalNodeCounts: body?.nonCanonicalNodeCounts || {},
    source: "kg_service",
    generatedAt: body?.generatedAt || new Date().toISOString(),
  };
}

export async function getKnowledgeGraphStats(): Promise<KnowledgeGraphStatsResponse> {
  // 1. Proxy path.
  const serviceUrl = process.env.KG_SERVICE_URL;
  if (serviceUrl) {
    try {
      return await proxyToKgService(serviceUrl);
    } catch (e) {
      console.warn(
        "KG service proxy failed, falling back to local registry:",
        e
      );
    }
  }

  // 2. Local registry path.
  const registryPath = process.env.KG_REGISTRY_PATH || DEFAULT_REGISTRY_PATH;
  const stats = await readLocalRegistry(registryPath);
  if (stats) return stats;

  // 3. No data available.
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
      "No knowledge graph statistics available. Set KG_SERVICE_URL to " +
      `proxy to the Neo4j service, or ensure the Phase 2 pipeline has ` +
      `written its registry to ${registryPath}.`,
  };
}
