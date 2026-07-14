import { NextRequest, NextResponse } from "next/server";
import {
  clearAuthCookies,
  getAuthenticatedUser,
  revokeAllRefreshTokensForUser,
  REFRESH_COOKIE,
} from "@/lib/auth/server";
import { writeAuditLog, requireCsrfOrSend, clearCsrfCookie } from "@/lib/api-helpers";
import { cookies } from "next/headers";

/**
 * POST /api/auth/logout
 *
 * FE-002 ROOT FIX: Logout MUST revoke refresh tokens server-side.
 *
 * BE-015 ROOT FIX: The previous code swallowed revokeAllRefreshTokensForUser
 * errors in a catch block — logging to stderr but still returning 200.
 * This gave users a FALSE sense of security: their cookies were cleared
 * locally but the attacker's stolen refresh token kept working for 30 days.
 *
 * Root fix: If revocation fails, return 500 "Logout failed — please contact
 * support." Do NOT clear cookies until revocation succeeds. This ensures
 * the user KNOWS their logout may not have terminated all sessions.
 * If the user has no session (already logged out), we still clear cookies
 * defensively — there's nothing to revoke.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();

  // Read the raw refresh-cookie value BEFORE clearing cookies. The cookie
  // store is mutated by clearAuthCookies() below, so we must snapshot now.
  const store = await cookies();
  const refreshCookieValue = store.get(REFRESH_COOKIE)?.value;

  if (user) {
    // BE-015: Revoke BEFORE clearing cookies. If this fails, we return 500
    // and the user's cookies stay — they know something went wrong.
    try {
      await revokeAllRefreshTokensForUser(user.userId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[logout] refresh-token revocation failed:", msg);
      return NextResponse.json(
        {
          error: "logout_failed",
          message: "Session revocation failed — your logout may not have terminated all sessions. Please contact support.",
        },
        { status: 500 }
      );
    }

    // Revocation succeeded — now log and clear cookies.
    await writeAuditLog({
      user,
      action: "logout",
      resource: `user:${user.userId}`,
    });
  } else if (refreshCookieValue) {
    // Even if we cannot resolve a user from the access token, the refresh
    // cookie may still be valid. Try to revoke it directly via the DB.
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
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[logout] orphan refresh-token revocation failed:", msg);
      // BE-015: Return 500 if we can't even revoke the orphan token.
      return NextResponse.json(
        {
          error: "logout_failed",
          message: "Session revocation failed. Please contact support.",
        },
        { status: 500 }
      );
    }
  }

  // All revocation succeeded — clear cookies and return success.
  await clearAuthCookies();
  await clearCsrfCookie();
  return NextResponse.json({ ok: true });
}
