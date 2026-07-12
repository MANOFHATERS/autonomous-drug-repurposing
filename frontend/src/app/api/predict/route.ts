import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { predictPairs, type DrugDiseasePair } from "@/lib/services/gt-inference";

/**
 * POST /api/predict
 * Body: { pairs: [{drug: string, disease: string}], limit?: number }
 *
 * RT-006 ROOT FIX (Team Member 17): this route exposes the Phase 3
 * Graph Transformer's `predict_drug_disease_scores()` inference function
 * to the frontend. A researcher can now ask "what is the GT score for
 * drug X -> disease Y?" and get a real answer in seconds.
 *
 * The route shells out to `scripts/gt_inference.py` (shipped with the
 * repo) which loads the trained checkpoint and runs the actual model.
 * We NEVER fabricate scores — if no checkpoint exists, we return an
 * empty list with `source: "none"` and a clear note.
 *
 * Phase 6 V1 launch criterion: "API handles 100 concurrent requests
 * without timeout" (project docx Section 8). The Python helper runs in
 * a subprocess per request; for high-concurrency deployments, set
 * GT_SERVICE_URL to proxy to a long-running FastAPI service instead.
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { pairs?: DrugDiseasePair[]; limit?: number };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Invalid JSON" },
      { status: 400 }
    );
  }

  if (!Array.isArray(body.pairs) || body.pairs.length === 0) {
    return NextResponse.json(
      { error: "bad_request", message: "pairs (array of {drug, disease}) is required" },
      { status: 400 }
    );
  }

  // Cap to prevent abuse — the GT model can score 100K pairs in seconds
  // on CPU, but a malicious caller could submit millions.
  const limit = Math.min(body.limit ?? 1000, 5000);
  const pairs = body.pairs.slice(0, limit);

  // Validate each pair
  for (const p of pairs) {
    if (typeof p.drug !== "string" || typeof p.disease !== "string" || !p.drug || !p.disease) {
      return NextResponse.json(
        { error: "bad_request", message: "Each pair must have non-empty string drug and disease" },
        { status: 400 }
      );
    }
  }

  try {
    const result = await predictPairs(pairs);
    await writeAuditLog({
      user: auth.user,
      action: "gt_predict",
      resource: "gt:predict",
      metadata: { count: result.count, source: result.source },
    });
    return NextResponse.json(result);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`GT predict failed: ${msg}`);
  }
}

/**
 * GET /api/predict?drug=<name>&disease=<name>
 *
 * Convenience single-pair GET. For batch scoring, use POST.
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

  try {
    const result = await predictPairs([{ drug, disease }]);
    await writeAuditLog({
      user: auth.user,
      action: "gt_predict",
      resource: `gt:${drug}:${disease}`,
      metadata: { count: result.count, source: result.source },
    });
    return NextResponse.json(result);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`GT predict failed: ${msg}`);
  }
}
