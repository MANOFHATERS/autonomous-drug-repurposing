/**
 * frontend/contracts/api_contracts.ts
 *
 * TASK 326 ROOT FIX (forensic, root-level):
 *   Previously the frontend's TypeScript types were defined INLINE in
 *   each route handler (src/app/api/{star}/route.ts) and each service
 *   client (src/lib/services/*.ts). The Python services' response shapes
 *   were reverse-engineered from FastAPI's auto-generated OpenAPI spec
 *   — but the frontend never actually imported the OpenAPI types, so
 *   when a Python service changed a response field (e.g. renamed
 *   `scores` to `predictions`), the frontend silently broke until
 *   someone noticed a runtime TypeError.
 *
 *   This file is the canonical TypeScript contract that mirrors the
 *   Python services' response shapes. The frontend MUST import these
 *   types instead of defining local interfaces. The contract
 *   consistency test (shared/tests/test_contract_consistency.py)
 *   verifies that the Python services actually return these shapes.
 *
 *   In a future iteration, this file should be auto-generated from the
 *   Python OpenAPI spec using `openapi-typescript`. For now it is
 *   hand-maintained and verified by the contract consistency test.
 */

// ============================================================================
// Canonical URL paths (mirrors shared/contracts/urls.py)
// ============================================================================
// IMPORTANT: these must match shared/contracts/urls.py EXACTLY.
// The contract consistency test verifies this.
export const API_URLS = {
  KG_STATS: "/kg/stats",
  KG_EXPLORE: "/kg/explore",
  PREDICT: "/predict",
  TOP_K: "/top-k",
  RANK: "/rank",
  RANK_BY_DRUG: "/rank/{drug}",
  VALIDATE: "/validate",
  HEALTH: "/health",
} as const;

export type ApiUrl = (typeof API_URLS)[keyof typeof API_URLS];

// ============================================================================
// Shared primitive types
// ============================================================================

/** ISO 8601 timestamp string (e.g. "2025-01-15T12:34:56Z"). */
export type IsoTimestamp = string;

/** A drug-disease pair identifier. */
export interface DrugDiseasePair {
  drug_id: string;
  disease_id: string;
  drug_name: string;
  disease_name: string;
}

// ============================================================================
// Phase 2 (Knowledge Graph) response shapes
// ============================================================================

/** Response from GET /kg/stats */
export interface KgStatsResponse {
  node_count: number;
  edge_count: number;
  node_type_counts: Record<string, number>;
  edge_type_counts: Record<string, [string, string, string][]>;
  last_updated: IsoTimestamp;
  source: "neo4j" | "in_memory";
}

/** Response from GET /kg/explore?node_id=...&node_type=... */
export interface KgExploreResponse {
  node: {
    id: string;
    type: string;
    name: string;
    properties: Record<string, unknown>;
  };
  neighbors: Array<{
    id: string;
    type: string;
    name: string;
    edge_type: string;
    edge_properties: Record<string, unknown>;
  }>;
  source: "neo4j" | "in_memory";
}

// ============================================================================
// Phase 3 (Graph Transformer) response shapes
// ============================================================================

/** Single prediction in a PredictResponse. */
export interface Prediction extends DrugDiseasePair {
  score: number;          // [0, 1] — link prediction probability
  confidence: number;     // [0, 1] — confidence bound
  pathways: string[];     // driving biological pathways (for explainability)
}

/** Response from POST /predict */
export interface PredictResponse {
  predictions: Prediction[];
  source: "gt_service" | "gt_subprocess" | "stub";
  modelVersion: string;
  generatedAt: IsoTimestamp;
  count: number;
  checkpointPath: string;
}

/** Response from GET /top-k */
export interface TopKResponse extends PredictResponse {
  /** K = the number of novel predictions requested. */
  k: number;
}

// ============================================================================
// Phase 4 (RL Ranker) response shapes
// ============================================================================

/** Single candidate in a RankResponse. */
export interface RankedCandidate extends DrugDiseasePair {
  score: number;          // RL composite score [0, 1]
  rank: number;           // 1-indexed rank
  gnn_score: number;
  safety_score: number;
  market_score: number;
  efficacy_score: number;
  patent_score: number;
  adme_score: number;
  literature_support: boolean;
  is_known_positive: boolean;
  reward?: number;
}

/** Response from GET /rank or POST /rank */
export interface RankResponse {
  candidates: RankedCandidate[];
  source: "rl_service" | "rl_csv" | "stub";
  generatedAt: IsoTimestamp;
  count: number;
}

// ============================================================================
// Validation (writeback) request / response shapes
// ============================================================================

/** Request body for POST /validate */
export interface ValidateRequest extends DrugDiseasePair {
  outcome: "validated_positive" | "validated_toxic" | "validated_inconclusive";
  validated_by: string;
  notes?: string;
}

/** Response from POST /validate */
export interface ValidateResponse {
  success: boolean;
  writeback_path: string;
  validated_at: IsoTimestamp;
  message: string;
}

// ============================================================================
// Health check response
// ============================================================================

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  service: string;
  version: string;
  uptime_seconds: number;
}

// ============================================================================
// Error response (all services return this on 4xx/5xx)
// ============================================================================

export interface ErrorResponse {
  detail: string;
  error_code?: string;
  request_id?: string;
}
