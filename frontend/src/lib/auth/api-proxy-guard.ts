import { NextResponse } from "next/server";
import { requireAuth } from "@/lib/api-helpers";
import {
  checkUserApiRateLimit,
  recordUserApiRequest,
} from "@/lib/auth/rate-limit";
import type { AuthenticatedUser } from "@/lib/auth/server";

/**
 * FE-006 ROOT FIX: Shared auth + rate-limit guard for the 6 public-API-proxy
 * routes (drugs, diseases, clinical-trials, literature, patents, safety).
 *
 * Previously these routes had NO authentication check — anyone on the
 * internet could use our backend as an open proxy to:
 *   - bypass IP-based rate limits at PubMed / CT.gov / openFDA / etc.
 *   - deplete our NCBI_API_KEY quota (10 req/sec, 1M req/day)
 *   - deplete our PATENTSVIEW_API_KEY quota
 *   - scrape adverse-event reports at scale
 *
 * This guard enforces:
 *   1. requireAuth() — caller must have a valid access token.
 *   2. Per-user rate limit (60 req/min) — no single user can drain the quota.
 *
 * Returns { user, response } — if response is non-null the caller must
 * return it immediately.
 */
export async function requireAuthAndRateLimit(): Promise<
  { user: AuthenticatedUser; response: null }
  | { user: null; response: NextResponse }
> {
  const auth = await requireAuth();
  if (auth.user === null) {
    // requireAuth returns Response (not NextResponse) on 401 — wrap it
    // into a NextResponse so the return type is consistent.
    return {
      user: null,
      response: new NextResponse(auth.response.body, {
        status: auth.response.status,
        statusText: auth.response.statusText,
        headers: auth.response.headers,
      }),
    };
  }

  const rl = checkUserApiRateLimit(auth.user.userId);
  if (rl.blocked) {
    return {
      user: null,
      response: NextResponse.json(
        {
          error: "rate_limited",
          message: `Too many requests. Try again in ${rl.retryAfterSeconds} second(s).`,
          retryAfterSeconds: rl.retryAfterSeconds,
        },
        {
          status: 429,
          headers: {
            "Retry-After": String(rl.retryAfterSeconds),
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0",
          },
        }
      ),
    };
  }

  return { user: auth.user, response: null };
}

/**
 * Record a successful upstream API request for the user. Call this AFTER
 * the upstream call has succeeded (so failed upstream calls don't count
 * against the quota — they already failed).
 */
export function recordApiRequestForUser(user: AuthenticatedUser): void {
  recordUserApiRequest(user.userId);
}
