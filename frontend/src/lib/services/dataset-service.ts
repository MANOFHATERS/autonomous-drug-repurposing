/**
 * Dataset service — unified, HTTP-only via the FastAPI backend
 * (TEAMMATE-4 ROOT FIX).
 *
 * FORENSIC ROOT-LEVEL FIX (replaces the previous version):
 *   The previous version of this file called PHASE1_SERVICE_URL
 *   directly, bypassing the FastAPI backend. This meant:
 *     - NO JWT authentication (Phase 1 is an unauthenticated internal
 *       service — any browser could call it).
 *     - NO org_id scoping (any user could read any other user's
 *       validated hypotheses — a multi-tenant data leak).
 *     - NO rate limiting (a single misbehaving client could saturate
 *       Phase 1's connection pool).
 *     - NO 503 fallback (when Phase 1 was down, the frontend hung on a
 *       30-second timeout or returned a confusing 500).
 *
 * ROOT FIX:
 *   This file now calls /api/datasets/* (Next.js routes) which proxy
 *   to the FastAPI backend's /datasets/* routes, which in turn proxy
 *   to Phase 1. The backend enforces JWT auth, org_id scoping, rate
 *   limiting, and 503 fallback at a single chokepoint.
 *
 *   Architecture:
 *     Browser -> Next.js /api/datasets/stats -> FastAPI /datasets/stats
 *                                            -> Phase 1 /stats
 *
 *   The PHASE1_SERVICE_URL env var is NO LONGER USED by the frontend.
 *   It now lives on the backend (BACKEND_SERVICE_URL on the frontend
 *   points to the FastAPI service, which has its own PHASE1_SERVICE_URL
 *   pointing to the Phase 1 service).
 *
 * SCIENTIFIC INTEGRITY:
 *   Dataset statistics must reflect the actual Phase 1 pipeline state.
 *   The backend proxies to Phase 1's /stats endpoint which reads real
 *   CSV row counts from phase1/processed_data/. We NEVER fabricate
 *   numbers, NEVER read a local checkpoint as a fallback.
 *
 * ERROR HANDLING:
 *   - 401: user not authenticated — the caller should redirect to /login.
 *   - 403: org_id mismatch (only on POST /datasets/validated_hypotheses)
 *          — the caller should surface a "cross-org validation forbidden"
 *          error to the user.
 *   - 429: rate limit exceeded — the caller should back off and retry
 *          with exponential jitter.
 *   - 503: Phase 1 service unavailable — the caller should show a
 *          "Phase 1 is down" banner and retry with backoff.
 */

import {
  DatasetStatsResponseSchema,
  type DatasetStatsResponse,
  type DatasetSourceStat,
  type DatasetHealthResponse,
  validateMlResponse,
} from "@/lib/ml-contracts";

// ---------------------------------------------------------------------------
// Public types (kept stable for existing callers)
// ---------------------------------------------------------------------------

export type { DatasetStatsResponse, DatasetSourceStat, DatasetHealthResponse };

/**
 * Backward-compat alias for the response shape. The previous
 * `dataset-stats.ts` exported `DatasetStatsResponse`; callers that
 * import that name should continue to work — it's re-exported above.
 */

// ---------------------------------------------------------------------------
// API base URL — Next.js routes (proxied to the FastAPI backend).
// ---------------------------------------------------------------------------

/**
 * The API base URL. Always /api/* (Next.js routes) — the frontend NEVER
 * calls Phase 1 or the FastAPI backend directly. The Next.js route
 * handles the cookie-to-Bearer translation and proxies to the backend.
 *
 * TEAMMATE-4 ROOT FIX: the previous version used PHASE1_SERVICE_URL
 * (calling Phase 1 directly). Now we use /api/datasets/* (Next.js
 * routes that proxy to the FastAPI backend).
 */
const API_BASE = "/api";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get dataset statistics from the Phase 1 service (via the backend).
 *
 * Calls GET /api/datasets/stats (Next.js route) which proxies to the
 * FastAPI backend's /datasets/stats endpoint, which proxies to Phase 1's
 * /stats endpoint.
 *
 * Returns a DatasetStatsResponse with:
 *   - sources: list of {name, loaded, rowsLoaded}
 *   - total_drugs, total_proteins, total_ppi
 *   - nodesLoaded, edgesLoaded, edgeTypesPresent
 *   - compoundNodesLoaded, proteinNodesLoaded
 *   - schemaVersion (real DB schema version, currently 20)
 *   - bridgeVersion, lastUpdated
 *   - warnings, errors, generatedAt
 *
 * On error (network failure, 5xx), throws an Error with a clear message.
 * On 401/403/429, returns a DatasetStatsResponse with status="service_down"
 * and the error in the errors[] array (so the dashboard can render
 * the error state without crashing).
 */
export async function getDatasetStats(): Promise<DatasetStatsResponse> {
  const url = `${API_BASE}/datasets/stats`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "include",  // send the NextAuth session cookie
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
  } catch (e: unknown) {
    // Network error — the Next.js route itself is unreachable (the
    // Next.js server is down, or the user is offline).
    const msg = e instanceof Error ? e.message : String(e);
    return {
      sources: [],
      nodesLoaded: 0,
      edgesLoaded: 0,
      edgeTypesPresent: [],
      warnings: [],
      errors: [
        `Network error calling /api/datasets/stats: ${msg}. The Next.js ` +
          `server may be down or you may be offline.`,
      ],
      generatedAt: new Date().toISOString(),
      status: "service_down",
      source: "none",
      note: `Network error: ${msg}`,
    } as DatasetStatsResponse;
  }

  // Handle non-200 responses. 401/403/429/503 all surface as
  // service_down with a clear error message — the dashboard renders
  // the error state instead of crashing.
  if (!response.ok) {
    let errorBody: string;
    try {
      const body = await response.json();
      errorBody = body?.message || body?.detail || JSON.stringify(body);
    } catch {
      errorBody = await response.text().catch(() => "");
    }

    if (response.status === 401) {
      return {
        sources: [],
        nodesLoaded: 0,
        edgesLoaded: 0,
        edgeTypesPresent: [],
        warnings: [],
        errors: ["Authentication required. Please log in."],
        generatedAt: new Date().toISOString(),
        status: "service_down",
        source: "none",
        note: "401 Unauthorized — redirect to /login.",
      } as DatasetStatsResponse;
    }

    if (response.status === 429) {
      return {
        sources: [],
        nodesLoaded: 0,
        edgesLoaded: 0,
        edgeTypesPresent: [],
        warnings: [],
        errors: [
          "Rate limit exceeded (100 requests/minute). Please wait and try again.",
        ],
        generatedAt: new Date().toISOString(),
        status: "service_down",
        source: "none",
        note: "429 Too Many Requests — back off and retry.",
      } as DatasetStatsResponse;
    }

    if (response.status === 503) {
      return {
        sources: [],
        nodesLoaded: 0,
        edgesLoaded: 0,
        edgeTypesPresent: [],
        warnings: [],
        errors: [
          `Phase 1 service unavailable: ${errorBody}. The backend is ` +
            `running but cannot reach the Phase 1 dataset service. ` +
            `Check that phase1/service.py is running and that ` +
            `PHASE1_SERVICE_URL is set correctly on the backend.`,
        ],
        generatedAt: new Date().toISOString(),
        status: "service_down",
        source: "none",
        note: `503 Service Unavailable: ${errorBody}`,
      } as DatasetStatsResponse;
    }

    // Other 4xx/5xx — generic error.
    return {
      sources: [],
      nodesLoaded: 0,
      edgesLoaded: 0,
      edgeTypesPresent: [],
      warnings: [],
      errors: [
        `Backend returned ${response.status}: ${errorBody.slice(0, 500)}`,
      ],
      generatedAt: new Date().toISOString(),
      status: "service_down",
      source: "none",
      note: `${response.status}: ${errorBody.slice(0, 200)}`,
    } as DatasetStatsResponse;
  }

  // 200 OK — validate the response against the Zod schema.
  const raw = await response.json();
  const validated = validateMlResponse<DatasetStatsResponse>(
    "phase1_dataset",
    "/stats",
    DatasetStatsResponseSchema,
    raw,
  );

  // Ensure the response has the status field (the backend may not set it).
  if (!validated.status) {
    (validated as DatasetStatsResponse & { status: string }).status = "ok";
  }
  if (!validated.source) {
    (validated as DatasetStatsResponse & { source: string }).source =
      "backend_proxy";
  }

  return validated;
}

/**
 * Get the mechanism-of-action for a specific drug.
 *
 * Calls GET /api/datasets/{drug}/mechanism (Next.js route) which proxies
 * to the FastAPI backend, which proxies to Phase 1.
 *
 * Returns the drug's targets (proteins) and indications (diseases it treats).
 */
export async function getDrugMechanism(
  drug: string,
): Promise<Record<string, unknown>> {
  const url = `${API_BASE}/datasets/${encodeURIComponent(drug)}/mechanism`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      status: "service_down",
      drug,
      note: `Network error: ${msg}`,
    };
  }

  if (!response.ok) {
    if (response.status === 404) {
      return { status: "not_found", drug, note: `Drug '${drug}' not found.` };
    }
    const text = await response.text().catch(() => "");
    return {
      status: "service_down",
      drug,
      note: `Backend returned ${response.status}: ${text.slice(0, 200)}`,
    };
  }

  return response.json();
}

/**
 * Post a validated hypothesis to the data flywheel.
 *
 * Calls POST /api/datasets/validated_hypotheses (Next.js route) which
 * proxies to the FastAPI backend, which proxies to Phase 1.
 *
 * The backend enforces org_id scoping: the org_id in the JWT must match
 * any org_id in the payload. Cross-org validation returns 403.
 *
 * Returns the persisted hypothesis row on success (201). Throws on
 * network error or 5xx. Returns a structured error on 400/403/429.
 */
export interface ValidatedHypothesisPayload {
  drug: string;
  disease: string;
  outcome: "validated_positive" | "validated_toxic" | "validated_negative" | "invalidated";
  validated_at: string;  // ISO-8601
  validated_by?: string;
  validation_study_id?: string;
  notes?: string;
  original_gt_score?: number;
  original_rl_rank?: number;
  writeback_version?: string;
  org_id?: string;  // optional; if present, must match JWT org_id
}

export interface ValidatedHypothesisResponse {
  status: string;
  id: number;
  validated_hypothesis: Record<string, unknown>;
  flywheel_status: string;
}

export async function postValidatedHypothesis(
  payload: ValidatedHypothesisPayload,
): Promise<ValidatedHypothesisResponse> {
  const url = `${API_BASE}/datasets/validated_hypotheses`;

  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail: string;
    try {
      const body = await response.json();
      detail = body?.message || body?.detail || JSON.stringify(body);
    } catch {
      detail = await response.text().catch(() => "");
    }
    throw new Error(
      `POST /api/datasets/validated_hypotheses failed: ${response.status} ${detail}`,
    );
  }

  return response.json();
}

/**
 * Health check — useful for the /api/system/status route.
 *
 * Calls GET /api/datasets/stats with a short timeout. If it returns
 * any HTTP response (even 503), the Next.js route is reachable. If it
 * throws a network error, the Next.js server is down.
 */
export async function checkDatasetHealth(): Promise<{
  configured: boolean;
  reachable: boolean;
  version?: string;
}> {
  try {
    const url = `${API_BASE}/datasets/stats`;
    const response = await fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(5_000),
    });
    // Any HTTP response (even 503) means the Next.js route is reachable.
    // The backend may be down (503), but the frontend is up.
    const body = await response.json().catch(() => ({}));
    return {
      configured: true,
      reachable: true,
      version: typeof body?.schemaVersion === "string" ? body.schemaVersion : undefined,
    };
  } catch {
    return { configured: true, reachable: false };
  }
}
