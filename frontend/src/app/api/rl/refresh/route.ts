import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { requireCsrfOrSend } from "@/lib/api-helpers";
// BE-027 ROOT FIX (Team Member 12): the previous version imported
// `clearRlCsvCache` + `getRlCsvCacheState` from rl-csv-cache.ts — a
// SEPARATE cache module that NO production route actually read from.
// /api/rl/route.ts uses getRankedHypotheses() from rl-ranker.ts, which
// has its OWN cache (csvCache in readLocalCsv). Clearing rl-csv-cache.ts
// left rl-ranker.ts's cache untouched — the operator's "Refresh" click
// was a no-op for the actual /api/rl route that served RL data.
//
// Root fix: delete rl-csv-cache.ts entirely (it was dead code — its
// `readRlCsvCached` was never called by any route). Expose production-safe
// `clearRlRankerCsvCache` + `getRlRankerCsvCacheState` from rl-ranker.ts
// (the single source of truth) and call THOSE here. There is now ONE
// cache, ONE clearer, ONE inspector — no possibility of clearing the
// wrong cache.
import {
  clearRlRankerCsvCache,
  getRlRankerCsvCacheState,
} from "@/lib/services/rl-ranker";

/**
 * POST /api/rl/refresh
 *
 * FE-022 ROOT FIX (Team Member 15) + BE-027 ROOT FIX (Team Member 12):
 *
 * ROOT CAUSE: the RL ranker's CSV cache (in rl-ranker.ts:readLocalCsv)
 * relies on `fs.watch()` to invalidate when the underlying CSV changes.
 * `fs.watch()` is unreliable on NFS, Samba, and some Linux filesystems —
 * the change event may not fire, leaving the cache stale until the 60s
 * TTL expires. For a pharma partner demo, 60 seconds of stale RL data is
 * noticeable.
 *
 * Additionally (BE-027), the previous version cleared a DIFFERENT cache
 * module (rl-csv-cache.ts) that no production route actually read from —
 * so the refresh button was a no-op for the actual /api/rl route that
 * served RL data (which uses rl-ranker.ts's cache via readLocalCsv).
 *
 * ROOT FIX: expose a manual refresh endpoint that clears the rl-ranker.ts
 * cache — the ONE cache the /api/rl route actually uses. The dashboard's
 * "Refresh" button POSTs here; we evict every cached entry so the next
 * `GET /api/rl` re-reads the CSV from disk.
 *
 * Auth: any authenticated user may refresh (the cache is per-process,
 * not per-user — refreshing for one user refreshes for all). We log the
 * action to the audit trail so admins can see who refreshed and when.
 *
 * CSRF: required (this is a state-changing POST).
 */
export async function POST(req: NextRequest) {
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    // BE-027: Clear the SINGLE source-of-truth cache in rl-ranker.ts.
    const stateBefore = getRlRankerCsvCacheState();
    clearRlRankerCsvCache();
    const stateAfter = getRlRankerCsvCacheState();

    await writeAuditLog({
      user: auth.user,
      action: "rl_cache_refresh",
      resource: "rl:cache",
      metadata: {
        clearedEntries: stateBefore.length,
        remainingEntries: stateAfter.length,
        cachesCleared: ["rl-ranker"],
      },
    });

    return NextResponse.json({
      ok: true,
      clearedEntries: stateBefore.length,
      cachesCleared: ["rl-ranker"],
      message:
        stateBefore.length === 0
          ? "RL cache was already empty — nothing to refresh."
          : `RL cache cleared (${stateBefore.length} entr${stateBefore.length === 1 ? "y" : "ies"}) from rl-ranker. The next request will re-read the CSV from disk.`,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`RL cache refresh failed: ${msg}`);
  }
}
