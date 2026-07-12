import { NextRequest, NextResponse } from "next/server";
import { searchPatents } from "@/lib/services/patentsview";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

// FE-006 ROOT FIX: This route previously had NO authentication. Anyone on
// the internet could use it as an open proxy to deplete our
// PATENTSVIEW_API_KEY quota. Now it requires auth + a per-user rate limit.
export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit(req);
  if (guard.response !== null) return guard.response;

  const q = req.nextUrl.searchParams.get("q") || "";
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "20", 10);
  if (!q || q.trim().length < 2) {
    return badRequest("Query parameter 'q' (min 2 chars) is required");
  }
  try {
    const result = await searchPatents({ query: q, limit });
    recordApiRequestForUser(guard.user);
    return NextResponse.json(result);
  } catch (e: any) {
    return internalError(`Patent search failed: ${e.message}`);
  }
}
