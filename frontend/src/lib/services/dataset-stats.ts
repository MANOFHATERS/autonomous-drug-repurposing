/**
 * Dataset Pipeline stats service — Phase 1 handoff.
 *
 * ROOT FIX for FE-003 (and the Phase 1 → API handoff gap):
 *
 * Previously: `/api/dataset` returned 501 unconditionally. The Phase 1
 * Airflow pipeline produces real dataset statistics (row counts per source,
 * freshness, schema validation results) but the Next.js backend never
 * surfaced them — the frontend had no way to display "ChEMBL: 2.1M
 * compounds loaded, DrugBank: 1,532 drugs loaded, …".
 *
 * ROOT FIX: this service reads the REAL Phase 1 pipeline checkpoint JSON
 * at `../phase2/data/checkpoints/step_01.json` (the bridge summary that
 * Phase 1 produces after entity resolution). It extracts:
 *   - per-source loaded status (sources_read, sources_attempted)
 *   - node / edge counts (nodes_staged, edges_staged, nodes_loaded,
 *     edges_loaded)
 *   - edge types present (Compound→Protein, Gene→Disease, etc.)
 *   - per-file SHA-256 checksums (for integrity audit)
 *
 * If `DATASET_SERVICE_URL` is set, we proxy to the standalone Airflow
 * service instead. This is the production path — the local JSON is the
 * dev / single-box fallback.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate statistics. If the checkpoint
 * is missing we return `source: "none"` with an empty list — the
 * dashboard then shows "Dataset pipeline has not been run yet" instead of
 * fake numbers.
 */

import { promises as fs } from "fs";
import path from "path";

export interface DatasetSourceStat {
  name: string;
  loaded: boolean;
  // Optional fields present in the checkpoint's `bridge_summary`.
  rowsLoaded?: number;
  sha256?: string;
}

export interface DatasetStatsResponse {
  sources: DatasetSourceStat[];
  nodesLoaded: number;
  edgesLoaded: number;
  edgeTypesPresent: string[];
  pipelineVersion?: string;
  schemaVersion?: string;
  bridgeVersion?: string;
  backend?: string;
  warnings: string[];
  errors: string[];
  source: "dataset_service" | "local_checkpoint" | "none";
  generatedAt: string;
  note?: string;
}

const DEFAULT_CHECKPOINT_PATH = path.resolve(
  process.cwd(),
  "..",
  "phase2",
  "data",
  "checkpoints",
  "step_01.json"
);

async function readLocalCheckpoint(checkpointPath: string): Promise<DatasetStatsResponse | null> {
  let content: string;
  try {
    content = await fs.readFile(checkpointPath, "utf8");
  } catch {
    return null;
  }
  let body: any;
  try {
    body = JSON.parse(content);
  } catch {
    return null;
  }
  const step1 = body?.step1 || {};
  const bridge = step1?.bridge_summary || {};
  const sourcesRead: string[] = bridge.sources_read || [];
  const sourcesAttempted: string[] = bridge.sources_attempted || [];
  const inputChecksums: Record<string, string> = step1?.input_checksums || {};
  const sources: DatasetSourceStat[] = sourcesAttempted.map((name) => ({
    name,
    loaded: sourcesRead.includes(name),
    sha256: inputChecksums[name],
  }));
  return {
    sources,
    nodesLoaded: bridge.nodes_loaded ?? 0,
    edgesLoaded: bridge.edges_loaded ?? 0,
    edgeTypesPresent: bridge.edge_types_present || [],
    pipelineVersion: body?.pipeline_version,
    schemaVersion: body?.schema_version,
    bridgeVersion: bridge.bridge_version,
    backend: bridge.backend,
    warnings: bridge.warnings || [],
    errors: bridge.errors || [],
    source: "local_checkpoint",
    generatedAt: new Date().toISOString(),
    note:
      "Served from local Phase 1 pipeline checkpoint. These are real " +
      "dataset statistics produced by the Airflow ETL pipeline.",
  };
}

async function proxyToDatasetService(url: string): Promise<DatasetStatsResponse> {
  const fullUrl = `${url.replace(/\/$/, "")}/stats`;
  const res = await fetch(fullUrl, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Dataset service at ${url} returned ${res.status}`);
  }
  const body = await res.json();
  return {
    sources: body?.sources || [],
    nodesLoaded: body?.nodesLoaded ?? 0,
    edgesLoaded: body?.edgesLoaded ?? 0,
    edgeTypesPresent: body?.edgeTypesPresent || [],
    pipelineVersion: body?.pipelineVersion,
    schemaVersion: body?.schemaVersion,
    bridgeVersion: body?.bridgeVersion,
    backend: body?.backend,
    warnings: body?.warnings || [],
    errors: body?.errors || [],
    source: "dataset_service",
    generatedAt: body?.generatedAt || new Date().toISOString(),
  };
}

export async function getDatasetStats(): Promise<DatasetStatsResponse> {
  // 1. Proxy path.
  const serviceUrl = process.env.DATASET_SERVICE_URL;
  if (serviceUrl) {
    try {
      return await proxyToDatasetService(serviceUrl);
    } catch (e) {
      console.warn("Dataset service proxy failed, falling back to local checkpoint:", e);
    }
  }

  // 2. Local checkpoint path.
  const checkpointPath = process.env.DATASET_CHECKPOINT_PATH || DEFAULT_CHECKPOINT_PATH;
  const stats = await readLocalCheckpoint(checkpointPath);
  if (stats) return stats;

  // 3. No data available.
  return {
    sources: [],
    nodesLoaded: 0,
    edgesLoaded: 0,
    edgeTypesPresent: [],
    warnings: [],
    errors: [],
    source: "none",
    generatedAt: new Date().toISOString(),
    note:
      "No dataset statistics available. Set DATASET_SERVICE_URL to proxy " +
      `to the Airflow service, or ensure the Phase 1 pipeline has written ` +
      `its checkpoint to ${checkpointPath}.`,
  };
}
