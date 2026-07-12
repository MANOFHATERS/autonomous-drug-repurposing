import { NextRequest, NextResponse } from "next/server";
import { checkRlAvailability } from "@/lib/services/ml-stubs";
import { db } from "@/lib/db";
import { writeAuditLog, requireAuth, internalError, requireCsrfOrSend } from "@/lib/api-helpers";
import {
  getRankedHypotheses,
  type RankedHypothesis,
} from "@/lib/services/rl-ranker";

/**
 * POST /api/rl
 * Body: { drug?: string, disease?: string, limit?: number }
 *
 * FE-019 ROOT FIX: This route previously had its OWN inline CSV parser
 * (parseRlCsv, RlCandidate interface) separate from
 * lib/services/rl-ranker.ts. Two divergent parsers, two env vars
 * (RL_LOCAL_CSV vs RL_OUTPUT_CSV_PATH), two schemas. The lib was dead
 * code. Root fix: deleted the inline parser, import getRankedHypotheses()
 * from the lib. ONE parser, ONE schema, ONE env var.
 *
 * FE-010 ROOT FIX: persistRlCandidates() previously wrote each RL
 * candidate to the Hypothesis table with `status: "validated"`. But
 * these are RAW MODEL PREDICTIONS, not validated hypotheses. A
 * "validated" hypothesis in pharma means wet-lab or clinical
 * confirmation. Mislabeling a model output as "validated" corrupts the
 * scientific record. Root fix: status="predicted", rlPredicted=true.
 *
 * FE-011: CSRF protection applied to POST.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { drug?: string; disease?: string; limit?: number };
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const drug = (body.drug || "").trim();
  const disease = (body.disease || "").trim();
  const limit = Math.min(body.limit ?? 50, 200);

  const availability = checkRlAvailability();
  const hasLocalCsv = Boolean(process.env.RL_OUTPUT_CSV_PATH || process.env.RL_LOCAL_CSV);
  if (!availability.available && !hasLocalCsv) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: availability.service,
        description: availability.description,
        reason: availability.reason,
        documentation:
          "See Phase 4 of the build plan (RL-Driven Hypothesis Ranking). " +
          "Set RL_SERVICE_URL to proxy to the standalone RL service, or " +
          "RL_OUTPUT_CSV_PATH to read a local output CSV in dev mode.",
      },
      { status: 503 }
    );
  }

  try {
    const result = await getRankedHypotheses({ drug: drug || undefined, disease: disease || undefined, limit });
    await persistRlCandidates(auth.user.userId, result.candidates);
    await writeAuditLog({
      user: auth.user,
      action: "rl_query",
      resource: `rl:${drug || "*"}:${disease || "*"}`,
      metadata: { count: result.candidates.length, source: result.source },
    });
    return NextResponse.json({
      candidates: result.candidates,
      source: result.source,
      csvPath: result.csvPath,
      total: result.candidates.length,
      note: result.note,
    });
  } catch (e: unknown) {
    // FE-063 ROOT FIX: `e: any` disabled type safety; if a non-Error was
    // thrown (e.g. a string), e.message was undefined and the response
    // became "undefined". Narrow with instanceof, fallback to String(e).
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`RL query failed: ${msg}`);
  }
}

export async function GET() {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  try {
    const result = await getRankedHypotheses({ limit: 50 });
    return NextResponse.json({
      candidates: result.candidates,
      source: result.source,
      csvPath: result.csvPath,
      total: result.candidates.length,
      note: result.note,
    });
  } catch (e: unknown) {
    // FE-063 ROOT FIX: `e: any` disabled type safety; if a non-Error was
    // thrown (e.g. a string), e.message was undefined and the response
    // became "undefined". Narrow with instanceof, fallback to String(e).
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`RL query failed: ${msg}`);
  }
}

/**
 * FE-010 ROOT FIX: candidates are persisted with status="predicted" and
 * rlPredicted=true. They are NOT "validated".
 */
async function persistRlCandidates(userId: string, candidates: RankedHypothesis[]): Promise<void> {
  if (candidates.length === 0) return;
  try {
    const membership = await db.organizationMember.findFirst({
      where: { userId },
      orderBy: { joinedAt: "asc" },
    });
    if (!membership) return;
    const project = await db.project.findFirst({
      where: { organizationId: membership.organizationId },
      orderBy: { createdAt: "asc" },
    });
    if (!project) return;

    for (const c of candidates.slice(0, 50)) {
      const existing = await db.hypothesis.findFirst({
        where: {
          projectId: project.id,
          drugName: c.drug,
          diseaseName: c.disease,
        },
      });
      const rlData = {
        plausibilityScore: c.plausibilityScore ?? c.gnnScore ?? null,
        safetyScore: c.safetyScore ?? null,
        marketScore: c.marketScore ?? null,
        overallScore: c.overallScore ?? null,
        rank: c.rank ?? null,
        policyProb: c.policyProb ?? null,
        reward: c.reward ?? null,
        gnnScore: c.gnnScore ?? null,
        literatureSupport: c.literatureSupportBool ?? (c.literatureSupport !== undefined ? c.literatureSupport > 0 : null),
        rlModelVersion: "rl_drug_ranker.py-v101",
        rlUpdatedAt: new Date(),
        rlPredicted: true,
      } as any;
      if (existing) {
        // FE-010: do NOT downgrade a wet-lab "validated" or "rejected"
        // hypothesis to "predicted".
        const nextStatus =
          existing.status === "validated" || existing.status === "rejected"
            ? existing.status
            : "predicted";
        await db.hypothesis.update({
          where: { id: existing.id },
          data: { ...rlData, status: nextStatus },
        });
      } else {
        await db.hypothesis.create({
          data: {
            projectId: project.id,
            title: `${c.drug} for ${c.disease}`,
            drugName: c.drug,
            diseaseName: c.disease,
            // FE-010: NEW RL-sourced hypotheses are "predicted", NOT "validated".
            status: "predicted",
            createdById: userId,
            notes: `RL rank ${c.rank ?? "—"}, reward ${c.reward?.toFixed(4) ?? "—"}, policy_prob ${c.policyProb?.toFixed(4) ?? "—"}`,
            ...rlData,
          } as any,
        });
      }
    }
  } catch (e: unknown) {
    // FE-063: explicit `e: unknown` — never `e: any`. Persistence is
    // best-effort; the response still returns the candidates. We log the
    // error for observability; if it's an Error we use .message, otherwise
    // String(e) so the log never shows "undefined".
    const msg = e instanceof Error ? e.message : String(e);
    console.error("persistRlCandidates failed:", msg);
  }
}
