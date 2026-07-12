import { NextRequest, NextResponse } from "next/server";
import { consumeRefreshToken, setAuthCookies, signAccessToken, clearAuthCookies } from "@/lib/auth/server";

export async function POST(_req: NextRequest) {
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
    return NextResponse.json({ error: "no_refresh_token" }, { status: 401 });
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
    return NextResponse.json({ error: "invalid_refresh_token" }, { status: 401 });
  }
  await setAuthCookies(result.access, result.refresh);
  return NextResponse.json({ ok: true });
}
