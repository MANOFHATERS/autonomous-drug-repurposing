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

/**
 * A drug-disease pair identifier.
 *
 * SH-024 ROOT FIX: the Python rl/service.py /rank and /validate
 * endpoints return `drug` and `disease` (canonical shared schema), NOT
 * `drug_id` / `drug_name` / `disease_id` / `disease_name`. The previous
 * 4-field shape caused every rank/validate response from the Python
 * backend to fail TypeScript validation in the frontend, because the
 * required `drug_id` / `disease_id` / `drug_name` / `disease_name`
 * fields were missing.
 *
 * The frontend now mirrors the Python service's actual response shape:
 * `drug` and `disease` (canonical names from the shared contract
 * `shared/contracts/writeback.py`).
 *
 * If a screen needs both an ID and a display name, derive one from the
 * other client-side (they are the same string in V1 — the canonical
 * name IS the identifier). Phase 2 will introduce separate ID/name
 * fields when the KG starts storing them as distinct properties.
 */
export interface DrugDiseasePair {
  drug: string;
  disease: string;
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

/**
 * Single candidate in a RankResponse.
 *
 * SH-024 ROOT FIX: the previous shape used snake_case + id fields
 * (drug_id, drug_name, gnn_score, safety_score, etc.) but the Python
 * rl/service.py /rank endpoint returns camelCase + bare names
 * (drug, disease, gnnScore, safetyScore, ...). Every frontend call to
 * /rank was silently breaking because the typed fields were missing.
 *
 * This interface now mirrors the ACTUAL Python response shape returned
 * by rl/service.py `_load_candidates_from_csv` and
 * `_load_candidates_from_checkpoint`. Sub-score keys are camelCase to
 * match the Python service exactly.
 *
 * The Python service is the authoritative writer of this contract —
 * the frontend MUST mirror it, not the other way around. If the
 * Python service changes, this file changes in the same commit (the
 * contract consistency test enforces this).
 */
export interface RankedCandidate extends DrugDiseasePair {
  rank: number;           // 1-indexed rank
  reward?: number;        // RL reward from the policy
  policyProb?: number;    // PPO policy probability
  gnnScore?: number;      // Graph Transformer score [0, 1]
  safetyScore?: number;   // Safety score [0, 1]
  marketScore?: number;   // Market opportunity score [0, 1]
  plausibilityScore?: number;  // alias for gnnScore
  overallScore?: number;  // weighted composite
  confidence?: number;    // model confidence
  pathwayScore?: number;
  unmetNeedScore?: number;
  efficacyScore?: number;
  admeScore?: number;
  literatureSupport?: number;
  isKnownPositive?: boolean;
}

/** Response from GET /rank or POST /rank */
export interface RankResponse {
  candidates: RankedCandidate[];
  source: "service" | "csv" | "none";
  modelVersion?: string;
  generatedAt: IsoTimestamp;
  total: number;          // total count BEFORE pagination
  page?: number;          // 0-indexed current page
  pageSize?: number;      // items per page
  count: number;          // items in THIS response
  backend?: "checkpoint" | "csv";
  csvPath?: string;
  note?: string;
}

// ============================================================================
// Validation (writeback) request / response shapes
// ============================================================================

/**
 * Request body for POST /validate
 *
 * SH-004 ROOT FIX: the previous enum had only 3 values
 * (`validated_positive | validated_toxic | validated_inconclusive`)
 * but the Python rl/service.py /validate endpoint accepts 4 values
 * (the canonical shared contract enum from
 * shared/contracts/writeback.py):
 *
 *   - validated_positive  : wet lab / clinical study confirmed efficacy
 *   - validated_negative  : wet lab / clinical study confirmed NO efficacy
 *   - validated_toxic     : drug caused adverse events — DO NOT retarget
 *   - invalidated         : partner could not reproduce the prediction
 *
 * `validated_inconclusive` is NOT a valid outcome — it was an artifact
 * of the old rl/contracts/phase4_schema.py drift (SH-002). Submitting
 * it would cause the Python service to return HTTP 400. The TS enum
 * now mirrors the canonical 4-value set so the frontend can never
 * submit an invalid outcome.
 *
 * The `drug` and `disease` fields use the canonical shared schema
 * (not drug_id / drug_name / disease_id / disease_name) — see
 * DrugDiseasePair for the rationale.
 */
export interface ValidateRequest extends DrugDiseasePair {
  outcome:
    | "validated_positive"
    | "validated_negative"
    | "validated_toxic"
    | "invalidated";
  validated_by: string;
  validation_study_id?: string;
  notes?: string;
  original_gt_score?: number;
  original_rl_rank?: number;
}

/**
 * Response from POST /validate
 *
 * SH-005 ROOT FIX: the previous shape was
 *   { success, writeback_path, validated_at, message }
 * but the Python rl/service.py /validate endpoint returns
 *   { ok, writeback: { phase1_csv_path, phase2_neo4j_written,
 *                      phase3_trigger_path, validated_hypothesis,
 *                      writeback_version }, message }
 *
 * Every frontend call to /validate was silently breaking because the
 * typed `success` / `writeback_path` / `validated_at` fields were
 * missing. This interface now mirrors the ACTUAL Python response so
 * the frontend can read the writeback result correctly.
 */
export interface ValidateResponse {
  ok: boolean;
  writeback: {
    phase1_csv_path: string;
    phase2_neo4j_written: boolean;
    phase3_trigger_path: string;
    validated_hypothesis: {
      drug: string;
      disease: string;
      outcome: string;
      validated_by: string;
      validation_study_id?: string;
      validated_at: string;
      notes?: string;
      original_gt_score?: number;
      original_rl_rank?: number;
    };
    writeback_version: string;
  };
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
