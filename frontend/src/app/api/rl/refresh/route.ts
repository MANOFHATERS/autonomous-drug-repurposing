import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { requireCsrfOrSend } from "@/lib/api-helpers";
// BE-073 ROOT FIX: Import from rl-ranker.ts (the ACTUAL cache used by
// /api/rl), NOT from rl-csv-cache.ts (a different cache that no route
// uses). The previous import was:
//   import { clearRlCsvCache, getRlCsvCacheState } from "@/lib/services/rl-csv-cache";
// This cleared a cache that was never read — the "Refresh" button was
// a no-op. Now we clear the rl-ranker.ts cache that /api/rl actually
// hits.
import {
  clearRlRankerCache,
  getRlRankerCacheState,
} from "@/lib/services/rl-ranker";

/**
 * POST /api/rl/refresh
 *
 * BE-073 ROOT FIX (replaces FE-022):
 *
 * ROOT CAUSE: `/api/rl/refresh` was calling `clearRlCsvCache()` from
 * `rl-csv-cache.ts`. But `/api/rl` uses `rl-ranker.ts`'s `readLocalCsv`
 * which has its OWN cache. Clearing `rl-csv-cache.ts` did NOT clear
 * `rl-ranker.ts`'s cache. The "Refresh" button was a no-op.
 *
 * ROOT FIX: Import `clearRlRankerCache` and `getRlRankerCacheState` from
 * `rl-ranker.ts` (the module that ACTUALLY serves /api/rl). The cache
 * clear now targets the correct cache, so the next GET /api/rl re-reads
 * the CSV from disk.
 *
 * Auth: any authenticated user may refresh (the cache is per-process,
 * not per-user — refreshing for one user refreshes for all). We log
 * the action to the audit trail so admins can see who refreshed and
 * when.
 *
 * CSRF: required (this is a state-changing POST).
 */
export async function POST(req: NextRequest) {
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const stateBefore = getRlRankerCacheState();
    clearRlRankerCache();
    const stateAfter = getRlRankerCacheState();

    await writeAuditLog({
      user: auth.user,
      action: "rl_cache_refresh",
      resource: "rl:cache",
      metadata: {
        clearedEntries: stateBefore.length,
        remainingEntries: stateAfter.length,
      },
    });

    return NextResponse.json({
      ok: true,
      clearedEntries: stateBefore.length,
      message:
        stateBefore.length === 0
          ? "RL cache was already empty — nothing to refresh."
          : `RL cache cleared (${stateBefore.length} entr${stateBefore.length === 1 ? "y" : "ies"}). The next request will re-read the CSV from disk.`,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`RL cache refresh failed: ${msg}`);
  }
}
