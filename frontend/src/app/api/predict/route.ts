import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { predictPairs, type DrugDiseasePair } from "@/lib/services/gt-inference";
// BE-029 ROOT FIX (Team Member 12): Zod-validated request body.
// BE-030 ROOT FIX (Team Member 12): the previous `Math.min(body.limit
// ?? 1000, 5000)` returned NaN when body.limit was a non-numeric string
// ("abc"), causing `pairs.slice(0, NaN)` to silently return [] — the
// route returned count:0 with no error. The Zod schema rejects
// non-number limits at parse time, so the NaN path is impossible.
import { validateBody, PredictBody } from "@/lib/zod-schemas";

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

  // BE-029 ROOT FIX: schema-validate the body BEFORE touching it. The
  // schema (PredictBody) enforces:
  //   - pairs is a non-empty array (max 5000) of {drug, disease} objects
  //   - each drug/disease is a non-empty string ≤200 chars
  //   - limit, if present, is a positive integer ≤5000
  // This eliminates the BE-030 NaN bug (limit:"abc" → Math.min returns
  // NaN → slice(0, NaN) → empty array returned silently) because Zod
  // rejects non-number limits at parse time with a 400.
  const parsed = validateBody(PredictBody, body);
  if (!parsed.ok) return parsed.response;

  // BE-030 ROOT FIX: the schema guarantees limit is a positive integer
  // ≤5000 (or undefined). We still cap at 5000 as defense-in-depth.
  const limit = Math.min(parsed.data.limit ?? 1000, 5000);
  const pairs = parsed.data.pairs.slice(0, limit);

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
