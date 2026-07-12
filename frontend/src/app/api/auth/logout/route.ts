import { NextRequest, NextResponse } from "next/server";
import {
  clearAuthCookies,
  getAuthenticatedUser,
  revokeAllRefreshTokensForUser,
  REFRESH_COOKIE,
} from "@/lib/auth/server";
import { writeAuditLog } from "@/lib/api-helpers";
import { cookies } from "next/headers";

/**
 * FE-002 ROOT FIX: Logout MUST revoke refresh tokens server-side.
 *
 * Previously this handler only cleared the browser cookies, leaving the
 * refresh-token row in the DB valid for up to 30 days. Any refresh token
 * captured by XSS, network sniffing, or shared-computer browser cache
 * remained usable via /api/auth/refresh, giving the attacker a persistent
 * back door even after the victim logged out.
 *
 * Fix: after writing the audit log we call revokeAllRefreshTokensForUser()
 * to invalidate every outstanding refresh token for the user. This is the
 * OWASP-recommended pattern — logout is a server-side state change, not
 * just a cookie wipe. We also defensively read the raw refresh-cookie
 * value before clearing so future extensions can revoke that specific
 * token (currently redundant with the user-wide revocation but provides
 * defense-in-depth if the user object ever fails to resolve).
 */
export async function POST(_req: NextRequest) {
  const user = await getAuthenticatedUser();

  // Read the raw refresh-cookie value BEFORE clearing cookies. The cookie
  // store is mutated by clearAuthCookies() below, so we must snapshot now.
  const store = await cookies();
  const refreshCookieValue = store.get(REFRESH_COOKIE)?.value;

  if (user) {
    await writeAuditLog({
      user,
      action: "logout",
      resource: `user:${user.userId}`,
    });
    // Revoke every outstanding refresh token for this user — not just the
    // current one. A compromised sibling session (different browser,
    // stolen token) must also be terminated.
    try {
      await revokeAllRefreshTokensForUser(user.userId);
    } catch (err) {
      // Log but do not fail the logout response. If revocation fails we
      // still want the user's cookies cleared so they appear logged out
      // locally; the lingering token will eventually expire.
       
      console.error("[logout] refresh-token revocation failed", err);
    }
  } else if (refreshCookieValue) {
    // Even if we cannot resolve a user from the access token, the refresh
    // cookie may still be valid. The consumeRefreshToken path will both
    // revoke it and rotate it — but we want revocation only. Calling
    // revokeAllRefreshTokensForUser requires a userId, so we look one up
    // indirectly via the refresh-token row. This branch is defensive: it
    // only triggers when the access token is missing/expired but the
    // refresh cookie is still present.
    // We deliberately do NOT rotate the token here.
    try {
      const { db } = await import("@/lib/db");
      const record = await db.refreshToken.findUnique({
        where: { token: refreshCookieValue },
      });
      if (record && !record.revokedAt) {
        await db.refreshToken.update({
          where: { id: record.id },
          data: { revokedAt: new Date() },
        });
      }
    } catch (err) {
       
      console.error("[logout] orphan refresh-token revocation failed", err);
    }
  }

  await clearAuthCookies();
  return NextResponse.json({ ok: true });
}
