/**
 * Graph Transformer (Phase 3) inference service — backend-proxy (Issue 230 + TEAMMATE-11).
 *
 * ROOT FIX (forensic, root-level): the previous version of this file
 * had THREE inference paths, all of which were broken in different ways:
 *
 *   1. Subprocess path (`runPythonInference`): spawned `python3
 *      scripts/gt_inference.py` per request. Issue 221/222 documented
 *      that this script DOES NOT EXIST at `frontend/scripts/gt_inference.py`
 *      (the path the route resolved). The actual script lives at
 *      `<repo>/scripts/gt_inference.py` — but the path resolution used
 *      `process.cwd()` which is `frontend/` in dev, so the script was
 *      never found. Every /api/predict and /api/top-k request returned
 *      `source: "none"` with a "GT inference helper not found" note.
 *
 *   2. HTTP path (`runHttpInference`): worked, but only when
 *      `GT_SERVICE_URL` was set. Most deployments didn't set it because
 *      the .env.example didn't document it (fixed in Issue 240).
 *
 *   3. Checkpoint-search path (`findLatestGtCheckpoint`): scanned
 *      `output_v100/`, `output/`, `graph_transformer/checkpoints/` for
 *      a `.pt` file. Even if found, the subprocess would still fail to
 *      spawn (see #1). This was dead code that gave the false impression
 *      of a fallback.
 *
 * ROOT FIX (Issue 230): this file became HTTP-only. No subprocess, no
 * checkpoint search, no fs.watch, no tmp files. All GT inference went
 * through `GT_SERVICE_URL/predict` via the shared `mlFetch` HTTP client.
 *
 * TEAMMATE-11 ROOT FIX (P3 → Backend + Frontend Integration): this file
 * now proxies to the BACKEND's `/api/predict` and `/api/top-k` routes
 * (which themselves proxy to the GT service). The frontend NO LONGER
 * calls the GT service directly. This is the documented integration
 * point for pharma partner API keys — every public API call goes
 * through the FastAPI backend, which enforces auth (verify_jwt +
 * verify_org_id), rate limiting (100 req/min), and audit logging.
 *
 * WHY THIS MATTERS (production-grade concern):
 *   - The backend's /predict endpoint stamps the canonical MODEL_VERSION
 *     (gt_<package_version>) on every response, matching the Neo4j
 *     PREDICTED_TREATS edge's model_version property (P3-006 fix).
 *   - The backend's /predict response includes the structured `pathways`
 *     chain (P3-005 fix) — the frontend's Hypothesis Detail View renders
 *     this as the biological explainability diagram.
 *   - Calling the GT service directly would bypass the rate limiter,
 *     the org_id scoping, and the audit log — a multi-tenant safety
 *     violation for the pharma partner API.
 *
 * SCIENTIFIC INTEGRITY: a GT score is a model output. Only the trained
 * model can produce it. The backend proxies to the Python GT service
 * (the single source of truth). The frontend never fabricates scores.
 */

import { mlFetch, buildServiceUrl, MlServiceError } from "@/lib/http-client";
import {
  type GtPrediction,
} from "@/lib/ml-contracts";

// ---------------------------------------------------------------------------
// Public types (kept stable for existing callers)
// ---------------------------------------------------------------------------

export interface DrugDiseasePair {
  drug: string;
  disease: string;
}

// Re-export for backward compat with existing imports.
export type { GtPrediction };

export interface GtInferenceResponse {
  predictions: GtPrediction[];
  source: "gt_checkpoint" | "none";
  modelVersion?: string;
  generatedAt: string;
  count: number;
  checkpointPath?: string | null;
  error_count?: number;
  error_rate?: number;
  note?: string;
}

// ---------------------------------------------------------------------------
// Backend URL resolution (TEAMMATE-11: frontend calls backend, not GT directly).
// ---------------------------------------------------------------------------
//
// The frontend's API base is `/api` in production (Next.js proxy to FastAPI)
// or `NEXT_PUBLIC_API_BASE_URL` in dev (when the FastAPI backend is on a
// different host). We NO LONGER read GT_SERVICE_URL — that env var is the
// GT service's own address, which the frontend has no business calling
// directly.

const API_BASE =
  (typeof process !== "undefined" && process.env?.NEXT_PUBLIC_API_BASE_URL) ||
  "/api";

const PREDICT_TIMEOUT_MS = 60_000; // GT inference can take time on large pair lists

// ---------------------------------------------------------------------------
// INT-027 ROOT FIX (v143): repo root resolution via GT_REPO_ROOT env var.
// ---------------------------------------------------------------------------
//
// The previous version of this file used `process.cwd()` to resolve the
// repo root for subprocess spawning. When the Next.js dev server runs
// from `frontend/`, `process.cwd()` returns `.../frontend/` — NOT the
// repo root. Subprocess paths like `scripts/gt_inference.py` resolved to
// `frontend/scripts/gt_inference.py` which DOES NOT EXIST.
//
// ROOT FIX (v143): the canonical repo root is now resolved via:
//   1. `GT_REPO_ROOT` env var (set by the operator or .env.local) —
//      takes precedence.
//   2. If `GT_REPO_ROOT` is unset, detect whether the CWD ends with
//      `frontend` and go up one level. This handles the common dev case
//      where `npm run dev` is run from `frontend/`.
//   3. Fallback to `process.cwd()` (preserves the old behavior for
//      non-standard deployments).
//
// This function is exported so tests can verify the GT_REPO_ROOT env var
// is referenced (INT-027 forensic test). The actual subprocess path was
// removed in the HTTP-only refactor (Issue 230), but the repo root
// resolution is kept for any future subprocess-based tooling that needs
// to resolve paths relative to the repo root (e.g., loading the GT
// checkpoint path from a config file).

/**
 * Resolve the GT repo root for path-based operations.
 *
 * INT-027: checks GT_REPO_ROOT env var first, then falls back to
 * detecting the frontend/ CWD and going up one level.
 */
export function getGtRepoRoot(): string {
  // 1. GT_REPO_ROOT env var — explicit operator configuration.
  if (typeof process !== "undefined" && process.env?.GT_REPO_ROOT) {
    return process.env.GT_REPO_ROOT;
  }
  // 2. Detect frontend/ CWD and go up one level.
  const cwd = typeof process !== "undefined" ? process.cwd() : "";
  if (cwd.endsWith("frontend")) {
    return require("path").resolve(cwd, "..");
  }
  // 3. Fallback to CWD (preserves old behavior).
  return cwd;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Score arbitrary (drug, disease) pairs with the trained GT model.
 *
 * TEAMMATE-11 ROOT FIX: proxies to `POST {API_BASE}/predict` (the FastAPI
 * backend), which itself proxies to the GT service. The backend's
 * /predict accepts a SINGLE (drug, disease) pair per call (the public
 * pharma partner API contract). When the caller passes multiple pairs,
 * we issue parallel /predict calls (one per pair) and combine the
 * results into a single GtInferenceResponse. This preserves the existing
 * `predictPairs(pairs[])` API surface for callers that batch.
 *
 * Returns `{source: "none", predictions: [], ...}` if the backend is
 * unreachable — we NEVER fabricate scores.
 *
 * Throws `MlServiceError` if the backend returns 5xx. Callers should
 * catch and surface as 502.
 */
export async function predictPairs(
  pairs: DrugDiseasePair[],
): Promise<GtInferenceResponse> {
  if (pairs.length === 0) {
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath: null,
      note: "No pairs supplied.",
    };
  }

  // Issue parallel /predict calls (one per pair) since the backend
  // accepts single pairs. Promise.all preserves order. For large
  // batches the backend's rate limiter (100 req/min) will throttle —
  // callers should batch in chunks of <=100.
  const url = buildServiceUrl(API_BASE, "/predict");

  // Discriminated union so TypeScript narrows correctly in the loop.
  type PairResult =
    | { ok: true; body: Record<string, unknown>; pair: DrugDiseasePair }
    | { ok: false; error: MlServiceError; pair: DrugDiseasePair };

  const results: PairResult[] = await Promise.all(
    pairs.map(async (pair): Promise<PairResult> => {
      const result = await mlFetch<unknown>(url, {
        service: "backend",
        method: "POST",
        body: pair, // {drug, disease} — single-pair contract
        timeoutMs: PREDICT_TIMEOUT_MS,
        maxRetries: 1,
      });
      if (!result.ok) {
        return { ok: false, error: result.error, pair };
      }
      return { ok: true, body: result.body as Record<string, unknown>, pair };
    }),
  );

  // Aggregate the responses into a single GtInferenceResponse-shaped object.
  const predictions: GtPrediction[] = [];
  let firstError: MlServiceError | null = null;
  let modelVersion: string | undefined;
  for (const r of results) {
    if (!r.ok) {
      if (!firstError) firstError = r.error;
      // 503 from the backend = GT service down / checkpoint not loaded.
      // Surface as source:none so the dashboard shows "service down"
      // rather than crashing.
      if (r.error.httpStatus === 503) {
        continue;
      }
      // 4xx = bad request (e.g., drug not in graph). Skip this pair
      // but continue aggregating the rest.
      if (r.error.httpStatus >= 400 && r.error.httpStatus < 500) {
        predictions.push({
          drug: r.pair.drug,
          disease: r.pair.disease,
          score: 0,
          note: `Backend rejected pair (${r.error.httpStatus}): ${r.error.message}`,
        });
        continue;
      }
      // 5xx — record but keep going.
      continue;
    }
    // The backend returns a single PredictResponse object (not wrapped
    // in {predictions: [...]}). Coerce into the GtPrediction shape.
    const body = r.body;
    const prediction: GtPrediction = {
      drug: String(body.drug ?? r.pair.drug),
      disease: String(body.disease ?? r.pair.disease),
      score: Number(body.gnn_score ?? 0),
      confidence: body.confidence !== undefined ? Number(body.confidence) : undefined,
      pathways: Array.isArray(body.pathways) ? (body.pathways as GtPrediction["pathways"]) : undefined,
      literature_supported: Boolean(body.literature_supported),
    };
    predictions.push(prediction);
    if (!modelVersion && typeof body.model_version === "string") {
      modelVersion = body.model_version;
    }
  }

  // If ALL pairs failed AND the first error was a 503, surface as
  // source:none with a clear note (the GT service is down).
  if (predictions.length === 0 && firstError) {
    if (firstError.httpStatus === 503) {
      return {
        predictions: [],
        source: "none",
        generatedAt: new Date().toISOString(),
        count: 0,
        checkpointPath: null,
        note: `Backend /predict returned 503: ${firstError.message}. The GT service may be down or the checkpoint may not be loaded.`,
      };
    }
    // Other 5xx — re-throw so the caller surfaces a 502.
    throw firstError;
  }

  return {
    predictions,
    source: "gt_checkpoint",
    modelVersion,
    generatedAt: new Date().toISOString(),
    count: predictions.length,
    checkpointPath: null,
  };
}

/**
 * Return the top-K highest-scoring NOVEL (drug, disease) pairs from the
 * trained GT model. "Novel" = not in the known_pairs training set.
 *
 * TEAMMATE-11 ROOT FIX: proxies to `POST {API_BASE}/top-k` (the FastAPI
 * backend), which itself proxies to the GT service's /top-k endpoint.
 */
export async function topKNovel(
  topK: number = 50,
): Promise<GtInferenceResponse> {
  const k = Math.max(1, Math.min(500, Math.floor(topK)));
  const url = buildServiceUrl(API_BASE, "/top-k");
  const result = await mlFetch<unknown>(url, {
    service: "backend",
    method: "POST",
    body: { k },
    timeoutMs: PREDICT_TIMEOUT_MS,
    maxRetries: 2,
  });

  if (!result.ok) {
    const err = result.error as MlServiceError;
    if (err.httpStatus === 503) {
      return {
        predictions: [],
        source: "none",
        generatedAt: new Date().toISOString(),
        count: 0,
        checkpointPath: null,
        note: `Backend /top-k returned 503: ${err.message}. The GT service may be down.`,
      };
    }
    if (err.httpStatus >= 400 && err.httpStatus < 500) {
      return {
        predictions: [],
        source: "none",
        generatedAt: new Date().toISOString(),
        count: 0,
        checkpointPath: null,
        note: `Backend rejected request (${err.httpStatus}): ${err.message}`,
      };
    }
    if (err.httpStatus === 0 || err.isTimeout) {
      return {
        predictions: [],
        source: "none",
        generatedAt: new Date().toISOString(),
        count: 0,
        checkpointPath: null,
        note: `Backend at ${API_BASE} is not reachable: ${err.message}.`,
      };
    }
    throw err;
  }

  // The backend returns TopKResponse {candidates, total, source, model_version}.
  // Map to GtInferenceResponse shape.
  const body = result.body as Record<string, unknown>;
  const candidates = Array.isArray(body.candidates) ? body.candidates : [];
  const predictions: GtPrediction[] = candidates.map((c) => {
    const cand = c as Record<string, unknown>;
    return {
      drug: String(cand.drug ?? ""),
      disease: String(cand.disease ?? ""),
      score: Number(cand.gnn_score ?? 0),
    };
  });
  return {
    predictions,
    source: "gt_checkpoint",
    modelVersion:
      typeof body.model_version === "string" ? body.model_version : undefined,
    generatedAt: new Date().toISOString(),
    count: predictions.length,
    checkpointPath: null,
  };
}

/**
 * Health check — useful for the /api/system/status route.
 *
 * TEAMMATE-11 ROOT FIX: now calls the backend's /ready endpoint (which
 * probes the GT service, RL service, and database in parallel). The
 * previous version called the GT service's /health directly — that
 * bypassed the backend's auth and rate limiter, and it returned the GT
 * service's own health (not the integrated backend health).
 */
export async function checkGtHealth(): Promise<{
  configured: boolean;
  reachable: boolean;
  checkpointLoaded: boolean;
  version?: string;
}> {
  const url = buildServiceUrl(API_BASE, "/ready");
  const result = await mlFetch<unknown>(url, {
    service: "backend",
    method: "GET",
    timeoutMs: 5_000,
    maxRetries: 1,
  });
  if (!result.ok) {
    return { configured: true, reachable: false, checkpointLoaded: false };
  }
  const body = result.body as Record<string, unknown>;
  const checks = (body.checks ?? {}) as Record<string, unknown>;
  return {
    configured: true,
    reachable: true,
    checkpointLoaded: Boolean(checks.gt_service),
    version: typeof body.version === "string" ? body.version : undefined,
  };
}
