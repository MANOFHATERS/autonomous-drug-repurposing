import { NextRequest, NextResponse } from "next/server";
import { getDrugSafetySummary } from "@/lib/services/openfda";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to scrape openFDA adverse-event
// reports at scale for competitive intelligence, and to deplete our openFDA
// API quota. Now it requires auth + a per-user rate limit.
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ drug: string }> }
) {
  const guard = await requireAuthAndRateLimit(req);
  if (guard.response !== null) return guard.response;

  const { drug } = await params;
  if (!drug || drug.length < 2) {
    return badRequest("Drug name parameter (min 2 chars) is required");
  }
  try {
    const summary = await getDrugSafetySummary(decodeURIComponent(drug));
    recordApiRequestForUser(guard.user);
    if (!summary) {
      return NextResponse.json({ error: "not_found", message: "No data returned for this drug" }, { status: 404 });
    }
    return NextResponse.json(summary);
  } catch (e: any) {
    return internalError(`openFDA lookup failed: ${e.message}`);
  }
}
