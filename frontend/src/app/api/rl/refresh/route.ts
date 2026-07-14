import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { requireCsrfOrSend } from "@/lib/api-helpers";
import {
  clearRlCsvCache,
  getRlCsvCacheState,
} from "@/lib/services/rl-csv-cache";
// BE-073 ROOT FIX: The rl-csv-cache.ts module has its OWN cache, but
// /api/rl/route.ts imports getRankedHypotheses from rl-ranker.ts which
// has a SEPARATE cache (csvCache in readLocalCsv). Clearing
// rl-csv-cache.ts's cache does NOT clear rl-ranker.ts's cache — so the
// "Refresh" button was a no-op for the actual route that serves RL data.
//
// Root fix: Also import and clear the rl-ranker.ts cache. Both caches
// are cleared on refresh so the next request to /api/rl re-reads the
// CSV from disk regardless of which code path served it.
import {
  __clearRlRankerCsvCacheForTests as clearRlRankerCache,
} from "@/lib/services/rl-ranker";

/**
 * POST /api/rl/refresh
 *
 * FE-022 ROOT FIX (Team Member 15) + BE-073 ROOT FIX:
 *
 * ROOT CAUSE: `rl-csv-cache.ts` relies on `fs.watch()` to invalidate
 * the cache when the underlying RL output CSV changes. `fs.watch()` is
 * unreliable on NFS, Samba, and some Linux filesystems — the change
 * event may not fire, leaving the cache stale until the 60s TTL
 * expires. For a pharma partner demo, 60 seconds of stale RL data is
 * noticeable.
 *
 * Additionally (BE-073), the `/api/rl` route uses `rl-ranker.ts`'s
 * `readLocalCsv` which has its OWN cache, separate from `rl-csv-cache.ts`.
 * Clearing only `rl-csv-cache.ts` does NOT clear `rl-ranker.ts`'s cache,
 * making the refresh button a no-op.
 *
 * ROOT FIX: expose a manual refresh endpoint that clears BOTH caches.
 * The dashboard renders a "Refresh" button; clicking it POSTs to this
 * route, which calls `clearRlCsvCache()` AND `clearRlRankerCache()` to
 * evict all cached entries from both modules. The next `GET /api/rl`
 * re-reads the CSV from disk.
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
    // BE-073: Clear BOTH caches. rl-csv-cache.ts's cache AND
    // rl-ranker.ts's cache are independent — clearing only one leaves
    // the other stale.
    const stateBeforeA = getRlCsvCacheState();
    clearRlCsvCache();
    clearRlRankerCache();  // BE-073: also clear rl-ranker.ts's cache
    const stateAfterA = getRlCsvCacheState();

    await writeAuditLog({
      user: auth.user,
      action: "rl_cache_refresh",
      resource: "rl:cache",
      metadata: {
        clearedEntries: stateBeforeA.length,
        remainingEntries: stateAfterA.length,
        cachesCleared: ["rl-csv-cache", "rl-ranker"],  // BE-073
      },
    });

    return NextResponse.json({
      ok: true,
      clearedEntries: stateBeforeA.length,
      cachesCleared: ["rl-csv-cache", "rl-ranker"],  // BE-073
      message:
        stateBeforeA.length === 0
          ? "RL cache was already empty — nothing to refresh."
          : `RL cache cleared (${stateBeforeA.length} entr${stateBeforeA.length === 1 ? "y" : "ies"}) from both cache modules. The next request will re-read the CSV from disk.`,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`RL cache refresh failed: ${msg}`);
  }
}
