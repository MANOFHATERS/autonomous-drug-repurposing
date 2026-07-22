import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
import { mlFetch, MlServiceError, buildServiceUrl } from "@/lib/http-client";
import { validateBody, PredictBody } from "@/lib/zod-schemas";
import {
  SERVICE_PORTS,
  buildServiceUrlHint,
} from "@/../contracts/_url-constants";

/**
 * FE-001 ROOT FIX (Teammate 13, v143, CRITICAL — recursive-call / orphaned-backend):
 *
 * === THE BUG (verified by reading the actual code, not the comments) ===
 * The previous /api/predict/route.ts imported `predictPairs` from
 * `@/lib/services/gt-inference` and called it. But `predictPairs()` calls
 * `mlFetch(buildServiceUrl(API_BASE, "/predict"))` where
 *   `API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api"`.
 * When NEXT_PUBLIC_API_BASE_URL is unset (the default in dev),
 * `API_BASE = "/api"` and the URL becomes the relative string `"/api/predict"`.
 * `fetch("/api/predict")` from a Next.js SERVER route handler has no origin
 * (server-side fetch cannot resolve a relative URL) → `mlFetch` throws a
 * `TypeError: Invalid URL` → every /api/predict request returns 500.
 *
 * When NEXT_PUBLIC_API_BASE_URL IS set, the call works — but it loops
 * through `gt-inference.ts` (browser-side helper) for no reason: the route
 * handler is server-side and can call the FastAPI backend directly.
 *
 * EITHER WAY the FastAPI backend (backend/api/main.py, default port 8004
 * per `uvicorn main:app --port 8004`) is NEVER called by /api/predict.
 * The only way to reach it is to hit it directly at
 * `http://localhost:8004/predict` — bypassing the Next.js route's CSRF
 * guard, audit log, and auth bridge. Pharma partners calling
 * `https://api.drugos.ai/predict` hit the FastAPI directly; researchers
 * using the dashboard hit /api/predict which calls gt-inference.ts which
 * fails to call anything. The two paths produce DIFFERENT results (FastAPI
 * returns real predictions; /api/predict returns 500) — the contract
 * violation that destroys trust, called out explicitly in the audit.
 *
 * === ROOT FIX (Teammate 13, v143) ===
 * 1. /api/predict/route.ts now calls the FastAPI backend DIRECTLY via
 *    `mlFetch(${DRUGOS_API_URL}/predict, ...)` — no recursion through
 *    gt-inference.ts.
 * 2. `DRUGOS_API_URL` env var (default `http://localhost:8004` per the
 *    canonical SERVICE_PORTS.backend_fastapi) is the SINGLE source of
 *    truth for the FastAPI address. `BACKEND_URL` is accepted as a
 *    legacy alias.
 * 3. The route forwards the user's auth state to the FastAPI via the
 *    `X-DrugOS-User-Id` / `X-DrugOS-Org-Id` / `X-DrugOS-Role` headers
 *    (read from `requireAuth()`). The FastAPI `verify_jwt` dependency
 *    accepts either a Bearer JWT OR these headers (the latter for
 *    same-network Next.js → FastAPI calls where the Next.js route has
 *    already verified the session cookie).
 * 4. gt-inference.ts predictPairs() is KEPT for browser-side use (React
 *    hooks in use-api-data.tsx call it to POST to /api/predict, which is
 *    THIS route — the browser-side path is unchanged).
 *
 * Phase 6 V1 launch criterion: "API handles 100 concurrent requests
 * without timeout" (project docx §8). The FastAPI (uvicorn with 4
 * workers, asyncio) handles 100+ concurrent requests; mlFetch's 60s
 * timeout + 1 retry (POST is non-idempotent) gives the backend time to
 * respond under load.
 */
export async function POST(req: NextRequest) {
  // Task 11.3 ROOT FIX (v129, TM11): CSRF protection on every
  // state-changing route. The /api/predict POST route was previously
  // MISSING the requireCsrfOrSend() call — an attacker on evil.com
  // could forge a POST that submits a large batch of (drug, disease)
  // pairs and exhausts the GT service's inference capacity (100
  // concurrent requests per the V1 criteria). The double-submit
  // cookie pattern (see lib/api-helpers.ts) blocks this attack.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { pairs?: unknown; limit?: number };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Invalid JSON" },
      { status: 400 }
    );
  }

  // BE-029 ROOT FIX: schema-validate the body BEFORE touching it.
  const parsed = validateBody(PredictBody, body);
  if (!parsed.ok) return parsed.response;

  // BE-030 ROOT FIX: the schema guarantees limit is a positive integer
  // ≤5000 (or undefined). We still cap at 5000 as defense-in-depth.
  const limit = Math.min(parsed.data.limit ?? 1000, 5000);
  const pairs = parsed.data.pairs.slice(0, limit);

  // FE-001 ROOT FIX (Teammate 13, v143): resolve the FastAPI URL.
  // DRUGOS_API_URL is the canonical env var; BACKEND_URL is the legacy
  // alias (kept so existing deployments don't break). If NEITHER is set,
  // we return a 503 with a hint built from SERVICE_PORTS.backend_fastapi
  // (port 8004 — matches `uvicorn main:app --port 8004`).
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
          ". FE-001 ROOT FIX (Teammate 13, v143): /api/predict now proxies " +
          "directly to the FastAPI backend — it no longer recurses through " +
          "gt-inference.ts.",
        source: "none",
        count: 0,
        predictions: [],
        generatedAt: new Date().toISOString(),
      },
      { status: 503 }
    );
  }

  // FE-001 ROOT FIX (Teammate 13, v143): call the FastAPI /predict
  // endpoint DIRECTLY. The FastAPI accepts the SAME body shape
  // ({pairs: [{drug, disease}], limit}) AND a single-pair shape
  // ({drug, disease}). We forward the full pairs list so the FastAPI
  // can batch internally (uvicorn's asyncio handles concurrent inference).
  const predictUrl = buildServiceUrl(drugosApiUrl, "/predict");
  const result = await mlFetch<unknown>(predictUrl, {
    service: "backend_fastapi",
    method: "POST",
    body: { pairs, limit },
    timeoutMs: 60_000, // GT inference can take time on large pair lists
    maxRetries: 1,     // POST is non-idempotent — at most 1 retry on 5xx
    // Forward the auth state so the FastAPI's verify_jwt dependency
    // can scope the request to the user's org. The FastAPI accepts
    // either a Bearer JWT (for direct pharma-partner calls) OR these
    // X-DrugOS-* headers (for Next.js → FastAPI same-network calls).
    headers: buildForwardedAuthHeaders(auth.user),
  });

  if (!result.ok) {
    const err = result.error as MlServiceError;
    // 503 = backend reachable but GT service down / checkpoint not loaded.
    // Surface as a structured "none" response so the dashboard shows the
    // "service down" state instead of crashing.
    if (err.httpStatus === 503) {
      return NextResponse.json(
        {
          error: "service_unavailable",
          message: `FastAPI /predict returned 503: ${err.message}. The GT service may be down or the checkpoint may not be loaded.`,
          source: "none",
          count: 0,
          predictions: [],
          generatedAt: new Date().toISOString(),
        },
        { status: 503 }
      );
    }
    // 4xx = bad request. Surface the message as-is.
    if (err.httpStatus >= 400 && err.httpStatus < 500) {
      return NextResponse.json(
        { error: "bad_request", message: err.message },
        { status: err.httpStatus }
      );
    }
    // 5xx / network — internal error.
    return internalError(`GT predict failed: ${err.message}`);
  }

  // The FastAPI returns a GtInferenceResponse-shaped object:
  //   { predictions: [...], source, modelVersion?, generatedAt, count, note? }
  // We pass it through unchanged so the frontend's contract is the SAME
  // whether the caller hits /api/predict (Next.js) or /predict (FastAPI)
  // directly. This is the contract equivalence the audit demanded.
  const responseBody = result.body as Record<string, unknown>;

  try {
    await writeAuditLog({
      user: auth.user,
      action: "gt_predict",
      resource: "gt:predict",
      metadata: {
        count: typeof responseBody.count === "number" ? responseBody.count : 0,
        source: typeof responseBody.source === "string" ? responseBody.source : "unknown",
        pairs: pairs.length,
      },
    });
  } catch {
    // Audit log failure is non-fatal — the prediction was already produced.
  }

  return NextResponse.json(responseBody);
}

/**
 * GET /api/predict?drug=<name>&disease=<name>
 *
 * Convenience single-pair GET. For batch scoring, use POST.
 *
 * FE-001 ROOT FIX (Teammate 13, v143): same direct-to-FastAPI proxy as POST.
 * The FastAPI's GET /predict?drug=&disease= returns a single PredictResponse
 * (not wrapped in {predictions}). We wrap it into the GtInferenceResponse
 * shape so the contract matches the POST response.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const drug = req.nextUrl.searchParams.get("drug");
  const disease = req.nextUrl.searchParams.get("disease");
  if (!drug || !disease) {
    return NextResponse.json(
      { error: "bad_request", message: "Both drug and disease query params are required" },
      { status: 400 }
    );
  }

  const drugosApiUrl = resolveDrugosApiUrl();
  if (!drugosApiUrl) {
    return NextResponse.json(
      {
        error: "service_unconfigured",
        message:
          "DRUGOS_API_URL is not set. " +
          buildServiceUrlHint("DRUGOS_API_URL", "backend_fastapi"),
        source: "none",
        count: 0,
        predictions: [],
        generatedAt: new Date().toISOString(),
      },
      { status: 503 }
    );
  }

  // Forward the same query string to the FastAPI.
  const qs = new URLSearchParams({ drug, disease });
  const predictUrl = buildServiceUrl(drugosApiUrl, `/predict?${qs.toString()}`);
  const result = await mlFetch<unknown>(predictUrl, {
    service: "backend_fastapi",
    method: "GET",
    timeoutMs: 60_000,
    maxRetries: 2, // GET is idempotent — allow up to 2 retries
    headers: buildForwardedAuthHeaders(auth.user),
  });

  if (!result.ok) {
    const err = result.error as MlServiceError;
    if (err.httpStatus === 503) {
      return NextResponse.json(
        {
          error: "service_unavailable",
          message: `FastAPI /predict returned 503: ${err.message}`,
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
    return internalError(`GT predict failed: ${err.message}`);
  }

  // The FastAPI GET /predict returns a single PredictResponse object:
  //   { drug, disease, gnn_score, confidence?, pathways?, model_version, ... }
  // Wrap it into the GtInferenceResponse shape so the contract matches POST.
  const single = result.body as Record<string, unknown>;
  const wrappedResponse = {
    predictions: [single],
    source: "gt_checkpoint",
    modelVersion: typeof single.model_version === "string" ? single.model_version : undefined,
    generatedAt: new Date().toISOString(),
    count: 1,
    checkpointPath: null,
  };

  try {
    await writeAuditLog({
      user: auth.user,
      action: "gt_predict",
      resource: `gt:${drug}:${disease}`,
      metadata: { count: 1, source: wrappedResponse.source },
    });
  } catch {
    // non-fatal
  }

  return NextResponse.json(wrappedResponse);
}

// ---------------------------------------------------------------------------
// Helpers (FE-001 ROOT FIX, Teammate 13, v143)
// ---------------------------------------------------------------------------

/**
 * Resolve the FastAPI backend URL from the canonical env var.
 * Returns null if neither DRUGOS_API_URL nor BACKEND_URL is set.
 *
 * NOTE: this is a SERVER-side env var (no NEXT_PUBLIC_ prefix) — the
 * browser cannot read it. The browser calls /api/predict (the Next.js
 * route), which resolves DRUGOS_API_URL server-side and proxies to the
 * FastAPI. This is the documented architecture: the FastAPI is
 * INTERNAL ONLY from the browser's perspective (only the Next.js route
 * is allowed to call it, after CSRF + auth checks).
 */
function resolveDrugosApiUrl(): string | null {
  const urls = [
    process.env.DRUGOS_API_URL,
    process.env.BACKEND_URL,        // legacy alias (Teammate 4)
    process.env.BACKEND_SERVICE_URL // legacy alias (Teammate 8)
  ];
  for (const u of urls) {
    if (u && u.trim()) return u.trim().replace(/\/$/, "");
  }
  return null;
}

/**
 * Build the X-DrugOS-* headers that the FastAPI's verify_jwt dependency
 * accepts for same-network Next.js → FastAPI calls. The Next.js route
 * has already verified the session cookie (via requireAuth), so the
 * FastAPI trusts these headers as proof of authentication.
 *
 * SECURITY: these headers MUST NOT be accepted from the browser. The
 * FastAPI's verify_jwt checks the `X-Forwarded-From: nextjs-internal`
 * header (set ONLY by this route) to distinguish browser-sourced calls
 * (which must present a Bearer JWT) from Next.js-sourced calls (which
 * may use these headers). A browser cannot forge `X-Forwarded-From`
 * because the FastAPI's CORS policy rejects browser requests with that
 * header.
 */
function buildForwardedAuthHeaders(user: { userId: string; orgId?: string | null; role?: string }): Record<string, string> {
  const headers: Record<string, string> = {
    "X-Forwarded-From": "nextjs-internal",
    "X-DrugOS-User-Id": user.userId,
  };
  if (user.orgId) headers["X-DrugOS-Org-Id"] = user.orgId;
  if (user.role) headers["X-DrugOS-Role"] = user.role;
  return headers;
}
