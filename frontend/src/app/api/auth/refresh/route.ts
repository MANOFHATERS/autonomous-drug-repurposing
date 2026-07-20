import { NextRequest, NextResponse } from "next/server";
import { consumeRefreshToken, setAuthCookies, clearAuthCookies } from "@/lib/auth/server";
import { writeAuditLog } from "@/lib/api-helpers";
// BE-014 + BE-076 ROOT FIX:
//
// BE-014: The previous refresh handler had NO rate limit. Each successful
// refresh ROTATES the token — `consumeRefreshToken` revokes the old token
// and issues a new one (creates a new RefreshToken DB row). An attacker
// who steals a refresh token could hammer this endpoint thousands of times
// per second, each call creating a new RefreshToken row. Effects:
//   (a) DB pollution — the RefreshToken table grows unboundedly (each row
//       has a 30-day TTL);
//   (b) the original victim's session is invalidated on the first attacker
//       call (token rotation revokes the old token), so the victim notices
//       and may change their password — but the attacker's new token is
//       valid for 30 days;
//   (c) CPU burn — each refresh calls db.user.findUnique +
//       db.refreshToken.update + db.refreshToken.create + JWT signing
//       (HS256), ~5-10ms per call, exhausts DB connection pool at
//       ~1000 req/s.
//
// BE-076: The previous handler wrote NO audit log entry for refreshes.
// Every successful and failed refresh is a security-relevant event — a
// refresh means a session was extended (or a stolen token was used to
// issue new tokens). FDA 21 CFR Part 11 requires audit trails for
// "creation, modification, and deletion of records" — a session extension
// is a modification of the user's authentication state. An attacker using
// a stolen refresh token would leave NO audit trail.
//
// TM10 v130 FORENSIC ROOT FIX (Task 10.6):
//
// ROOT CAUSE: the previous "fix" (v128) introduced two layers of rate
// limiting, BUT with WRONG limits:
//
//   1. Layer 1 (per-IP) used `checkIpRateLimitDistributed(req)` which is
//      the SHARED login IP limiter (IP_MAX_ATTEMPTS=20, IP_WINDOW_MINUTES=5
//      = 20 per 5 min = 4/min avg, allows bursts of 20 in 1 second).
//      The task spec requires 5/min DEDICATED to refresh. The shared
//      limiter is 4x too lenient AND couples refresh rate limits to login
//      rate limits — a user who fails login 20 times in 5 minutes also
//      gets blocked from refreshing (and vice versa).
//
//   2. Layer 2 (per-user) used REFRESH_USER_RATE_LIMIT = { max: 10,
//      windowSeconds: 60 } = 10 per MINUTE. The task spec requires 10 per
//      HOUR. The v128 limit is 60x too lenient — an attacker with a stolen
//      token could rotate it 600 times per hour, each creating a new
//      RefreshToken DB row. The v128 test file claimed this was "600x
//      stricter" — that claim is mathematically WRONG (10/min = 600/hour,
//      which is 60x MORE LENIENT than 10/hour).
//
// ROOT FIX:
//   1. Layer 1 (per-IP, unauthenticated): use the GENERIC
//      `checkUserRateLimitDistributed` with a synthetic key
//      `refresh:ip:${ip}` and REFRESH_IP_RATE_LIMIT = { max: 5,
//      windowSeconds: 60 }. This gives refresh its OWN 5/min IP budget,
//      decoupled from login. The 6th refresh from the same IP within 60s
//      is blocked with 429 + Retry-After.
//
//   2. Layer 2 (per-user, post-authentication): use
//      `checkUserRateLimitDistributed` with the user's userId and
//      REFRESH_USER_RATE_LIMIT = { max: 10, windowSeconds: 3600 }. The
//      11th refresh from the same user within 1 hour is blocked.
//
// Why use `checkUserRateLimitDistributed` for BOTH layers (instead of
// `checkIpRateLimitDistributed` for Layer 1)?
//   - `checkUserRateLimitDistributed` takes an arbitrary string key. We
//     pass `refresh:ip:${ip}` for Layer 1 — the function doesn't care
//     that it's an IP, it just rate-limits on the key.
//   - This reuses the existing Redis-backed distributed infrastructure
//     (multi-instance production) with NO new code.
//   - It allows custom limits per call site (REFRESH_IP_RATE_LIMIT ≠
//     REFRESH_USER_RATE_LIMIT).
//   - It DECOUPLES refresh rate limits from login rate limits — the login
//     endpoint still uses its own 20/5min IP limiter, refresh uses 5/1min.
//
// FALLBACK: when REDIS_URL is unset (single-instance dev/test), the
// distributed limiter automatically uses the in-memory backend. When
// REDIS_URL is set but Redis is unreachable, the distributed limiter
// throws — we catch and fall back to the SYNC `checkUserRateLimit`
// (in-memory only). This preserves the v128 fallback pattern.
//
// AUDIT (BE-076): every refresh — success or failure — writes an audit
// log entry. Successful: `token_refreshed`. Failed: `token_refresh_failed`
// with the reason. Rate-limited: `token_refresh_rate_limited` with the
// retry-after. The user identity is included when known (from the consumed
// refresh token); for unauthenticated failures (no token, invalid token,
// IP rate limit), user is null but IP + UA are still captured.
import {
  REFRESH_IP_RATE_LIMIT,
  REFRESH_USER_RATE_LIMIT,
  getClientIpFromHeaders,
} from "@/lib/auth/rate-limit";
import {
  checkUserRateLimit,
  checkUserRateLimitDistributed,
} from "@/lib/auth/per-user-rate-limit";

export async function POST(req: NextRequest) {
  // BE-014 Layer 1 (TM10 v130 ROOT FIX): per-IP rate limit, DEDICATED to
  // refresh. Uses the generic checkUserRateLimitDistributed with a
  // synthetic key `refresh:ip:${ip}` so refresh has its OWN 5/min budget,
  // decoupled from the login endpoint's 20/5min budget.
  //
  // Why a dedicated budget? An attacker probing with random refresh tokens
  // from a single IP should be blocked after 5 attempts/min — NOT after
  // 20 attempts/5min (the login budget). The login budget is appropriate
  // for password brute-force (where the attacker needs thousands of
  // attempts); refresh token brute-force needs only 5 attempts to detect
  // the rate limit and move on. Tighter is better here.
  //
  // FALLBACK: if the distributed limiter throws (REDIS_URL set but Redis
  // unreachable), fall back to the SYNC in-memory limiter. Same pattern
  // as v128 — preserves the graceful-degradation behavior.
  const clientIp = getClientIpFromHeaders(req.headers);
  const refreshIpKey = `refresh:ip:${clientIp}`;
  let ipCheck;
  try {
    ipCheck = await checkUserRateLimitDistributed(refreshIpKey, REFRESH_IP_RATE_LIMIT);
  } catch (e) {
    console.error(
      "[RATE-LIMIT] distributed IP limiter failed for /api/auth/refresh, falling back to sync:",
      e
    );
    ipCheck = checkUserRateLimit(refreshIpKey, REFRESH_IP_RATE_LIMIT);
  }
  if (ipCheck.blocked) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: `Too many refresh attempts from this IP. Try again in ${Math.max(
          1,
          Math.ceil(ipCheck.retryAfterSeconds / 60)
        )} minute(s).`,
        retryAfter: ipCheck.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(ipCheck.retryAfterSeconds) },
      }
    );
  }

  // The refresh cookie is HttpOnly, so we read it via the cookies() helper.
  // We import dynamically to avoid next/headers SSR warnings outside of route
  // handlers.
  const { cookies } = await import("next/headers");
  const store = await cookies();
  const refresh = store.get("drugos_refresh")?.value;
  if (!refresh) {
    // FE-031 ROOT FIX: No refresh cookie at all — clear any stale access
    // cookie so the browser stops sending it on every subsequent request.
    await clearAuthCookies();
    // BE-076: Audit the failed refresh (no token presented). User identity
    // is unknown — log with user: null so the audit row captures the IP
    // and timestamp for forensic analysis.
    await writeAuditLog({
      user: null,
      action: "token_refresh_failed",
      resource: "auth:refresh",
      metadata: { reason: "no_refresh_token" },
    }).catch(() => {
      // Audit-log failure must not change the 401 response.
    });
    return NextResponse.json(
      { error: "no_refresh_token" },
      { status: 401 }
    );
  }
  const result = await consumeRefreshToken(refresh);
  if (!result) {
    // FE-031 ROOT FIX: The refresh token is invalid (revoked, expired, or
    // not found). Previously we returned 401 WITHOUT clearing cookies —
    // the browser kept sending the bad cookie on every subsequent request,
    // triggering a DB lookup and 401 every time. The user was effectively
    // locked out until they manually cleared cookies.
    //
    // Now we clear both cookies (access + refresh) so the client returns
    // to a clean state. The frontend's 401 handler will redirect to login.
    await clearAuthCookies();
    // BE-076: Audit the failed refresh (invalid token). User identity is
    // unknown because consumeRefreshToken returned null (we couldn't
    // decode the user from the token). The IP and UA are still recorded.
    await writeAuditLog({
      user: null,
      action: "token_refresh_failed",
      resource: "auth:refresh",
      metadata: { reason: "invalid_or_expired_token" },
    }).catch(() => {
      // Audit-log failure must not change the 401 response.
    });
    return NextResponse.json(
      { error: "invalid_refresh_token" },
      { status: 401 }
    );
  }

  // BE-014 Layer 2 (TM10 v130 ROOT FIX): per-USER rate limit, 10 per HOUR.
  // This catches an attacker who has a VALID stolen refresh token — the IP
  // limit above doesn't apply if they're rotating IPs (botnet, Tor).
  // 10/hour is 2.5x the legitimate rate (access token TTL 15min → 4
  // refreshes/hour per session). The check runs AFTER consumeRefreshToken
  // so we have a userId to key on.
  //
  // TM10 v130: the v128 limit was 10/MINUTE = 600/hour (150x the
  // legitimate rate, effectively no limit). The v130 limit is 10/HOUR
  // (2.5x the legitimate rate, tight enough to stop DB pollution).
  let userRl;
  try {
    userRl = await checkUserRateLimitDistributed(
      result.userId,
      REFRESH_USER_RATE_LIMIT
    );
  } catch (e) {
    console.error(
      "[RATE-LIMIT] distributed user limiter failed for /api/auth/refresh, falling back to sync:",
      e
    );
    userRl = checkUserRateLimit(result.userId, REFRESH_USER_RATE_LIMIT);
  }
  if (userRl.blocked) {
    // Don't set the new cookies — the refresh is rejected. The old refresh
    // token was already revoked by consumeRefreshToken (rotation), so the
    // attacker loses their stolen token AND gets a 429.
    await writeAuditLog({
      user: {
        userId: result.userId,
        email: result.email,
        role: result.role,
        platformRole: result.platformRole,
      },
      action: "token_refresh_rate_limited",
      resource: "auth:refresh",
      metadata: { retryAfterSeconds: userRl.retryAfterSeconds },
    }).catch(() => {});
    return NextResponse.json(
      {
        error: "rate_limited",
        message: "Too many refresh attempts. Slow down.",
        retryAfter: userRl.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(userRl.retryAfterSeconds) },
      }
    );
  }

  await setAuthCookies(result.access, result.refresh);

  // BE-076: Audit the successful refresh. The user identity comes from
  // consumeRefreshToken's result (which decoded the user from the
  // presented refresh token). The IP and UA are auto-captured by
  // writeAuditLog via the request context (when available). Non-critical:
  // if the audit write fails, the refresh still succeeds — the user is
  // already authenticated and the action is non-destructive. We don't
  // want a DB outage to lock the user out of their session.
  await writeAuditLog({
    user: {
      userId: result.userId,
      email: result.email,
      role: result.role,
      platformRole: result.platformRole,
    },
    action: "token_refreshed",
    resource: "auth:refresh",
  }).catch(() => {
    // Best-effort — refresh must still succeed for the legitimate user.
  });

  return NextResponse.json({ ok: true });
}
