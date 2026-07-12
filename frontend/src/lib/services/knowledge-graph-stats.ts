/**
 * Knowledge Graph stats service — Phase 2 handoff.
 *
 * ROOT FIX for FE-002 (Team Member 13):
 *
 * Previously: `/api/knowledge-graph` returned 503 unconditionally when
 * `KG_SERVICE_URL` was unset. The lib service `knowledge-graph-stats.ts`
 * (this file) was DEAD CODE — the route never imported or called it.
 *
 * ROOT FIX: this service is now the single source of truth for KG stats.
 * It is wired into `/api/knowledge-graph/route.ts` as the fallback when
 * `KG_SERVICE_URL` is not set. The route calls `getKnowledgeGraphStats()`
 * directly.
 *
 * BEYOND THE ORIGINAL FE-002 ISSUE (root-level fix, not surface fix):
 * The original `registry.json` only contains SIDER (91,926 side-effects
 * misreported as 'nodes') and STRING (0 edges, loaded: false) — NOT the
 * bridge's Compound/Protein/Disease/Pathway node counts. The audit
 * explicitly calls this out: "registry.json only contains SIDER (91,926
 * side-effects misreported as 'nodes') and STRING (0 edges, loaded: false)
 * — NOT the bridge's Compound/Protein/Disease/Pathway nodes."
 *
 * ROOT FIX: this service now reads BOTH:
 *   1. `../phase2/data/registry.json` — per-source load status (SIDER,
 *      STRING, ChEMBL, DrugBank, etc.) with row counts.
 *   2. `../phase2/data/checkpoints/step_01.json` — the bridge summary
 *      with the REAL Phase 2 node/edge counts by type (Compound,
 *      Protein, Disease, Pathway, ClinicalOutcome) and edge types
 *      (Compound→Protein, Gene→Disease, etc.).
 *
 * The dashboard's KG explorer page now shows:
 *   - Per-source registered status (loaded flag, row counts, SHA-256)
 *   - Real bridge-level node/edge counts (not SIDER row counts
 *     mislabeled as "nodes")
 *   - Edge type breakdown (Compound→Protein, Compound→Disease, etc.)
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate graph statistics. If both
 * files are missing we return `source: "none"` with empty lists — the
 * dashboard then shows "Knowledge graph has not been built yet" instead
 * of fake node/edge counts.
 */

import { promises as fs } from "fs";
import path from "path";

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
}

export interface BridgeNodeEdgeStats {
  /**
   * Real Phase 2 node counts from the bridge summary. These are the
   * Compound/Protein/Disease/Pathway/ClinicalOutcome counts produced
   * by the Phase 1 → Phase 2 bridge — NOT SIDER row counts.
   */
  nodesStaged: number;
  nodesLoaded: number;
  edgesStaged: number;
  edgesLoaded: number;
  /**
   * Edge type breakdown from the bridge summary. Each entry is a
   * Cypher-style "(SourceType, RELATION, TargetType)" string, e.g.
   * "(Compound, inhibits, Protein)".
   */
  edgeTypesPresent: string[];
  bridgeVersion?: string;
  backend?: string;
}

export interface KnowledgeGraphStatsResponse {
  sources: GraphSourceStat[];
  /**
   * Total node count. When the bridge summary is available, this is
   * `bridge.nodesLoaded` (the real Phase 2 count). Otherwise, it falls
   * back to the sum of per-source rows (which over-counts because SIDER
   * rows are side-effects, not graph nodes). The `nodeCountSource` field
   * tells the caller which one was used.
   */
  nodeCount: number;
  /**
   * Total edge count. Same logic as nodeCount.
   */
  edgeCount: number;
  /**
   * Edge type breakdown from the bridge summary. Empty if the bridge
   * summary is missing.
   */
  edgeTypesPresent: string[];
  /**
   * Bridge-level stats from the Phase 1 → Phase 2 handoff. Null if the
   * checkpoint is missing.
   */
  bridge: BridgeNodeEdgeStats | null;
  source: "kg_service" | "local_registry" | "none";
  nodeCountSource: "bridge_summary" | "registry_rows_sum" | "none";
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

const DEFAULT_CHECKPOINT_PATH = path.resolve(
  process.cwd(),
  "..",
  "phase2",
  "data",
  "checkpoints",
  "step_01.json"
);

async function readJsonFile(filePath: string): Promise<any | null> {
  let content: string;
  try {
    content = await fs.readFile(filePath, "utf8");
  } catch {
    return null;
  }
  try {
    return JSON.parse(content);
  } catch {
    return null;
  }
}

/**
 * Read the Phase 2 registry. Returns per-source load status.
 * The registry is a flat object: { [sourceName]: { loaded, rows, edge_count, ... } }.
 */
async function readLocalRegistry(registryPath: string): Promise<{
  sources: GraphSourceStat[];
  registryRowsSum: number;
  registryEdgesSum: number;
} | null> {
  const body = await readJsonFile(registryPath);
  if (!body || typeof body !== "object") return null;

  const sources: GraphSourceStat[] = Object.entries(body).map(
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
    })
  );

  // Sum of per-source rows. NOTE: this is NOT the graph node count —
  // SIDER's 91,926 rows are side-effects, not graph nodes. We surface
  // it as a fallback only when the bridge summary is unavailable.
  const registryRowsSum = sources.reduce((s, x) => s + (x.rows ?? 0), 0);
  const registryEdgesSum = sources.reduce((s, x) => s + (x.edgeCount ?? 0), 0);

  return { sources, registryRowsSum, registryEdgesSum };
}

/**
 * Read the Phase 1 → Phase 2 bridge summary from the checkpoint JSON.
 * This contains the REAL graph node/edge counts (Compound, Protein,
 * Disease, Pathway, ClinicalOutcome) — not SIDER row counts.
 */
async function readBridgeSummary(
  checkpointPath: string
): Promise<BridgeNodeEdgeStats | null> {
  const body = await readJsonFile(checkpointPath);
  if (!body || typeof body !== "object") return null;
  const bridge = body?.step1?.bridge_summary;
  if (!bridge || typeof bridge !== "object") return null;
  return {
    nodesStaged: typeof bridge.nodes_staged === "number" ? bridge.nodes_staged : 0,
    nodesLoaded: typeof bridge.nodes_loaded === "number" ? bridge.nodes_loaded : 0,
    edgesStaged: typeof bridge.edges_staged === "number" ? bridge.edges_staged : 0,
    edgesLoaded: typeof bridge.edges_loaded === "number" ? bridge.edges_loaded : 0,
    edgeTypesPresent: Array.isArray(bridge.edge_types_present)
      ? bridge.edge_types_present
      : [],
    bridgeVersion: bridge.bridge_version,
    backend: bridge.backend,
  };
}

async function proxyToKgService(url: string): Promise<KnowledgeGraphStatsResponse> {
  const fullUrl = `${url.replace(/\/$/, "")}/stats`;
  const res = await fetch(fullUrl, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`KG service at ${url} returned ${res.status}`);
  }
  const body = await res.json();
  // The KG service is the authoritative source — if it returns bridge
  // stats, use them; otherwise fall back to its top-level counts.
  return {
    sources: body?.sources || [],
    nodeCount: body?.nodeCount ?? body?.bridge?.nodesLoaded ?? 0,
    edgeCount: body?.edgeCount ?? body?.bridge?.edgesLoaded ?? 0,
    edgeTypesPresent: body?.edgeTypesPresent || body?.bridge?.edgeTypesPresent || [],
    bridge: body?.bridge || null,
    source: "kg_service",
    nodeCountSource: body?.bridge ? "bridge_summary" : "registry_rows_sum",
    generatedAt: body?.generatedAt || new Date().toISOString(),
  };
}

export async function getKnowledgeGraphStats(): Promise<KnowledgeGraphStatsResponse> {
  // 1. Proxy path — production.
  const serviceUrl = process.env.KG_SERVICE_URL;
  if (serviceUrl) {
    try {
      return await proxyToKgService(serviceUrl);
    } catch (e) {
      console.warn("KG service proxy failed, falling back to local registry + bridge:", e);
    }
  }

  // 2. Local path — read BOTH the registry (per-source load status) AND
  // the bridge summary (real graph node/edge counts by type). The audit
  // explicitly warned that the registry alone mislabels SIDER row counts
  // as "nodes" — the bridge summary is the only authoritative source for
  // graph node/edge counts.
  const registryPath = process.env.KG_REGISTRY_PATH || DEFAULT_REGISTRY_PATH;
  const checkpointPath = process.env.DATASET_CHECKPOINT_PATH || DEFAULT_CHECKPOINT_PATH;

  const [registryResult, bridge] = await Promise.all([
    readLocalRegistry(registryPath),
    readBridgeSummary(checkpointPath),
  ]);

  if (!registryResult && !bridge) {
    // 3. No data available — neither registry nor checkpoint.
    return {
      sources: [],
      nodeCount: 0,
      edgeCount: 0,
      edgeTypesPresent: [],
      bridge: null,
      source: "none",
      nodeCountSource: "none",
      generatedAt: new Date().toISOString(),
      note:
        "No knowledge graph statistics available. Set KG_SERVICE_URL to " +
        `proxy to the Neo4j service, or ensure the Phase 2 pipeline has ` +
        `written its registry to ${registryPath} and its bridge summary ` +
        `to ${checkpointPath}.`,
    };
  }

  const sources = registryResult?.sources || [];
  // Prefer the bridge summary's node/edge counts (real graph counts by
  // type). Fall back to the registry rows sum (which over-counts because
  // SIDER rows are side-effects) only when the bridge is missing.
  const nodeCount = bridge ? bridge.nodesLoaded : (registryResult?.registryRowsSum ?? 0);
  const edgeCount = bridge ? bridge.edgesLoaded : (registryResult?.registryEdgesSum ?? 0);
  const edgeTypesPresent = bridge?.edgeTypesPresent || [];
  const nodeCountSource: "bridge_summary" | "registry_rows_sum" =
    bridge ? "bridge_summary" : "registry_rows_sum";

  return {
    sources,
    nodeCount,
    edgeCount,
    edgeTypesPresent,
    bridge,
    source: "local_registry",
    nodeCountSource,
    generatedAt: new Date().toISOString(),
    note:
      "Served from local Phase 2 registry + Phase 1 → Phase 2 bridge " +
      "summary. Per-source load status comes from the registry; graph " +
      "node/edge counts by type come from the bridge summary " +
      "(Compound/Protein/Disease/Pathway/ClinicalOutcome). " +
      (bridge
        ? `Bridge: ${bridge.nodesLoaded} nodes, ${bridge.edgesLoaded} edges loaded.`
        : "Bridge summary not found — node/edge counts fall back to the sum of per-source rows (over-counts because SIDER rows are side-effects, not graph nodes)."),
  };
}
