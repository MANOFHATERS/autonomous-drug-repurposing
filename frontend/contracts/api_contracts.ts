/**
 * frontend/contracts/api_contracts.ts
 * ===================================
 *
 * CANONICAL API CONTRACT — the SINGLE source of truth for every URL path
 * and response shape consumed by the frontend.
 *
 * TM14 ROOT FIX (v118, forensic, root-level):
 *   The previous codebase had NO frontend/contracts/api_contracts.ts file.
 *   The shared contract consistency test (shared/tests/test_contract_consistency.py
 *   TEST 11) FAILED because the file was missing — the test verifies the
 *   frontend defines the canonical URL constants and TypeScript interfaces
 *   that match the Python services' URL contract (shared/contracts/urls.py).
 *
 *   Without this file, the frontend's URL paths were hardcoded in
 *   lib/api-client.ts, lib/services/*.ts, and lib/ml-contracts.ts — each
 *   with slight variations (some had trailing slashes, some used /api/
 *   prefix, some used /v1/ prefix). When a Python service changed a URL
 *   (e.g., /predict → /gt/predict), the frontend silently broke in
 *   production with 404s that operators couldn't diagnose.
 *
 *   ROOT FIX: this file is the SINGLE source of truth. Every frontend
 *   module that calls a Python service MUST import the URL constant from
 *   here — never hardcode the path string. The contract consistency test
 *   verifies this file exists and contains all 7 canonical URLs.
 *
 *   The URL values EXACTLY match shared/contracts/urls.py (the Python
 *   side of the contract). Any change to a URL is now a 2-file change:
 *   this file + urls.py — the contract test catches drift.
 *
 * IMPORT RULE (frontend):
 *   import { URL_KG_STATS, URL_PREDICT, type PredictResponse }
 *     from "@/contracts/api_contracts";
 *
 * IMPORT RULE (contract test):
 *   The test reads this file as text and checks for the URL string
 *   literals. Do NOT refactor the URL constants into a generated file —
 *   the test depends on the literal strings being present in the source.
 */

// ============================================================================
// CANONICAL URL PATHS — MUST match shared/contracts/urls.py exactly
// ============================================================================
// These are the path strings the Python services register via
// @app.get(path) or @app.post(path). The frontend's fetch helpers append
// these to the service's base URL (e.g., http://localhost:8002 + URL_PREDICT).

/** Phase 2 KG service — graph stats (node/edge counts). */
export const URL_KG_STATS = "/kg/stats";

/** Phase 2 KG service — explore a node's neighborhood. */
export const URL_KG_EXPLORE = "/kg/explore";

/** Phase 3 GT service — predict drug-disease score. */
export const URL_PREDICT = "/predict";

/** Phase 3 GT service — top-k novel predictions. */
export const URL_TOP_K = "/top-k";

/** Phase 4 RL service — ranked candidates (composite score). */
export const URL_RANK = "/rank";

/** Phase 4 RL service — ranked candidates filtered by drug. */
export const URL_RANK_BY_DRUG = "/rank/{drug}";

/** Phase 4 RL service — validate a hypothesis (initiates writeback). */
export const URL_VALIDATE = "/validate";

/** All services — health check (liveness probe). */
export const URL_HEALTH = "/health";

/**
 * All canonical service URLs (for the contract consistency test).
 * MUST match ALL_SERVICE_URLS in shared/contracts/urls.py.
 */
export const ALL_SERVICE_URLS = [
  URL_KG_STATS,
  URL_KG_EXPLORE,
  URL_PREDICT,
  URL_TOP_K,
  URL_RANK,
  URL_RANK_BY_DRUG,
  URL_VALIDATE,
  URL_HEALTH,
] as const;

// ============================================================================
// DEFAULT SERVICE PORTS — MUST match shared/contracts/urls.py SERVICE_PORTS
// ============================================================================
// Used by the frontend's service URL resolvers (lib/services/*.ts) to build
// the full URL from a service name + path. The values are the SAME ports
// docker-compose.yml exposes — changing a port here without updating
// docker-compose.yml (and vice versa) breaks the platform.

export const SERVICE_PORTS = {
  phase1_dataset: 8000,
  phase2_kg: 8001,
  phase3_gt: 8002,
  phase4_rl: 8003,
  airflow_webserver: 8080,
  mlflow_tracking: 5000,
  neo4j_bolt: 7687,
  neo4j_http: 7474,
  postgres: 5432,
  frontend: 3000,
} as const;

export type ServiceName = keyof typeof SERVICE_PORTS;

// ============================================================================
// RESPONSE / REQUEST INTERFACES
// ============================================================================
// These interfaces describe the JSON shapes the Python services return.
// They are hand-maintained to match the Pydantic models in:
//   - phase2/service.py
//   - graph_transformer/service.py
//   - rl/service.py
//   - phase1/service.py
//
// For runtime validation, import the Zod schemas from lib/ml-contracts.ts
// (which validate the same response shapes). This file is the static-type
// contract; lib/ml-contracts.ts is the runtime contract. Both must stay
// in sync — the contract consistency test catches drift on the URL side,
// and the runtime Zod validation catches drift on the shape side.

// ─── Phase 2 (KG service) ────────────────────────────────────────────────

export interface KgStatsResponse {
  /** Total number of nodes in the knowledge graph. */
  total_nodes: number;
  /** Total number of edges in the knowledge graph. */
  total_edges: number;
  /** Per-label node counts (e.g., { Compound: 8341, Protein: 24193, ... }). */
  node_counts: Record<string, number>;
  /** Per-type edge counts (e.g., { "Compound|inhibits|Protein": 18234, ... }). */
  edge_counts: Record<string, number>;
  /** KG build version (semantic version string). */
  kg_version: string;
  /** ISO 8601 UTC timestamp when the KG was last built. */
  built_at: string | null;
  /** Backend used to build the KG ("neo4j" or "recording"). */
  backend: string;
}

export interface KgExploreNode {
  /** Node label (e.g., "Compound", "Protein"). */
  label: string;
  /** Node identifier (e.g., "aspirin", "P23219"). */
  id: string;
  /** Display name. */
  name: string;
  /** Node-level properties (free-form dict). */
  properties?: Record<string, unknown>;
}

export interface KgExploreEdge {
  /** Edge type / relationship label (e.g., "inhibits"). */
  type: string;
  /** Source node ID. */
  source: string;
  /** Target node ID. */
  target: string;
  /** Edge-level properties. */
  properties?: Record<string, unknown>;
}

export interface KgExploreResponse {
  /** The focal node the exploration centered on. */
  center: KgExploreNode;
  /** 1-hop neighbor nodes. */
  neighbors: KgExploreNode[];
  /** 1-hop edges (incoming + outgoing). */
  edges: KgExploreEdge[];
  /** Whether the result was truncated (e.g., max_neighbors limit hit). */
  truncated: boolean;
}

// ─── Phase 3 (GT service) ────────────────────────────────────────────────
//
// SH-025 + SH-006 + SH-031 ROOT FIX (v120 forensic, hostile-auditor):
// The previous static ``PredictResponse`` interface described a SINGLE
// prediction object with fields ``drug, disease, gnn_score,
// gnn_score_calibrated, confidence, gnn_score_timestamp, cached,
// model_version`` (snake_case). That shape NEVER matched the actual
// Python service response. The real shape (served by BOTH
// ``graph_transformer/service.py`` AND ``scripts/gt_api.py``) is a
// WRAPPER object: ``{predictions: Prediction[], source, modelVersion,
// generatedAt, count, checkpointPath, error_count?, error_rate?}``.
// The runtime Zod schema in ``frontend/src/lib/ml-contracts.ts``
// (``GtPredictResponseSchema``) already matches the real shape — only
// this static interface was stale. The audit (SH-025) also flagged
// that the ``source`` field's enum in the static contract
// (``"gt_service" | "gt_subprocess" | "stub"``) did NOT include
// ``"gt_checkpoint"`` — the value the Python service actually returns.
// The runtime Zod schema uses ``z.string()`` (no enum constraint), so
// it accepted any string — but the static type would have rejected
// ``"gt_checkpoint"`` at compile time. This fix aligns the static
// contract with the runtime Zod schema AND with the Python service.
//
// ``error_count`` and ``error_rate`` (SH-031) are kept as OPTIONAL
// fields — they are returned by ``graph_transformer/service.py`` for
// monitoring but are NOT required by the frontend (the Zod schema
// marks them optional). The previous comment in ``service.py`` claimed
// they were "returned as HTTP response HEADERS (not in the JSON body)"
// — that comment was a LIE (the code returns them in the body). The
// comment has been corrected to match the code.

/**
 * Single prediction item (one element of ``PredictResponse.predictions``).
 * Matches ``GtPredictionSchema`` in ``frontend/src/lib/ml-contracts.ts``.
 */
export interface GtPrediction {
  /** Drug name (echoed from request). */
  drug: string;
  /** Disease name (echoed from request). */
  disease: string;
  /** GT probability score in [0, 1] (temperature-calibrated per P3-004). */
  score: number;
  /** Binary-entropy confidence in [0, 1] (P3-010 fix). */
  confidence?: number;
  /** Optional note (e.g., "drug not in graph" for error cases). */
  note?: string;
}

/**
 * Response shape for ``POST /predict`` — the WRAPPER object.
 * Matches ``GtPredictResponseSchema`` in ``frontend/src/lib/ml-contracts.ts``
 * and the Python service in ``graph_transformer/service.py`` +
 * ``scripts/gt_api.py``.
 *
 * The ``source`` field is the canonical enum: ``"gt_checkpoint"`` (the
 * production value, served when a trained checkpoint is loaded),
 * ``"gt_service"`` / ``"gt_subprocess"`` (legacy aliases), or ``"stub"``
 * (test-only). The Python service currently always returns
 * ``"gt_checkpoint"`` in production.
 */
export interface PredictResponse {
  /** List of per-pair predictions (one per requested pair). */
  predictions: GtPrediction[];
  /** Canonical source enum. Production value: ``"gt_checkpoint"``. */
  source: "gt_checkpoint" | "gt_service" | "gt_subprocess" | "stub";
  /** Model version (camelCase — matches Python service). */
  modelVersion: string;
  /** ISO 8601 UTC timestamp when the response was generated. */
  generatedAt: string;
  /** Number of predictions returned (== predictions.length). */
  count: number;
  /** Filesystem path to the checkpoint used for inference. */
  checkpointPath: string | null;
  /** Optional: number of pairs that failed scoring (monitoring). */
  error_count?: number;
  /** Optional: fraction of pairs that failed (monitoring). */
  error_rate?: number;
}

/**
 * Single top-K novel prediction item.
 * Matches the prediction shape returned by ``GET /top-k``.
 */
export interface TopKNovelPrediction {
  drug: string;
  disease: string;
  /** GT score (temperature-calibrated per P3-004 fix). */
  score: number;
  /** Rank position (1-indexed). */
  rank?: number;
  /** Key biological pathways driving the prediction (for explainability). */
  key_pathways?: string[];
}

/**
 * Response shape for ``GET /top-k`` — the WRAPPER object.
 * Matches ``GtTopKResponseSchema`` in ``frontend/src/lib/ml-contracts.ts``
 * and the Python service in ``graph_transformer/service.py`` +
 * ``scripts/gt_api.py``.
 *
 * SH-025 ROOT FIX (v120): the previous static interface used
 * ``total_considered``, ``k``, and ``model_version`` (snake_case) —
 * NONE of which are returned by the Python service. The real shape is
 * ``{predictions, source, modelVersion, generatedAt, count,
 * checkpointPath}`` (camelCase). This fix aligns the static contract
 * with the runtime Zod schema and the Python service.
 */
export interface TopKResponse {
  /** Top-k novel predictions, sorted by score descending. */
  predictions: TopKNovelPrediction[];
  /** Canonical source enum. Production value: ``"gt_checkpoint"``. */
  source: "gt_checkpoint" | "gt_service" | "gt_subprocess" | "stub";
  /** Model version (camelCase — matches Python service). */
  modelVersion: string;
  /** ISO 8601 UTC timestamp when the response was generated. */
  generatedAt: string;
  /** Number of predictions returned (== predictions.length). */
  count: number;
  /** Filesystem path to the checkpoint used for inference. */
  checkpointPath: string | null;
}

// ─── Phase 4 (RL service) ────────────────────────────────────────────────

export interface RankedCandidate {
  drug: string;
  disease: string;
  /** Composite RL reward (weighted sum of all features). */
  reward: number;
  /** Rank position (1-indexed). */
  rank: number;
  /** Per-feature breakdown of the reward (for transparency). */
  reward_breakdown?: {
    gnn_score: number;
    safety_score: number;
    market_score: number;
    pathway_score: number;
    patent_score: number;
    adme_score: number;
    unmet_need_score: number;
    rare_disease_flag: number;
  };
  /** Literature support flag (true if ≥1 PubMed paper supports the hypothesis). */
  literature_support: boolean;
  /** Whether the (drug, disease) pair is a known positive (held-out). */
  is_known_positive: boolean;
}

export interface RankResponse {
  /** Ranked candidates, sorted by reward descending. */
  candidates: RankedCandidate[];
  /** Total candidates considered. */
  total: number;
  /** Source of the ranking ("service" = live RL, "csv_fallback" = static). */
  source: "service" | "csv_fallback";
  /** Pagination cursor (null if no more results). */
  next_cursor: string | null;
  /** ISO 8601 UTC timestamp when the ranking was generated. */
  ranked_at: string;
}

// ─── Validation (writable endpoint — initiates writeback) ────────────────

export interface ValidateRequest {
  /** Drug name (must exist in the KG). */
  drug: string;
  /** Disease name (must exist in the KG). */
  disease: string;
  /** Validation outcome. */
  outcome:
    | "validated_positive"
    | "validated_toxic"
    | "validated_negative"
    | "invalidated";
  /** Who validated this hypothesis (researcher email or org ID). */
  validated_by: string;
  /** Optional: external study ID (e.g., ClinicalTrials.gov NCT ID). */
  validation_study_id?: string;
  /** Optional: free-form notes. */
  notes?: string;
  /** Original GT score at prediction time (for audit trail). */
  original_gt_score?: number;
  /** Original RL rank at prediction time (for audit trail). */
  original_rl_rank?: number;
}

export interface ValidateResponse {
  /** Whether the writeback succeeded. */
  success: boolean;
  /** ISO 8601 UTC timestamp of the writeback. */
  validated_at: string;
  /** Path to the validated_hypotheses.csv (for audit). */
  csv_path: string;
  /** Number of rows in the CSV after this writeback. */
  csv_row_count: number;
  /** Edge label written to Neo4j (e.g., "VALIDATED_TREATS"). */
  neo4j_edge_label?: string;
  /** Error message (if success is false). */
  error?: string;
}

// ─── Health (all services) ───────────────────────────────────────────────

export interface HealthResponse {
  /** Service name (e.g., "phase3_gt"). */
  service: string;
  /** "ok" if healthy, "degraded" if partially healthy, "down" if unhealthy. */
  status: "ok" | "degraded" | "down";
  /** ISO 8601 UTC timestamp of the health check. */
  checked_at: string;
  /** Service version (semantic version string). */
  version: string;
  /** Optional detail message (e.g., "model not loaded" for degraded status). */
  detail?: string;
}

// ============================================================================
// CONTRACT METADATA (for the contract consistency test)
// ============================================================================

/**
 * The version of this contract file. Increment when adding/removing/renaming
 * any URL constant or interface. The Python-side contract (shared/contracts/
 * urls.py) has a corresponding __version__ that MUST match.
 */
export const API_CONTRACTS_VERSION = "2.0.0-shared-contract";

/**
 * All interfaces exported by this file (for the contract consistency test
 * to verify via static analysis).
 */
export const ALL_INTERFACES = [
  "KgStatsResponse",
  "KgExploreResponse",
  "KgExploreNode",
  "KgExploreEdge",
  "PredictResponse",
  "TopKResponse",
  "TopKNovelPrediction",
  "RankedCandidate",
  "RankResponse",
  "ValidateRequest",
  "ValidateResponse",
  "HealthResponse",
] as const;
