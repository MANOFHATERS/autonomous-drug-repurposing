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
    // FE-005 ROOT FIX (Team Member 13): Standardize on {items: [...]}.
    //
    // Previously this route returned `{ query, results }` while the
    // api-client.ts's searchDiseases() expected `{ items: DiseaseResult[] }`
    // and accessed response.items.map(...). Since `items` was undefined,
    // the .map() call threw "Cannot read properties of undefined
    // (reading 'map')" and the disease search UI crashed on every search.
    //
    // ROOT FIX: return `{ items: results }`. We also keep `query` and
    // `total` for clients that want them, but `items` is the canonical
    // field that the api-client and every list endpoint agrees on.
    return NextResponse.json({ items: results, total: results.length, query: q });
  } catch (e: unknown) {
    // FE-063: never use `e: any` — narrow with instanceof.
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`MeSH search failed: ${msg}`);
  }
}
