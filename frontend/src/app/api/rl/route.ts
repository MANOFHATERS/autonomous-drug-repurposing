import { NextRequest, NextResponse } from "next/server";
import { getRankedHypotheses, syncRlOutputToHypotheses } from "@/lib/services/rl-ranker";
import { requireAuth, badRequest, internalError } from "@/lib/api-helpers";

/**
 * POST /api/rl
 * Body: { drug?: string, disease?: string, limit?: number, sync?: boolean }
 *
 * ROOT FIX for FE-002: /api/rl no longer returns 501. It now reads the
 * real Phase 4 RL ranker output (from RL_SERVICE_URL if set, otherwise
 * from the local `rl/validated_hypotheses.csv` artifact) and returns real
 * ranked repurposing candidates.
 *
 * If `sync: true` is passed in the body, the endpoint also writes the
 * ranked candidates back into the Hypothesis table — populating the
 * plausibilityScore / safetyScore / overallScore / policyProb / reward /
 * gnnScore / literatureSupport / rank / rlModelVersion / rlUpdatedAt
 * fields. This is the missing Phase 4 → DB handoff: previously the
 * Hypothesis schema had these fields but no code ever populated them, so
 * they stayed null forever and the dashboard's "Repurposing Confidence:
 * 92%" was a hardcoded mock.
 *
 * Auth: any authenticated user can read; only `admin`/`owner`/`developer`
 * can pass `sync: true` (because it mutates the DB).
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { drug?: string; disease?: string; limit?: number; sync?: boolean } = {};
  try {
    body = await req.json();
  } catch {
    // Empty body is fine — return all candidates.
  }

  const limit = typeof body.limit === "number" ? Math.max(1, Math.min(200, body.limit)) : 50;

  // Sync mode requires elevated role.
  if (body.sync && auth.user.role !== "admin" && auth.user.role !== "owner" && auth.user.role !== "developer") {
    return NextResponse.json(
      { error: "forbidden", message: "sync=true requires admin, owner, or developer role" },
      { status: 403 }
    );
  }

  try {
    const result = await getRankedHypotheses({
      drug: body.drug,
      disease: body.disease,
      limit,
    });
    let syncedCount = 0;
    if (body.sync) {
      syncedCount = await syncRlOutputToHypotheses();
    }
    return NextResponse.json({
      ...result,
      syncedHypotheses: syncedCount,
    });
  } catch (e: any) {
    return internalError(`RL ranker lookup failed: ${e.message}`);
  }
}

/**
 * GET /api/rl?drug=...&disease=...&limit=...
 *
 * Read-only variant for easy curl / dashboard use.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const drug = req.nextUrl.searchParams.get("drug") || undefined;
  const disease = req.nextUrl.searchParams.get("disease") || undefined;
  const limitRaw = req.nextUrl.searchParams.get("limit");
  const limit = limitRaw ? Math.max(1, Math.min(200, parseInt(limitRaw, 10) || 50)) : 50;

  try {
    const result = await getRankedHypotheses({ drug, disease, limit });
    return NextResponse.json(result);
  } catch (e: any) {
    return badRequest(`RL ranker lookup failed: ${e.message}`);
  }
}
