import { NextRequest, NextResponse } from "next/server";
import { searchDiseasesByName } from "@/lib/services/mesh";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to deplete our MeSH / NLM
// API quota. Now it requires auth + enforces a per-user rate limit.
export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit();
  if (guard.response !== null) return guard.response;

  const q = req.nextUrl.searchParams.get("q") || "";
  if (!q || q.trim().length < 2) {
    return badRequest("Query parameter 'q' (min 2 chars) is required");
  }
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "10", 10);
  try {
    const results = await searchDiseasesByName(q, limit);
    recordApiRequestForUser(guard.user);
    return NextResponse.json({ query: q, results });
  } catch (e: any) {
    return internalError(`MeSH search failed: ${e.message}`);
  }
}
