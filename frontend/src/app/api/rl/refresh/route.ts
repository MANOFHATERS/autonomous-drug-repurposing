import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { requireCsrfOrSend } from "@/lib/api-helpers";
import {
  clearRlCsvCache,
  getRlCsvCacheState,
} from "@/lib/services/rl-csv-cache";

/**
 * POST /api/rl/refresh
 *
 * FE-022 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE: `rl-csv-cache.ts` relies on `fs.watch()` to invalidate
 * the cache when the underlying RL output CSV changes. `fs.watch()` is
 * unreliable on NFS, Samba, and some Linux filesystems — the change
 * event may not fire, leaving the cache stale until the 60s TTL
 * expires. For a pharma partner demo, 60 seconds of stale RL data is
 * noticeable.
 *
 * ROOT FIX: expose a manual refresh endpoint. The dashboard renders a
 * "Refresh" button; clicking it POSTs to this route, which calls
 * `clearRlCsvCache()` to evict all cached entries. The next
 * `GET /api/rl` re-reads the CSV from disk.
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
    const stateBefore = getRlCsvCacheState();
    clearRlCsvCache();
    const stateAfter = getRlCsvCacheState();

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
