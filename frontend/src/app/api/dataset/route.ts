import { NextResponse } from "next/server";
import { getDatasetStats } from "@/lib/services/dataset-stats";
import { requireAuth, internalError } from "@/lib/api-helpers";

/**
 * GET /api/dataset
 *
 * ROOT FIX for FE-003: /api/dataset no longer returns 501. It now returns
 * real Phase 1 dataset pipeline statistics — per-source loaded status,
 * row counts, SHA-256 checksums, edge types present.
 *
 * Resolution order:
 *   1. If DATASET_SERVICE_URL is set, proxy to the standalone Airflow
 *      service (production path).
 *   2. Otherwise, read the local Phase 1 checkpoint JSON at
 *      `../phase2/data/checkpoints/step_01.json` (dev / single-box path).
 *   3. If neither yields data, return `source: "none"` with an empty list.
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate dataset statistics.
 */
export async function GET() {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const stats = await getDatasetStats();
    return NextResponse.json(stats);
  } catch (e: any) {
    return internalError(`Dataset stats lookup failed: ${e.message}`);
  }
}
