/**
 * ML service contracts — TypeScript types matching the Python service
 * response schemas (Issue 235).
 *
 * ROOT FIX (forensic, root-level): the frontend previously had THREE
 * sources of truth for each ML service's response shape:
 *
 *   1. The Python service's actual response (FastAPI + Pydantic).
 *   2. The frontend lib service's hand-written interface (drifted).
 *   3. The api-client.ts's hand-written interface (drifted differently).
 *
 * When the Python service added a field (e.g., `error_count` to /predict),
 * the frontend's hand-written interfaces did not update. The dashboard
 * silently dropped the new field. When the Python service RENAMED a field
 * (e.g., `scores` → `predictions`), the frontend's `.map(p => p.score)`
 * returned undefined for every prediction — the dashboard showed "score:
 * undefined" for every drug-disease pair.
 *
 * ROOT FIX: this file is the SINGLE source of truth for every ML service
 * response shape consumed by the frontend. Each interface is paired with
 * a Zod schema that VALIDATES the response at runtime — if the Python
 * service drifts, the frontend sees a structured `MlContractError` at
 * the call site instead of a cryptic render error 10 layers deep.
 *
 * The Zod schemas are written to match the ACTUAL Python response shapes
 * as observed in:
 *   - graph_transformer/service.py (Phase 3)
 *   - rl/service.py (Phase 4)
 *   - phase2/service.py (Phase 2 KG)
 *   - phase1/service.py (Phase 1 dataset)
 *
 * Fields marked optional (`?`) are ones the Python service may omit
 * (e.g., `note` is only present in some response paths). Fields marked
 * required are guaranteed by the Python service's Pydantic models.
 *
 * OPENAPI NOTE: this file is hand-maintained rather than auto-generated
 * from openapi-typescript because the Python services do not currently
 * expose /openapi.json in CI (they require a trained checkpoint to start).
 * When the services are updated to expose OpenAPI specs in CI, this file
 * SHOULD be regenerated from `openapi-typescript`. For now, the Zod
 * schemas enforce the contract at runtime — drift is caught immediately.
 */

import { z } from "zod";

// ============================================================================
// Phase 3 — Graph Transformer Service (graph_transformer/service.py)
// ============================================================================
//
// TEAMMATE-11 ROOT FIX (P3-005): added structured PathwayItem schema.
// Each pathway is now {pathway, intermediate_protein, chain[]}, not a
// bare string. The backend's PredictResponse.pathways is List[PathwayItem].
//
// TEAMMATE-11 ROOT FIX (P3-006): added `model_version` to GtPredictResponse
// so the caller can verify which model version produced the score.

export const PathwayItemSchema = z.object({
  pathway: z.string(),
  intermediate_protein: z.string(),
  chain: z.array(z.string()),
});

export const GtPredictionSchema = z.object({
  drug: z.string(),
  disease: z.string(),
  score: z.number(),
  confidence: z.number().optional(),
  // TEAMMATE-11 P3-005: structured pathway chain (replaces bare string list).
  pathways: z.array(PathwayItemSchema).optional(),
  literature_supported: z.boolean().optional(),
  note: z.string().optional(),
});

export const GtPredictResponseSchema = z.object({
  predictions: z.array(GtPredictionSchema),
  source: z.string(),
  modelVersion: z.string().optional(),
  // TEAMMATE-11 P3-006: snake_case mirror of modelVersion for callers
  // that read the backend's PredictResponse directly (the backend returns
  // `model_version` snake_case; the GT service returns `modelVersion`
  // camelCase). The frontend tolerates either.
  model_version: z.string().optional(),
  generatedAt: z.string(),
  count: z.number(),
  checkpointPath: z.string().nullable().optional(),
  error_count: z.number().optional(),
  error_rate: z.number().optional(),
});

export const GtTopKResponseSchema = z.object({
  predictions: z.array(
    z.object({
      drug: z.string(),
      disease: z.string(),
      score: z.number(),
    }),
  ),
  source: z.string(),
  modelVersion: z.string().optional(),
  model_version: z.string().optional(),
  generatedAt: z.string(),
  count: z.number(),
  checkpointPath: z.string().nullable().optional(),
});

export const GtHealthResponseSchema = z.object({
  status: z.string(),
  service: z.string(),
  version: z.string(),
  checkpoint_configured: z.boolean(),
  checkpoint_loaded: z.boolean(),
});

export type PathwayItem = z.infer<typeof PathwayItemSchema>;
export type GtPrediction = z.infer<typeof GtPredictionSchema>;
export type GtPredictResponse = z.infer<typeof GtPredictResponseSchema>;
export type GtTopKResponse = z.infer<typeof GtTopKResponseSchema>;
export type GtHealthResponse = z.infer<typeof GtHealthResponseSchema>;

// Input contract for /predict
export const GtPredictRequestSchema = z.object({
  pairs: z
    .array(
      z.object({
        drug: z.string().min(1).max(200),
        disease: z.string().min(1).max(200),
      }),
    )
    .max(5000)
    .optional(),
  drug: z.string().min(1).max(200).optional(),
  disease: z.string().min(1).max(200).optional(),
  limit: z.number().int().min(1).max(5000).optional(),
});

export type GtPredictRequest = z.infer<typeof GtPredictRequestSchema>;

// ============================================================================
// Phase 4 — RL Hypothesis Ranker Service (rl/service.py)
// ============================================================================

/**
 * TM13 ROOT FIX (v132, CRITICAL — Phase 2 ↔ Phase 4 wiring):
 * PathwayChainItemSchema describes ONE pathway chain connecting a drug to
 * a disease. The chain is an ordered list of biological entities, e.g.:
 *   ["metformin", "mTOR", "mTOR signaling", "cancer"]
 *   ["aspirin", "COX-2", "prostaglandin synthesis", "inflammation"]
 *
 * The Python rl/service.py attaches pathway_chain to each RankedHypothesis
 * by querying the Phase 2 Neo4j knowledge graph (phase2/service.py /kg/explore).
 * This is the "biological pathway chain that explains the prediction"
 * deliverable mandated by project docx §6 (Phase 4 output). Without this
 * field, the dashboard could show scores with no mechanistic explanation —
 * exactly the broken state Teammate 13's issue describes.
 *
 * Fields:
 *   - pathway: the canonical pathway name (e.g., "mTOR signaling").
 *   - intermediate_protein: the drug target protein that links the drug
 *     to the pathway (e.g., "mTOR" for metformin). May be omitted when
 *     the chain is drug → pathway directly (no intermediate).
 *   - chain: the ordered list of biological entities from drug to disease.
 *     chain[0] is the drug, chain[chain.length-1] is the disease. Middle
 *     elements are proteins/pathways.
 */
export const PathwayChainItemSchema = z.object({
  pathway: z.string(),
  intermediate_protein: z.string().optional(),
  chain: z.array(z.string()),
});

export type PathwayChainItem = z.infer<typeof PathwayChainItemSchema>;

export const RankedHypothesisSchema = z.object({
  drug: z.string(),
  disease: z.string(),
  rank: z.number().int().optional(),
  reward: z.number().nullable().optional(),
  policyProb: z.number().nullable().optional(),
  gnnScore: z.number().nullable().optional(),
  safetyScore: z.number().nullable().optional(),
  marketScore: z.number().nullable().optional(),
  plausibilityScore: z.number().nullable().optional(),
  overallScore: z.number().nullable().optional(),
  confidence: z.number().nullable().optional(),
  pathwayScore: z.number().nullable().optional(),
  unmetNeedScore: z.number().nullable().optional(),
  efficacyScore: z.number().nullable().optional(),
  admeScore: z.number().nullable().optional(),
  literatureSupport: z.number().nullable().optional(),
  isKnownPositive: z.boolean().optional(),
  /**
   * TM13 ROOT FIX (v132): pathway_chain is the list of biological
   * pathways connecting this drug to this disease. Empty array when
   * the Phase 2 KG has no pathway data for this pair, or when the KG
   * service is unreachable. The candidate table renders this as an
   * expandable "N pathways" cell.
   */
  pathway_chain: z.array(PathwayChainItemSchema).default([]),
});

export const RlRankResponseSchema = z.object({
  candidates: z.array(RankedHypothesisSchema),
  /**
   * TM13 ROOT FIX (v132): the Python service returns source: "service"
   * (per P4-045 fix). The previous Zod schema accepted any string, which
   * was permissive enough — but the frontend's RlRankerResponse type
   * restricted source to "rl_service" | "none", causing the hardcoded
   * source override in rl-ranker.ts. The Zod schema here stays permissive
   * (accepts any string); the narrowing happens at the rl-ranker.ts
   * type level. This keeps the runtime validation lenient (so a future
   * Python update adding a new source value doesn't break the frontend)
   * while the TypeScript type forces callers to handle the known set.
   */
  source: z.string(),
  modelVersion: z.string().optional(),
  generatedAt: z.string(),
  total: z.number().int(),
  page: z.number().int(),
  pageSize: z.number().int(),
  count: z.number().int(),
  csvPath: z.string().optional(),
  backend: z.string().optional(),
  note: z.string().optional(),
  /**
   * TM13 ROOT FIX (v132): orgId is echoed back by the Python service
   * for audit attribution (21 CFR Part 11). The previous schema silently
   * dropped this field. Now validated as an optional string (the service
   * returns "anonymous" when RL_REQUIRE_AUTH=false, so it's always
   * present in practice, but kept optional for dev/CI environments
   * that may not set it).
   */
  orgId: z.string().optional(),
  /**
   * TM13 ROOT FIX (v132): pathway_enrichment_available is a boolean
   * flag indicating whether the Python service successfully queried the
   * Phase 2 KG for pathway chains. When true, candidates' pathway_chain
   * arrays may be non-empty. When false, all pathway_chain arrays are
   * empty (KG unreachable, no pathways found, or pathway enrichment
   * disabled in config). The candidate table reads this flag to decide
   * whether to render the Pathway column at all.
   */
  pathway_enrichment_available: z.boolean().optional().default(false),
});

export const RlHealthResponseSchema = z.object({
  status: z.string(),
  service: z.string(),
  version: z.string(),
  checkpoint_configured: z.boolean(),
  csv_output_available: z.boolean(),
});

export const RlValidateRequestSchema = z.object({
  drug: z.string().min(1).max(200),
  disease: z.string().min(1).max(200),
  outcome: z.enum([
    "validated_positive",
    "validated_negative",
    "validated_toxic",
    "invalidated",
  ]),
  validated_by: z.string().min(1).max(200),
  validation_study_id: z.string().max(200).optional(),
  notes: z.string().max(10_000).optional(),
  original_gt_score: z.number().min(0).max(1).optional(),
  original_rl_rank: z.number().int().min(1).optional(),
});

export const RlValidateResponseSchema = z.object({
  ok: z.boolean(),
  writeback: z.object({
    phase1_csv_path: z.string(),
    phase2_neo4j_written: z.boolean(),
    phase3_trigger_path: z.string(),
    validated_hypothesis: z.record(z.string(), z.unknown()),
    writeback_version: z.string(),
  }),
  message: z.string().optional(),
});

export type RankedHypothesis = z.infer<typeof RankedHypothesisSchema>;
export type RlRankResponse = z.infer<typeof RlRankResponseSchema>;
export type RlHealthResponse = z.infer<typeof RlHealthResponseSchema>;
export type RlValidateRequest = z.infer<typeof RlValidateRequestSchema>;
export type RlValidateResponse = z.infer<typeof RlValidateResponseSchema>;

// ============================================================================
// Phase 2 — Knowledge Graph Service (phase2/service.py)
// ============================================================================

export const GraphSourceStatSchema = z.object({
  name: z.string(),
  loaded: z.boolean(),
  loadedReason: z.string().optional(),
  version: z.string().optional(),
  rows: z.number().optional(),
  edgeCount: z.number().optional(),
  sha256: z.string().optional(),
  producedAt: z.string().optional(),
  producedBy: z.string().optional(),
  loadId: z.string().optional(),
  nodeTypeCounts: z.record(z.string(), z.number()).optional(),
  edgeTypeCounts: z.record(z.string(), z.number()).optional(),
});

export const KgStatsResponseSchema = z.object({
  sources: z.array(GraphSourceStatSchema),
  nodeCount: z.number(),
  // Teammate 8 ROOT FIX: canonicalNodeCount — count of CANONICAL-type
  // nodes only (Compound, Protein, Pathway, Disease, ClinicalOutcome).
  // The Phase 2 service emits this directly (phase2/service.py:
  // _compute_canonical_node_count); the backend FastAPI proxy passes
  // it through unchanged. The frontend's Knowledge Graph Explorer
  // displays BOTH nodeCount (total, includes non-canonical types like
  // Gene/MedDRA_Term/Anatomy) AND canonicalNodeCount (canonical
  // scientific entities only). The field is OPTIONAL in the schema
  // for backward compat with older Phase 2 deployments that don't
  // emit it yet — when missing, kg-service.ts derives it client-side
  // by summing the canonical entries in nodeTypeCounts.
  canonicalNodeCount: z.number().optional(),
  edgeCount: z.number(),
  nodeTypeCounts: z.record(z.string(), z.number()),
  edgeTypeCounts: z.record(z.string(), z.number()),
  nonCanonicalNodeCounts: z.record(z.string(), z.number()).optional(),
  source: z.string(),
  generatedAt: z.string(),
  note: z.string().optional(),
});

export const KgQueryResponseSchema = z.object({
  nodes: z
    .array(
      z.object({
        id: z.string(),
        label: z.string().optional(),
        type: z.string().optional(),
      }),
    )
    .optional(),
  edges: z
    .array(
      z.object({
        source: z.string(),
        target: z.string(),
        type: z.string().optional(),
      }),
    )
    .optional(),
});

export const KgCypherResponseSchema = z.object({
  records: z.array(z.record(z.string(), z.unknown())).optional(),
  rows: z.array(z.array(z.unknown())).optional(),
  truncated: z.boolean().optional(),
});

export const KgHealthResponseSchema = z.object({
  status: z.string(),
  service: z.string(),
  version: z.string().optional(),
  neo4j_configured: z.boolean().optional(),
});

export type GraphSourceStat = z.infer<typeof GraphSourceStatSchema>;
export type KgStatsResponse = z.infer<typeof KgStatsResponseSchema>;
export type KgQueryResponse = z.infer<typeof KgQueryResponseSchema>;
export type KgCypherResponse = z.infer<typeof KgCypherResponseSchema>;
export type KgHealthResponse = z.infer<typeof KgHealthResponseSchema>;

// ============================================================================
// Phase 1 — Dataset Service (phase1/service.py)
// ============================================================================

export const DatasetSourceStatSchema = z.object({
  name: z.string(),
  loaded: z.boolean(),
  rowsLoaded: z.number().optional(),
  sha256: z.string().optional(),
});

export const DatasetStatsResponseSchema = z.object({
  sources: z.array(DatasetSourceStatSchema),
  nodesLoaded: z.number(),
  edgesLoaded: z.number(),
  edgeTypesPresent: z.array(z.string()),
  pipelineVersion: z.string().optional(),
  schemaVersion: z.string().optional(),
  bridgeVersion: z.string().nullable().optional(),
  backend: z.string().optional(),
  warnings: z.array(z.string()),
  errors: z.array(z.string()),
  generatedAt: z.string(),
  status: z.string().optional(),
  source: z.string().optional(),
  note: z.string().optional(),
});

export const DatasetHealthResponseSchema = z.object({
  status: z.string(),
  service: z.string(),
  version: z.string().optional(),
});

export type DatasetSourceStat = z.infer<typeof DatasetSourceStatSchema>;
export type DatasetStatsResponse = z.infer<typeof DatasetStatsResponseSchema>;
export type DatasetHealthResponse = z.infer<typeof DatasetHealthResponseSchema>;

// ============================================================================
// Canonical node types — per project docx Phase 2 (5 types)
// ============================================================================
// Teammate 8 ROOT FIX: the previous list used "ClinicalOutcomes" (PLURAL),
// but the Phase 2 KG label vocabulary uses the SINGULAR form
// "ClinicalOutcome" (see phase2/service.py::CANONICAL_NODE_TYPES and
// drugos_graph.schemas). The plural form caused every ClinicalOutcome
// node to be classified as non-canonical by the frontend's transform
// layer (kg-service.ts:175 `if (CANONICAL_NODE_TYPE_SET.has(type))`),
// silently dropping them from the canonical nodeCount. The dashboard
// under-reported the canonical node count by the entire
// ClinicalOutcome population — a scientific reporting bug.
//
// ROOT FIX: align with the Phase 2 contract — use "ClinicalOutcome"
// (singular). This matches:
//   - phase2/service.py:CANONICAL_NODE_TYPES (the Python source of truth)
//   - drugos_graph.schemas (the Pydantic KG label enum)
//   - phase2/drugos_graph/kg_builder.py (the Neo4j label writer)
//
// The previous plural form was a typo that propagated from an early
// Phase 2 design doc; the Phase 2 service code has ALWAYS used the
// singular form. This fix aligns the frontend with the actual Phase 2
// implementation.

export const CANONICAL_NODE_TYPES = [
  "Compound",
  "Protein",
  "Pathway",
  "Disease",
  "ClinicalOutcome", // SINGULAR — matches Phase 2 KG label vocabulary
] as const;

export type CanonicalNodeType = (typeof CANONICAL_NODE_TYPES)[number];

export const CANONICAL_NODE_TYPE_SET: ReadonlySet<string> = new Set(
  CANONICAL_NODE_TYPES,
);

// ============================================================================
// Service URL env-var names (single source of truth)
// ============================================================================

export const SERVICE_URL_ENV_VARS = {
  phase1: ["PHASE1_SERVICE_URL", "DATASET_SERVICE_URL"], // canonical + legacy alias
  phase2: ["KG_SERVICE_URL"],
  phase3: ["GT_SERVICE_URL"],
  phase4: ["RL_SERVICE_URL"],
} as const;

/**
 * MlContractError — thrown when a Python service response fails Zod
 * validation. This indicates the Python service's response shape has
 * drifted from the contract documented in this file.
 */
export class MlContractError extends Error {
  readonly service: string;
  readonly endpoint: string;
  readonly zodError: z.ZodError;
  readonly rawBody: unknown;

  constructor(params: {
    service: string;
    endpoint: string;
    zodError: z.ZodError;
    rawBody: unknown;
  }) {
    super(
      `ML contract violation: ${params.service} ${params.endpoint} ` +
        `response did not match expected schema. First issue: ` +
        `${params.zodError.issues[0]?.message ?? "unknown"}`,
    );
    this.name = "MlContractError";
    this.service = params.service;
    this.endpoint = params.endpoint;
    this.zodError = params.zodError;
    this.rawBody = params.rawBody;
  }

  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      service: this.service,
      endpoint: this.endpoint,
      message: this.message,
      issues: this.zodError.issues,
      rawBody:
        typeof this.rawBody === "string"
          ? this.rawBody.slice(0, 500)
          : this.rawBody,
    };
  }
}

/**
 * Validate a response body against a Zod schema. Throws MlContractError
 * on mismatch — callers should catch and surface as 502.
 */
export function validateMlResponse<T>(
  service: string,
  endpoint: string,
  schema: z.ZodType<T>,
  body: unknown,
): T {
  const result = schema.safeParse(body);
  if (!result.success) {
    throw new MlContractError({
      service,
      endpoint,
      zodError: result.error,
      rawBody: body,
    });
  }
  return result.data;
}
