import { NextRequest, NextResponse } from "next/server";
import { searchClinicalTrials } from "@/lib/services/clinical-trials";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to deplete our ClinicalTrials.gov
// API quota and to scrape trial data at scale. Now it requires auth + a
// per-user rate limit.
export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit();
  if (guard.response !== null) return guard.response;

  const condition = req.nextUrl.searchParams.get("condition") || "";
  const intervention = req.nextUrl.searchParams.get("intervention") || "";
  const status = (req.nextUrl.searchParams.get("status") || "ALL") as any;
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "20", 10);
  // FE-015: CT.gov v2 is cursor-only. The client must pass back the
  // opaque `pageToken` returned by the previous response — NOT a numeric
  // offset. We accept `pageToken` as a query param.
  const pageToken = req.nextUrl.searchParams.get("pageToken") || undefined;

  if (!condition && !intervention) {
    return badRequest("At least one of 'condition' or 'intervention' is required");
  }
  try {
    const result = await searchClinicalTrials({
      condition: condition || undefined,
      intervention: intervention || undefined,
      status,
      limit,
      pageToken,
    });
    recordApiRequestForUser(guard.user);
    return NextResponse.json(result);
  } catch (e: any) {
    return internalError(`ClinicalTrials.gov search failed: ${e.message}`);
  }
}
