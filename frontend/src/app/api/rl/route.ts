import { NextRequest, NextResponse } from "next/server";
import { checkRlAvailability } from "@/lib/services/ml-stubs";
import { db } from "@/lib/db";
import { writeAuditLog, requireAuth, badRequest, internalError } from "@/lib/api-helpers";
import { checkUserRateLimit } from "@/lib/auth/per-user-rate-limit";
import {
  readRlCsvCached,
  filterAndRankCandidates,
  type RlCandidate,
} from "@/lib/services/rl-csv-cache";

/**
 * POST /api/rl
 * Body: { drug?: string, disease?: string, limit?: number }
 *
 * FE-002 ROOT FIX: The previous code unconditionally returned 501 — even
 * when RL_SERVICE_URL was set. There was NO code anywhere in src/ that
 * read the Phase 4 RL ranker's output CSV. The candidate fields (drug,
 * disease, reward, rank, policy_prob, safety_score, literature_support)
 * appeared NOWHERE in the Next.js codebase.
 *
 * FE-069 ROOT FIX (this revision): The previous revision parsed the CSV
 * on EVERY request — O(n) disk I/O + parsing per call. A single
 * authenticated user could DoS the platform by spamming GET/POST /api/rl.
 *
 * Root fix applied:
 *   1. CSV parse result is cached in memory with a 60s TTL (rl-csv-cache.ts).
 *      A file watcher invalidates the cache immediately when the RL agent
 *      writes a new CSV — so re-running the ranker is reflected instantly.
 *   2. Per-user rate limiting: 60 req/min (per-user-rate-limit.ts). Spam
 *      from a single account is rejected with 429 + Retry-After before
 *      touching disk.
 *   3. The expensive filter+sort+slice now runs against the cached array
 *      (zero disk I/O on cache hits).
 *
 * Two integration paths remain:
 *   1. HTTP proxy (production): If RL_SERVICE_URL is set, POST to the
 *      standalone RL FastAPI service and stream back its JSON.
 *   2. Local CSV (dev/demo): If RL_LOCAL_CSV is set, parse it in-process
 *      (cached) and return the ranked candidates.
 *
 * If NEITHER env var is set, return 503 service_not_deployed — we NEVER
 * fabricate predictions. A pharma company might act on a fake "high
 * confidence" prediction — that's a patient-safety violation.
 */

// FE-069: 60 requests per minute per authenticated user. The RL endpoint
// is read-mostly (the dashboard polls for top-N candidates); 60/min is
// generous for legitimate use but stops a single user from DoSing the
// platform via CSV re-parsing.
const RL_RATE_LIMIT = { max: 60, windowSeconds: 60 };

function rateLimitedResponse(userId: string): NextResponse | null {
  const rl = checkUserRateLimit(userId, RL_RATE_LIMIT);
  if (rl.blocked) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: `Too many RL requests. Retry after ${rl.retryAfterSeconds}s.`,
        retryAfterSeconds: rl.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(rl.retryAfterSeconds) },
      }
    );
  }
  return null;
}

export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // FE-069: rate-limit BEFORE any disk I/O.
  const blocked = rateLimitedResponse(auth.user.userId);
  if (blocked) return blocked;

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
  if (!availability.available) {
    // Fall through to local CSV check below — maybe the dev set RL_LOCAL_CSV
    // without setting RL_SERVICE_URL. That's a valid dev configuration.
    if (!process.env.RL_LOCAL_CSV) {
      return NextResponse.json(
        {
          error: "service_not_deployed",
          service: availability.service,
          description: availability.description,
          reason: availability.reason,
          documentation:
            "See Phase 4 of the build plan (RL-Driven Hypothesis Ranking). " +
            "Set RL_SERVICE_URL to proxy to the standalone RL service, or " +
            "RL_LOCAL_CSV to read a local output CSV in dev mode.",
        },
        { status: 503 }
      );
    }
  }

  // Path 1: HTTP proxy to the standalone RL service.
  const rlServiceUrl = process.env.RL_SERVICE_URL;
  if (rlServiceUrl) {
    try {
      const upstream = await fetch(`${rlServiceUrl.replace(/\/$/, "")}/rank`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drug, disease, limit }),
      });
      if (!upstream.ok) {
        const text = await upstream.text();
        return NextResponse.json(
          {
            error: "rl_service_error",
            message: `RL service returned ${upstream.status}: ${text.slice(0, 500)}`,
          },
          { status: 502 }
        );
      }
      const data = await upstream.json();
      await persistRlCandidates(auth.user.userId, data.candidates || []);
      await writeAuditLog({
        user: auth.user,
        action: "rl_query",
        resource: `rl:${drug || "*"}:${disease || "*"}`,
        metadata: { count: (data.candidates || []).length, source: "proxy" },
      });
      return NextResponse.json(data);
    } catch (e: any) {
      return internalError(`RL service proxy failed: ${e.message}`);
    }
  }

  // Path 2: Local CSV (dev/demo mode). FE-069: cached parse + in-memory
  // filter. No disk I/O on cache hit.
  const csvPath = process.env.RL_LOCAL_CSV;
  if (csvPath) {
    try {
      const all = await readRlCsvCached(csvPath);
      const candidates = filterAndRankCandidates(all, { drug, disease, limit });
      await persistRlCandidates(auth.user.userId, candidates);
      await writeAuditLog({
        user: auth.user,
        action: "rl_query",
        resource: `rl:${drug || "*"}:${disease || "*"}`,
        metadata: { count: candidates.length, source: "local_csv" },
      });
      return NextResponse.json({
        candidates,
        source: "local_csv",
        csvPath,
        total: candidates.length,
      });
    } catch (e: any) {
      return internalError(`RL CSV parse failed: ${e.message}`);
    }
  }

  // Should not reach here (availability check above handles this), but
  // belt-and-suspenders.
  return NextResponse.json(
    { error: "service_not_deployed", message: "RL service is not configured." },
    { status: 503 }
  );
}

export async function GET() {
  // FE-001: dashboard calls GET for a default top-N list.
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // FE-069: rate-limit BEFORE any disk I/O.
  const blocked = rateLimitedResponse(auth.user.userId);
  if (blocked) return blocked;

  const csvPath = process.env.RL_LOCAL_CSV;
  if (csvPath) {
    try {
      const all = await readRlCsvCached(csvPath);
      const candidates = filterAndRankCandidates(all, { limit: 50 });
      return NextResponse.json({ candidates, source: "local_csv", total: candidates.length });
    } catch (e: any) {
      return internalError(`RL CSV parse failed: ${e.message}`);
    }
  }
  return NextResponse.json(
    { error: "service_not_deployed", message: "Set RL_SERVICE_URL or RL_LOCAL_CSV." },
    { status: 503 }
  );
}

// ---------------------------------------------------------------------------
// Persistence — store RL candidates as Hypothesis rows so the user can
// reference them in projects. Best-effort; failures are logged not thrown.
// ---------------------------------------------------------------------------

async function persistRlCandidates(userId: string, candidates: RlCandidate[]): Promise<void> {
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
      if (existing) {
        await db.hypothesis.update({
          where: { id: existing.id },
          data: {
            plausibilityScore: c.plausibilityScore,
            safetyScore: c.safetyScore,
            marketScore: c.marketScore,
            overallScore: c.overallScore,
            status: "validated",
          },
        });
      } else {
        await db.hypothesis.create({
          data: {
            projectId: project.id,
            title: `${c.drug} for ${c.disease}`,
            drugName: c.drug,
            diseaseName: c.disease,
            status: "validated",
            plausibilityScore: c.plausibilityScore,
            safetyScore: c.safetyScore,
            marketScore: c.marketScore,
            overallScore: c.overallScore,
            createdById: userId,
            notes: `RL rank ${c.rank}, reward ${c.reward.toFixed(4)}, policy_prob ${c.policyProb.toFixed(4)}, literature_support ${c.literatureSupport}`,
          },
        });
      }
    }
  } catch (e) {
    console.error("persistRlCandidates failed:", e);
  }
}
