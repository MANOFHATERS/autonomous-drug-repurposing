import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser, verifyPassword } from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/disable
 * Body: { password?: string, code?: string }
 *
 * ROOT FIX for FE-005 (2FA disable requires no re-auth): previously this
 * endpoint disabled 2FA on the basis of an authenticated session alone. A
 * malicious party with access to an open laptop could turn off the user's
 * 2FA in 1 click and then quietly exfiltrate the account.
 *
 * ROOT FIX: the caller MUST supply either:
 *  - their current account password, OR
 *  - a valid 6-digit TOTP code from their authenticator app.
 *
 * If neither is supplied or both are invalid, the request is rejected with
 * 401. This is the standard re-authentication pattern used by GitHub,
 * Google, and AWS before security-sensitive changes.
 */
export async function POST(req: NextRequest) {
  const csrf = await requireCsrfOrSend();
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  let body: { password?: string; code?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const dbUser = await db.user.findUnique({ where: { id: user.userId } });
  if (!dbUser) {
    return NextResponse.json({ error: "not_found", message: "User not found" }, { status: 404 });
  }
  if (!dbUser.mfaEnabled) {
    // Nothing to disable — idempotent success.
    return NextResponse.json({ ok: true, enabled: false });
  }

  // Re-auth: require either current password OR current TOTP code.
  const password = (body.password || "").trim();
  const code = (body.code || "").trim();
  let reauthOk = false;
  if (password) {
    reauthOk = await verifyPassword(password, dbUser.passwordHash);
  }
  if (!reauthOk && code && dbUser.mfaSecret) {
    reauthOk = verifyTotp(dbUser.mfaSecret, code);
  }
  if (!reauthOk) {
    return NextResponse.json(
      {
        error: "reauth_required",
        message: "Supply your current password or a valid 2FA code to disable 2FA.",
      },
      { status: 401 }
    );
  }

  try {
    await db.user.update({
      where: { id: user.userId },
      data: { mfaSecret: null, mfaEnabled: false },
    });
    // Revoke all existing refresh tokens so any stolen session dies too.
    const { revokeAllRefreshTokensForUser } = await import("@/lib/auth/server");
    await revokeAllRefreshTokensForUser(user.userId);
    await writeAuditLog({
      user,
      action: "2fa_disable",
      resource: user.userId,
    });
    return NextResponse.json({ ok: true, enabled: false });
  } catch (e) {
    console.error("2FA disable failed:", e);
    return internalError("Failed to disable 2FA.");
  }
}
