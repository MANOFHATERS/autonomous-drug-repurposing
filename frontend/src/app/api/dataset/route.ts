import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { getDatasetStats } from "@/lib/services/dataset-stats";

/**
 * GET /api/dataset?source=<chembl|drugbank|uniprot|string|disgenet|omim|pubchem>
 *
 * FE-021 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE (forensic): The previous route NEVER called
 * `getDatasetStats()`. It only checked `checkDatasetAvailability()`
 * (which requires `DATASET_SERVICE_URL`) and returned 503
 * `service_not_deployed` when that env var was not set — even though
 * `getDatasetStats()` had a perfectly good local-checkpoint fallback
 * that reads `../phase2/data/checkpoints/step_01.json`. On a fresh
 * deploy without `DATASET_SERVICE_URL`, the dashboard showed a generic
 * "service not deployed" error instead of either:
 *   (a) the real local checkpoint data (if Phase 1 had been run), or
 *   (b) a clear "No data ingested yet — run Phase 1 to populate" message.
 *
 * The local-checkpoint fallback in `dataset-stats.ts` was effectively
 * dead code: no route wired it up.
 *
 * ROOT FIX:
 *   1. Always call `getDatasetStats()` first. That function handles the
 *      proxy-vs-local-vs-none decision tree and returns a `status` field
 *      (`ok` | `no_data` | `service_down`) so the dashboard can render
 *      a clear message.
 *   2. When `status === "no_data"`, return HTTP 200 with the status
 *      field — NOT 503 or 500. The request succeeded; there just isn't
 *      any data yet. The dashboard renders the helpful message.
 *   3. When `status === "service_down"` (proxy was configured but
 *      failed AND no local checkpoint exists), return HTTP 502.
 *   4. When `status === "ok"`, return HTTP 200 with the stats.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate dataset statistics. If the
 * checkpoint is missing we return `status: "no_data"` with empty
 * arrays — the dashboard shows "Run Phase 1 to populate" instead of
 * fake numbers.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const stats = await getDatasetStats();

    await writeAuditLog({
      user: auth.user,
      action: "dataset_query",
      resource: `dataset:${req.nextUrl.searchParams.get("source") || "all"}`,
      metadata: { source: stats.source, status: stats.status },
    });

    // FE-021: 200 for ok + no_data (request succeeded; data may be empty).
    // 502 only when the proxy was configured but failed AND no local
    // checkpoint exists.
    if (stats.status === "service_down") {
      return NextResponse.json(stats, { status: 502 });
    }
    return NextResponse.json(stats);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Dataset stats failed: ${msg}`);
  }
}
