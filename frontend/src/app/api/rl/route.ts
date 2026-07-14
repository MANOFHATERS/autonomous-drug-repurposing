import { NextRequest, NextResponse } from "next/server";
import { checkRlAvailability } from "@/lib/services/ml-stubs";
import { db } from "@/lib/db";
import { writeAuditLog, requireAuth, internalError, requireCsrfOrSend } from "@/lib/api-helpers";
import {
  getRankedHypotheses,
  type RankedHypothesis,
} from "@/lib/services/rl-ranker";
// FE-069 ROOT FIX: per-user rate limiting. The CSV cache lives inside
// rl-ranker.ts (the single source of truth for parsing); the rate limiter
// lives here at the route boundary so a flood of requests is rejected
// BEFORE any disk I/O or upstream HTTP call.
//
// FE-017 ROOT FIX (Team Member 14): Use the DISTRIBUTED rate limiter so the
// cap is enforced across all Node.js instances (K8s replicas, etc.). When
// REDIS_URL is set, this hits Redis (shared state). When REDIS_URL is NOT
// set, it falls back to the in-memory Map (single-instance dev/test). The
// sync `checkUserRateLimit` is kept for backwards compatibility with
// existing tests that mock it; the route uses the async version so
// production multi-instance deployments get the correct per-user cap.
import { checkUserRateLimitDistributed as checkUserRateLimitAsync } from "@/lib/auth/per-user-rate-limit";
// Keep the sync import so the route can fall back to it if the async path
// throws (e.g. Redis briefly unreachable). This is a defense-in-depth
// measure: a Redis outage should NOT disable rate limiting entirely.
import { checkUserRateLimit as checkUserRateLimitSync } from "@/lib/auth/per-user-rate-limit";

/**
 * POST /api/rl
 * Body: { drug?: string, disease?: string, limit?: number, sort?: RlSortField, sortDir?: 'asc'|'desc', page?: number, pageSize?: number }
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
 *
 * FE-069 ROOT FIX: per-user rate limiting (60 req/min) added to BOTH GET
 * and POST. The CSV cache lives inside rl-ranker.ts's readLocalCsv (TTL +
 * mtime + fs.watch). The rate limiter is checked BEFORE any disk I/O so a
 * flood of requests is rejected at the gate with 429 + Retry-After.
 *
 * FE-033 ROOT FIX: Server-side sort + pagination. The previous version
 * accepted only `drug`, `disease`, `limit` and returned all matching
 * candidates (capped at 200). The candidate table then sorted client-side,
 * which froze the browser at 100K-candidate production scale. Root fix:
 * the route now accepts `sort`, `sortDir`, `page`, `pageSize` and passes
 * them to getRankedHypotheses(). The response includes `total` (count
 * after filtering, before pagination) and `page` / `pageSize` so the
 * caller can render "Showing X–Y of Z" and pagination controls. Default
 * page size is 50; max is 200.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // FE-069 ROOT FIX: per-user rate limit (60 req/min). Checked BEFORE we
  // parse the request body or touch disk — a flood of requests is rejected
  // at the gate. The 429 response includes Retry-After so well-behaved
  // clients back off correctly.
  //
  // FE-017 ROOT FIX (Team Member 14): use the DISTRIBUTED (async) limiter
  // so the cap is enforced across all Node.js instances. When REDIS_URL is
  // set, this hits Redis (shared state). When REDIS_URL is NOT set, it
  // falls back to the in-memory Map. If the async path throws (e.g. Redis
  // briefly unreachable), we fall back to the sync in-memory limiter so a
  // Redis outage does NOT disable rate limiting entirely.
  let rl;
  try {
    rl = await checkUserRateLimitAsync(auth.user.userId, { max: 60, windowSeconds: 60 });
  } catch (e) {
    console.error("[RATE-LIMIT] async limiter failed, falling back to sync:", e);
    rl = checkUserRateLimitSync(auth.user.userId, { max: 60, windowSeconds: 60 });
  }
  if (rl.blocked) {
    return NextResponse.json(
      { error: "rate_limited", message: "Too many RL requests. Please slow down." },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterSeconds) } }
    );
  }

  let body: {
    drug?: string;
    disease?: string;
    limit?: number;
    sort?: string;
    sortDir?: string;
    page?: number;
    pageSize?: number;
    orgId?: string;
  };
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const drug = (body.drug || "").trim();
  const disease = (body.disease || "").trim();

  // FE-033: Parse + validate sort + pagination params. Invalid values fall
  // back to the defaults (rank/asc, page 0, pageSize 50).
  const VALID_SORT_FIELDS = ['rank', 'overallScore', 'gnnScore', 'safetyScore', 'marketScore', 'reward', 'drug', 'disease'] as const;
  const VALID_SORT_DIRS = ['asc', 'desc'] as const;
  const sort = (VALID_SORT_FIELDS as readonly string[]).includes(body.sort || '')
    ? (body.sort as typeof VALID_SORT_FIELDS[number])
    : undefined;
  const sortDir = (VALID_SORT_DIRS as readonly string[]).includes(body.sortDir || '')
    ? (body.sortDir as typeof VALID_SORT_DIRS[number])
    : undefined;
  const pageSizeRaw = Math.min(Math.max(1, Number(body.pageSize ?? body.limit ?? 50) || 50), 200);
  const pageRaw = Math.max(0, Math.floor(Number(body.page ?? 0) || 0));
  const offset = pageRaw * pageSizeRaw;

  // FE-007 ROOT FIX (Team Member 13): accept an explicit orgId.
  //
  // The POST body may carry `orgId` to scope the RL candidate persistence
  // to a specific org. This is critical for pharma consortia users who
  // are members of multiple orgs — without this, the previous code used
  // `findFirst({ orderBy: { joinedAt: "asc" } })` which always picked
  // the user's FIRST org, even if they intended to query for a different
  // org. Cross-org data leakage was possible.
  //
  // Resolution order:
  //   1. body.orgId (explicit) — wins.
  //   2. query string ?orgId=... — also explicit.
  //   3. auth.user.orgId — the user's CURRENT active org (from the
  //      session token, NOT their first org by joinedAt). This is the
  //      default — it respects the org the user is currently switched
  //      into in the dashboard.
  //   4. null — no org scoping. persistRlCandidates will skip persistence
  //      (it cannot create a project without an org).
  //
  // SECURITY: the orgId MUST be one of the user's actual memberships.
  // persistRlCandidates verifies this with `organizationMember.findFirst`
  // before creating or finding a project. A user cannot inject an orgId
  // for an org they don't belong to.
  const explicitOrgId =
    (body.orgId && typeof body.orgId === "string" ? body.orgId : undefined) ||
    (req.nextUrl.searchParams.get("orgId") || undefined);
  const targetOrgId = explicitOrgId || auth.user.orgId || null;

  const availability = checkRlAvailability();
  // FE-003 ROOT FIX: the local-CSV path is no longer gated on the env
  // var being set. The lib service `rl-ranker.ts` now resolves the
  // default path to the LATEST `top_candidates_*.csv` file (the real
  // Phase 4 output), falling back to `validated_hypotheses.csv` only
  // when no top_candidates_*.csv exists. The dashboard therefore shows
  // real RL predictions by default — not the 4 hardcoded known-positive
  // FDA-approved drugs in validated_hypotheses.csv.
  //
  // We always attempt the local-CSV path when RL_SERVICE_URL is unset.
  // The lib service handles the "no CSV file exists" case gracefully
  // (returns source: "none" with an empty candidate list), and the
  // route returns 503 only if the service is unset AND the lib returns
  // zero candidates AND the resolved CSV path does not exist.
  const hasLocalCsv = Boolean(
    process.env.RL_OUTPUT_CSV_PATH ||
      process.env.RL_LOCAL_CSV ||
      process.env.RL_OUTPUT_DIR
  );
  if (!availability.available && !hasLocalCsv) {
    // Even without env vars, the lib service may find a top_candidates_*.csv
    // in the default search locations. Defer to getRankedHypotheses() — if
    // it returns source: "none", we return 503 below.
  }

  try {
    const result = await getRankedHypotheses({
      drug: drug || undefined,
      disease: disease || undefined,
      sort,
      sortDir,
      offset,
      pageSize: pageSizeRaw,
    });

    // FE-003 (Team 13): if neither the service URL is set nor any local CSV
    // was found, the lib returns source: "none" with an empty candidate
    // list. Return 503 so the dashboard shows a clear "RL not run yet"
    // state instead of presenting an empty table.
    if (!availability.available && result.source === "none") {
      return NextResponse.json(
        {
          error: "service_not_deployed",
          service: availability.service,
          description: availability.description,
          reason:
            availability.reason +
            " No local top_candidates_*.csv file was found in rl/, " +
            "rl/output/, or $RL_OUTPUT_DIR either. Run the Phase 4 RL " +
            "ranker (rl/rl_drug_ranker.py) to produce real predictions, " +
            "or set RL_SERVICE_URL to proxy to the standalone service.",
          documentation:
            "See Phase 4 of the build plan (RL-Driven Hypothesis " +
            "Ranking). Set RL_SERVICE_URL to proxy to the standalone " +
            "RL service, or RL_OUTPUT_CSV_PATH / RL_OUTPUT_DIR to read " +
            "a local output CSV in dev mode.",
        },
        { status: 503 }
      );
    }

    // FE-007 (Team 13): pass targetOrgId so persistence is scoped to the
    // user's intended org, not their first org by joinedAt.
    await persistRlCandidates(auth.user.userId, result.candidates, targetOrgId);
    await writeAuditLog({
      user: auth.user,
      action: "rl_query",
      resource: `rl:${drug || "*"}:${disease || "*"}`,
      metadata: {
        count: result.candidates.length,
        source: result.source,
        sort: sort || 'rank',
        sortDir: sortDir || 'asc',
        page: pageRaw,
        pageSize: pageSizeRaw,
        total: result.total ?? result.candidates.length,
      },
    });
    return NextResponse.json({
      candidates: result.candidates,
      source: result.source,
      csvPath: result.csvPath,
      // FE-033: `total` is the count AFTER filtering but BEFORE pagination.
      // The candidate table uses this to render "Showing X–Y of Z" and
      // pagination controls. `count` is the page size (length of candidates
      // on the current page).
      total: result.total ?? result.candidates.length,
      page: pageRaw,
      pageSize: pageSizeRaw,
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

export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // FE-069 ROOT FIX: per-user rate limit (60 req/min) on GET too. The GET
  // handler is the one most easily spammed (no body required), so it must
  // be throttled identically to POST.
  //
  // FE-017 ROOT FIX (Team Member 14): use the DISTRIBUTED (async) limiter
  // with sync fallback (see POST handler above for rationale).
  let rl;
  try {
    rl = await checkUserRateLimitAsync(auth.user.userId, { max: 60, windowSeconds: 60 });
  } catch (e) {
    console.error("[RATE-LIMIT] async limiter failed, falling back to sync:", e);
    rl = checkUserRateLimitSync(auth.user.userId, { max: 60, windowSeconds: 60 });
  }
  if (rl.blocked) {
    return NextResponse.json(
      { error: "rate_limited", message: "Too many RL requests. Please slow down." },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterSeconds) } }
    );
  }

  // FE-033: GET also accepts sort + pagination via query params so the
  // candidate table can paginate without a POST body.
  const sp = req.nextUrl.searchParams;
  const VALID_SORT_FIELDS = ['rank', 'overallScore', 'gnnScore', 'safetyScore', 'marketScore', 'reward', 'drug', 'disease'] as const;
  const VALID_SORT_DIRS = ['asc', 'desc'] as const;
  const sort = (VALID_SORT_FIELDS as readonly string[]).includes(sp.get('sort') || '')
    ? (sp.get('sort') as typeof VALID_SORT_FIELDS[number])
    : undefined;
  const sortDir = (VALID_SORT_DIRS as readonly string[]).includes(sp.get('sortDir') || '')
    ? (sp.get('sortDir') as typeof VALID_SORT_DIRS[number])
    : undefined;
  const pageSizeRaw = Math.min(Math.max(1, Number(sp.get('pageSize') ?? sp.get('limit') ?? 50) || 50), 200);
  const pageRaw = Math.max(0, Math.floor(Number(sp.get('page') ?? 0) || 0));
  const offset = pageRaw * pageSizeRaw;

  const availability = checkRlAvailability();

  try {
    const result = await getRankedHypotheses({
      sort,
      sortDir,
      offset,
      pageSize: pageSizeRaw,
    });

    // FE-003 (Team 13): same 503 fallback as POST. The dashboard's RL page
    // calls GET to populate the table — if neither the service nor a local
    // CSV is available, return 503 so the UI shows a clear state.
    if (!availability.available && result.source === "none") {
      return NextResponse.json(
        {
          error: "service_not_deployed",
          service: availability.service,
          description: availability.description,
          reason:
            availability.reason +
            " No local top_candidates_*.csv file was found in rl/, " +
            "rl/output/, or $RL_OUTPUT_DIR either. Run the Phase 4 RL " +
            "ranker (rl/rl_drug_ranker.py) to produce real predictions, " +
            "or set RL_SERVICE_URL to proxy to the standalone service.",
          documentation:
            "See Phase 4 of the build plan (RL-Driven Hypothesis " +
            "Ranking).",
        },
        { status: 503 }
      );
    }


    return NextResponse.json({
      candidates: result.candidates,
      source: result.source,
      csvPath: result.csvPath,
      total: result.total ?? result.candidates.length,
      page: pageRaw,
      pageSize: pageSizeRaw,
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
 *
 * FE-037 ROOT FIX (re-applied after regression): persistRlCandidates
 * previously found the user's FIRST org membership, then found the FIRST
 * project in that org (ordered by createdAt asc), and upserted hypotheses
 * into it. The user may not be the owner or even a member of that project
 * (the Project model has no ProjectMember table — projects are org-scoped,
 * not user-scoped). So user A's RL query could populate user B's project
 * with hypotheses.
 *
 * Root fix: We now find or create a DEDICATED 'RL Predictions' project
 * OWNED BY the calling user. This guarantees:
 *   - The user is the owner of the project (createdById = userId).
 *   - Other users' projects are NEVER touched.
 *   - Re-running the RL agent upserts into the same dedicated project
 *     (keyed on the project name + ownerId) so re-runs update scores
 *     rather than creating duplicate projects.
 *
 * FE-007 ROOT FIX (Team Member 13): the function now accepts a `targetOrgId`
 * parameter. This is the org the candidate persistence should be scoped to.
 * Resolution:
 *   - If `targetOrgId` is provided, we verify the user is a member of
 *     that org (organizationMember.findFirst) and use it.
 *   - If `targetOrgId` is null, we fall back to the user's FIRST org
 *     membership by joinedAt asc (the original behavior) — but only as
 *     a last resort. The route layer passes `auth.user.orgId` (the
 *     user's CURRENT active org from the session) by default, so this
 *     fallback is rarely hit.
 *
 * SECURITY: the membership check is mandatory — a user cannot inject an
 * orgId for an org they don't belong to. If the membership check fails,
 * we SKIP persistence (return early) rather than falling back to the
 * first org — that would be a security hole.
 */
/**
 * BE-014 ROOT FIX: The previous implementation did up to 50 sequential
 * findFirst + update/create queries per request (6000 DB queries/min at
 * 60 req/min), and swallowed ALL errors in a catch block — the route
 * returned 200 even if nothing was saved. Data-loss silent failure.
 *
 * Root fix:
 *   1. Use db.$transaction to batch all writes atomically.
 *   2. Collect all operations into a single transaction — no partial saves.
 *   3. If the transaction fails, THROW — the caller returns 500.
 *   4. Do NOT swallow errors. Persistence failure is a data-loss event.
 *   5. Return { saved: count } so the caller can include it in the response.
 */
async function persistRlCandidates(
  userId: string,
  candidates: RankedHypothesis[],
  targetOrgId: string | null
): Promise<{ saved: number }> {
  if (candidates.length === 0) return { saved: 0 };

  // FE-007: resolve the org to persist into.
  let organizationId: string | null = null;
  if (targetOrgId) {
    const membership = await db.organizationMember.findFirst({
      where: { userId, organizationId: targetOrgId },
    });
    if (!membership) {
      console.warn(
        `persistRlCandidates: user ${userId} is not a member of org ${targetOrgId}; skipping persistence.`
      );
      return { saved: 0 };
    }
    organizationId = targetOrgId;
  } else {
    const membership = await db.organizationMember.findFirst({
      where: { userId },
      orderBy: { joinedAt: "asc" },
    });
    if (!membership) return { saved: 0 };
    organizationId = membership.organizationId;
  }

  // FE-037: Find or create a DEDICATED 'RL Predictions' project.
  const RL_PROJECT_NAME = "RL Predictions";
  let project = await db.project.findFirst({
    where: { ownerId: userId, name: RL_PROJECT_NAME, organizationId },
  });
  if (!project) {
    try {
      project = await db.project.create({
        data: {
          name: RL_PROJECT_NAME,
          description: "Auto-populated by the Phase 4 RL ranker. Hypotheses here are derived from RL predictions — verify before acting on them.",
          status: "active",
          visibility: "private",
          ownerId: userId,
          organizationId,
          tags: "rl,predictions,auto-generated",
        },
      });
    } catch {
      project = await db.project.findFirst({
        where: { ownerId: userId, name: RL_PROJECT_NAME, organizationId },
      });
      if (!project) throw new Error("Failed to find or create RL Predictions project");
    }
  }

  // BE-014 ROOT FIX: Batch all hypothesis operations inside a single
  // transaction. All-or-nothing: if any operation fails, nothing is saved.
  const toSave = candidates.slice(0, 50);
  try {
    await db.$transaction(async (tx) => {
      for (const c of toSave) {
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
        };

        // Find existing hypothesis for upsert logic.
        const existing = await tx.hypothesis.findFirst({
          where: {
            projectId: project!.id,
            drugName: c.drug,
            diseaseName: c.disease,
          },
        });

        if (existing) {
          // FE-010: do NOT downgrade a wet-lab "validated" or "rejected".
          const nextStatus =
            existing.status === "validated" || existing.status === "rejected"
              ? existing.status
              : "predicted";
          await tx.hypothesis.update({
            where: { id: existing.id },
            data: { ...rlData, status: nextStatus },
          });
        } else {
          await tx.hypothesis.create({
            data: {
              projectId: project!.id,
              title: `${c.drug} for ${c.disease}`,
              drugName: c.drug,
              diseaseName: c.disease,
              status: "predicted",
              createdById: userId,
              notes: `RL rank ${c.rank ?? "—"}, reward ${c.reward?.toFixed(4) ?? "—"}`,
              ...rlData,
            },
          });
        }
      }
    }, {
      maxWait: 5000,
      timeout: 10000,
    });
    return { saved: toSave.length };
  } catch (e: unknown) {
    // BE-014: THROW — do NOT swallow. The caller must return 500.
    const msg = e instanceof Error ? e.message : String(e);
    console.error("BE-014 persistRlCandidates transaction failed:", msg);
    throw new Error(`Failed to persist RL candidates: ${msg}`);
  }
}
