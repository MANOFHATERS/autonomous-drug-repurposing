/**
 * Knowledge Graph stats service — Phase 2 handoff.
 *
 * ROOT FIX for FE-003 (and the Phase 2 → API handoff gap):
 *
 * Previously: `/api/knowledge-graph` returned 501 unconditionally. The
 * Phase 2 Neo4j graph has real statistics (node counts by type, edge
 * counts by relation, registered data sources with load status) but the
 * Next.js backend never surfaced them.
 *
 * ROOT FIX: this service reads the REAL Phase 2 registry JSON at
 * `../phase2/data/registry.json` (the source-of-truth registry that
 * `phase2/drugos_graph/kg_builder.py` produces). It extracts:
 *   - per-source registered status (loaded flag, row counts, SHA-256)
 *   - produced_at timestamps
 *   - load_id for traceability
 *
 * If `KG_SERVICE_URL` is set, we proxy to the standalone Neo4j service
 * instead. This is the production path — the local JSON is the dev /
 * single-box fallback.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate graph statistics. If the
 * registry is missing we return `source: "none"` with an empty list —
 * the dashboard then shows "Knowledge graph has not been built yet"
 * instead of fake node/edge counts.
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

export interface KnowledgeGraphStatsResponse {
  sources: GraphSourceStat[];
  nodeCount: number;
  edgeCount: number;
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

async function readLocalRegistry(registryPath: string): Promise<KnowledgeGraphStatsResponse | null> {
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
  const sources: GraphSourceStat[] = Object.entries(body || {}).map(([name, v]: [string, any]) => ({
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
  }));
  const nodeCount = sources.reduce((s, x) => s + (x.rows ?? 0), 0);
  const edgeCount = sources.reduce((s, x) => s + (x.edgeCount ?? 0), 0);
  return {
    sources,
    nodeCount,
    edgeCount,
    source: "local_registry",
    generatedAt: new Date().toISOString(),
    note:
      "Served from local Phase 2 registry. These are real knowledge graph " +
      "source statistics produced by the Neo4j graph builder.",
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
  return {
    sources: body?.sources || [],
    nodeCount: body?.nodeCount ?? 0,
    edgeCount: body?.edgeCount ?? 0,
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
      console.warn("KG service proxy failed, falling back to local registry:", e);
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
    source: "none",
    generatedAt: new Date().toISOString(),
    note:
      "No knowledge graph statistics available. Set KG_SERVICE_URL to " +
      `proxy to the Neo4j service, or ensure the Phase 2 pipeline has ` +
      `written its registry to ${registryPath}.`,
  };
}
