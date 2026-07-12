import { NextRequest, NextResponse } from "next/server";
import { searchDrugsByName, getDrugProperties } from "@/lib/services/rxnorm";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to deplete our RxNorm / NLM
// API quota. Now it requires auth + enforces a per-user rate limit.
export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit();
  if (guard.response !== null) return guard.response;

  const q = req.nextUrl.searchParams.get("q") || "";
  const rxcui = req.nextUrl.searchParams.get("rxcui");
  try {
    if (rxcui) {
      const props = await getDrugProperties(rxcui);
      recordApiRequestForUser(guard.user);
      return NextResponse.json(props);
    }
    if (!q || q.trim().length < 2) {
      return badRequest("Query parameter 'q' (min 2 chars) or 'rxcui' is required");
    }
    const limit = parseInt(req.nextUrl.searchParams.get("limit") || "10", 10);
    const results = await searchDrugsByName(q, limit);
    recordApiRequestForUser(guard.user);
    return NextResponse.json({ query: q, results });
  } catch (e: any) {
    return internalError(`RxNorm search failed: ${e.message}`);
  }
}
