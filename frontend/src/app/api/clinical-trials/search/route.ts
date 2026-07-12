import { NextRequest, NextResponse } from "next/server";
import { searchClinicalTrials } from "@/lib/services/clinical-trials";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";
// FE-011 ROOT FIX (Team Member 13): shared pagination helper.
import { parsePagination } from "@/lib/pagination";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to deplete our ClinicalTrials.gov
// API quota and to scrape trial data at scale. Now it requires auth + a
// per-user rate limit.
//
// FE-006 ROOT FIX (response shape): Previously this route returned the raw
// service response `{ total, trials, nextPageToken? }`. The api-client.ts's
// searchClinicalTrials() expected `{ items: ClinicalTrial[] }` and accessed
// response.items.map(...). Since `items` was undefined, the .map() call
// threw "Cannot read properties of undefined (reading 'map')" and the
// clinical trials search UI crashed on every search.
//
// ROOT FIX: Standardize on `{ items: [...], total: number, ... }`. We map
// the service's `trials` field to `items` and pass through `total` and
// `nextPageToken` for paginated follow-up requests.
//
// FE-011 ROOT FIX (pagination): Previously this route accepted a `limit`
// query param but NO `page` / `pageSize` / `offset` — for a common disease
// (e.g., 'breast cancer' has 10,000+ trials), the response could be 10MB+
// and crash the frontend's table component. ROOT FIX: add `page` and
// `pageSize` query params (default page=1, pageSize=50, capped at 100).
// Return `{ items, total, page, pageSize, nextPageToken }` so the frontend
// can render a paginated table and request the next page via the cursor.
export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit();
  if (guard.response !== null) return guard.response;

  const condition = req.nextUrl.searchParams.get("condition") || "";
  const intervention = req.nextUrl.searchParams.get("intervention") || "";
  const status = (req.nextUrl.searchParams.get("status") || "ALL") as any;
  // FE-015: CT.gov v2 is cursor-only. The client must pass back the
  // opaque `pageToken` returned by the previous response — NOT a numeric
  // offset. We accept `pageToken` as a query param.
  const pageToken = req.nextUrl.searchParams.get("pageToken") || undefined;

  if (!condition && !intervention) {
    return badRequest("At least one of 'condition' or 'intervention' is required");
  }

  // FE-011: parse `page` and `pageSize` from the query string. The helper
  // clamps `pageSize` to [1, 100] and defaults to 50. `page` is 1-indexed
  // (matches the standard REST convention and the frontend's table
  // component). We translate it to a `limit` for the underlying service.
  // Note: CT.gov v2 is cursor-only, so `page` is best-effort — the
  // canonical pagination mechanism is `nextPageToken`. We expose both:
  //   - `page` / `pageSize` for the UI's table component (which uses
  //     1-indexed page numbers).
  //   - `nextPageToken` for follow-up requests that need the exact next
  //     page (the UI passes it back as `pageToken`).
  const pageQuery = parsePagination(req.nextUrl.searchParams);
  // Override the limit caps from parsePagination with our own: CT.gov
  // allows up to 1000 rows per page, but we cap at 100 to prevent abuse.
  const pageSize = Math.min(
    Math.max(pageQuery.limit, 1),
    100
  );
  // pageQuery.offset is 0-indexed; we expose 1-indexed page in the response.
  const page = Math.floor(pageQuery.offset / pageSize) + 1;

  try {
    const result = await searchClinicalTrials({
      condition: condition || undefined,
      intervention: intervention || undefined,
      status,
      limit: pageSize,
      pageToken,
    });
    recordApiRequestForUser(guard.user);
    // FE-006: map `trials` → `items`. Keep `total` and `nextPageToken`.
    // FE-011: include `page` and `pageSize` so the frontend can render
    // a paginated table component.
    return NextResponse.json({
      items: result.trials,
      total: result.total,
      page,
      pageSize,
      nextPageToken: result.nextPageToken,
    });
  } catch (e: unknown) {
    // FE-063: never use `e: any` — narrow with instanceof.
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`ClinicalTrials.gov search failed: ${msg}`);
  }
}
