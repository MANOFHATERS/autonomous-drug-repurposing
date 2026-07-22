import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { mlFetch, MlServiceError, buildServiceUrl } from "@/lib/http-client";
import {
  SERVICE_PORTS,
  buildServiceUrlHint,
} from "@/../contracts/_url-constants";

/**
 * GET /api/top-k?top_k=<n>
 *
 * FE-001 ROOT FIX (Teammate 13, v143, CRITICAL — same recursive-call bug as /api/predict):
 *
 * The previous /api/top-k/route.ts imported `topKNovel` from
 * `@/lib/services/gt-inference` and called it. `topKNovel()` calls
 * `mlFetch(buildServiceUrl(API_BASE, "/top-k"))` where
 *   `API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api"`.
 * When NEXT_PUBLIC_API_BASE_URL is unset (the default in dev),
 * `API_BASE = "/api"` and the URL becomes the relative string "/api/top-k"
 * — server-side fetch cannot resolve relative URLs, so every /api/top-k
 * request returned 500 with `TypeError: Invalid URL`.
 *
 * ROOT FIX (Teammate 13, v143): the route now calls the FastAPI backend
 * DIRECTLY at `${DRUGOS_API_URL}/top-k?k=<n>` via `mlFetch`. No recursion
 * through gt-inference.ts. The FastAPI's /top-k endpoint is the canonical
 * source for top-K novel predictions (project docx §8: "We take the
 * model's top 50 novel predictions and run an automated PubMed literature
 * search" — this is the endpoint that feeds the Phase 6 literature
 * cross-check).
 *
 * The FastAPI response shape ({candidates, total, source, model_version})
 * is normalized to the GtInferenceResponse shape the frontend expects
 * ({predictions, source, modelVersion, generatedAt, count}) so the
 * contract is identical whether the caller hits /api/top-k (Next.js) or
 * /top-k (FastAPI) directly.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // Cap top_k at 500 to prevent runaway responses. The schema enforces
  // a positive integer; we additionally floor at 1.
  const topK = Math.max(1, Math.min(parseInt(req.nextUrl.searchParams.get("top_k") || "50", 10), 500));

  const drugosApiUrl = resolveDrugosApiUrl();
  if (!drugosApiUrl) {
    return NextResponse.json(
      {
        error: "service_unconfigured",
        message:
          "DRUGOS_API_URL is not set. The FastAPI backend " +
          "(backend/api/main.py) must be running and reachable. Start it with " +
          "`python -m backend.api.main` (defaults to port " +
          `${SERVICE_PORTS.backend_fastapi} per the canonical SERVICE_PORTS ` +
          "contract) and " + buildServiceUrlHint("DRUGOS_API_URL", "backend_fastapi") +
          ". FE-001 ROOT FIX (Teammate 13, v143): /api/top-k now proxies " +
          "directly to the FastAPI backend.",
        source: "none",
        count: 0,
        predictions: [],
        generatedAt: new Date().toISOString(),
      },
      { status: 503 }
    );
  }

  const qs = new URLSearchParams({ k: String(topK) });
  const topKUrl = buildServiceUrl(drugosApiUrl, `/top-k?${qs.toString()}`);
  const result = await mlFetch<unknown>(topKUrl, {
    service: "backend_fastapi",
    method: "GET",
    timeoutMs: 60_000,
    maxRetries: 2, // GET is idempotent
    headers: buildForwardedAuthHeaders(auth.user),
  });

  if (!result.ok) {
    const err = result.error as MlServiceError;
    if (err.httpStatus === 503) {
      return NextResponse.json(
        {
          error: "service_unavailable",
          message: `FastAPI /top-k returned 503: ${err.message}. The GT service may be down or the checkpoint may not be loaded.`,
          source: "none",
          count: 0,
          predictions: [],
          generatedAt: new Date().toISOString(),
        },
        { status: 503 }
      );
    }
    if (err.httpStatus >= 400 && err.httpStatus < 500) {
      return NextResponse.json(
        { error: "bad_request", message: err.message },
        { status: err.httpStatus }
      );
    }
    return internalError(`GT top-k failed: ${err.message}`);
  }

  // The FastAPI returns TopKResponse:
  //   { candidates: [{drug, disease, gnn_score, ...}], total, source, model_version }
  // Map to the GtInferenceResponse shape the frontend expects.
  const body = result.body as Record<string, unknown>;
  const candidates = Array.isArray(body.candidates) ? body.candidates : [];
  const predictions = candidates.map((c) => {
    const cand = (c as Record<string, unknown>) ?? {};
    return {
      drug: String(cand.drug ?? ""),
      disease: String(cand.disease ?? ""),
      score: Number(cand.gnn_score ?? 0),
    };
  });

  const responsePayload = {
    predictions,
    source: "gt_checkpoint",
    modelVersion: typeof body.model_version === "string" ? body.model_version : undefined,
    generatedAt: new Date().toISOString(),
    count: predictions.length,
    checkpointPath: null,
  };

  try {
    await writeAuditLog({
      user: auth.user,
      action: "gt_top_k",
      resource: "gt:top_k",
      metadata: { count: responsePayload.count, source: responsePayload.source, topK },
    });
  } catch {
    // non-fatal
  }

  return NextResponse.json(responsePayload);
}

// ---------------------------------------------------------------------------
// Helpers — identical to /api/predict/route.ts (FE-001 ROOT FIX, v143)
// ---------------------------------------------------------------------------

function resolveDrugosApiUrl(): string | null {
  const urls = [
    process.env.DRUGOS_API_URL,
    process.env.BACKEND_URL,
    process.env.BACKEND_SERVICE_URL,
  ];
  for (const u of urls) {
    if (u && u.trim()) return u.trim().replace(/\/$/, "");
  }
  return null;
}

function buildForwardedAuthHeaders(user: { userId: string; orgId?: string | null; role?: string }): Record<string, string> {
  const headers: Record<string, string> = {
    "X-Forwarded-From": "nextjs-internal",
    "X-DrugOS-User-Id": user.userId,
  };
  if (user.orgId) headers["X-DrugOS-Org-Id"] = user.orgId;
  if (user.role) headers["X-DrugOS-Role"] = user.role;
  return headers;
}
