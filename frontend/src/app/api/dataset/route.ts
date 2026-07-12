import { NextRequest, NextResponse } from "next/server";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";
import { getDatasetStats } from "@/lib/services/dataset-stats";

/**
 * GET /api/dataset?source=<chembl|drugbank|uniprot|string|disgenet|omim|pubchem>
 *
 * RT-007 ROOT FIX (Team Member 17): the previous version returned 503
 * "service_not_deployed" whenever DATASET_SERVICE_URL was unset, even
 * though a local lib service (`getDatasetStats`) was available that
 * reads the Phase 1 / Phase 2 checkpoint JSON from disk. The dashboard's
 * dataset page therefore 503'd in every default deployment.
 *
 * Root fix: ALWAYS call `getDatasetStats()` from the local lib service.
 * The lib service itself proxies to DATASET_SERVICE_URL when that env
 * var is set (production multi-node deploy), and falls back to reading
 * the local checkpoint JSON (single-box dev / CI deploy). Either way
 * the route returns real dataset statistics instead of 503.
 *
 * We NEVER fabricate dataset statistics. If neither the proxy nor the
 * local checkpoint yields data, the lib returns `source: "none"` with
 * an empty sources list, and we surface that to the caller.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const stats = await getDatasetStats();
    const source = req.nextUrl.searchParams.get("source") || "all";

    await writeAuditLog({
      user: auth.user,
      action: "dataset_query",
      resource: `dataset:${source}`,
      metadata: { source, statsSource: stats.source, count: stats.sources.length },
    });

    // If a specific source was requested, filter the sources list.
    if (source !== "all" && stats.sources.length > 0) {
      const filtered = stats.sources.filter(
        (s) => s.name.toLowerCase() === source.toLowerCase()
      );
      return NextResponse.json({ ...stats, sources: filtered });
    }

    return NextResponse.json(stats);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Dataset stats failed: ${msg}`);
  }
}
