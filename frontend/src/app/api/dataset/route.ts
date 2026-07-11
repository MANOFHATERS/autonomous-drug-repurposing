import { NextRequest, NextResponse } from "next/server";
import { checkDatasetAvailability } from "@/lib/services/ml-stubs";
import { requireAuth, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * GET /api/dataset?source=<chembl|drugbank|uniprot|string|disgenet|omim|pubchem>
 *
 * FE-003 ROOT FIX: The previous code returned 501 even when
 * DATASET_SERVICE_URL was set. The Phase 1 Airflow ETL pipeline was
 * unreachable from the dashboard.
 *
 * ROOT FIX: This endpoint now proxies to the standalone dataset service
 * (a FastAPI wrapper around the Airflow ETL pipeline) when
 * DATASET_SERVICE_URL is set. The dashboard can query real dataset
 * statistics (row counts, last-updated timestamps, quality metrics)
 * from each of the 7 Phase 1 sources.
 *
 * We NEVER fabricate dataset statistics. If the dataset service is not
 * deployed, we return 503 service_not_deployed.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  const availability = checkDatasetAvailability();
  if (!availability.available) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: availability.service,
        description: availability.description,
        reason: availability.reason,
        documentation:
          "See Phase 1 of the build plan (Data Ingestion & Pipeline Setup). " +
          "Set DATASET_SERVICE_URL to enable the proxy.",
      },
      { status: 503 }
    );
  }

  const datasetUrl = process.env.DATASET_SERVICE_URL!;
  const source = req.nextUrl.searchParams.get("source") || "all";
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "100", 10);

  try {
    const upstream = await fetch(
      `${datasetUrl.replace(/\/$/, "")}/stats?source=${encodeURIComponent(source)}&limit=${limit}`,
      { headers: { Accept: "application/json" } }
    );
    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        {
          error: "dataset_service_error",
          message: `Dataset service returned ${upstream.status}: ${text.slice(0, 500)}`,
        },
        { status: 502 }
      );
    }
    const data = await upstream.json();
    await writeAuditLog({
      user: auth.user,
      action: "dataset_query",
      resource: `dataset:${source}`,
      metadata: { source },
    });
    return NextResponse.json(data);
  } catch (e: any) {
    return internalError(`Dataset service proxy failed: ${e.message}`);
  }
}
