import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser, verifyPassword } from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
<<<<<<< HEAD
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

=======
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/disable
 * Body: { currentPassword: string, totpCode: string }
 *
 * FE-005 ROOT FIX: The previous implementation disabled 2FA with NO
 * re-authentication — it just trusted the authenticated session. The
 * code comment even admitted it: "for this development build we trust
 * the authenticated session."
 *
 * If an attacker steals a session cookie (XSS, network sniffing on HTTP,
 * dev-tools on a shared computer), they can disable 2FA in one POST and
 * then the account is password-only — compounding the FE-004 2FA bypass.
 *
 * Root fix: require BOTH the current password AND a valid current TOTP
 * code before clearing mfaSecret/mfaEnabled. This means:
 *   - A stolen session cookie alone is NOT enough to disable 2FA.
 *   - A stolen password alone is NOT enough (attacker still needs TOTP).
 *   - Only someone with BOTH factors can disable 2FA.
 *
 * Edge case: if the user has lost their authenticator device, they must
 * go through an admin-mediated recovery flow (out of scope here — that's
 * a separate /api/auth/2fa/recover endpoint with its own audit trail).
 */
export async function POST(req: NextRequest) {
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", message: "Authentication required" },
      { status: 401 }
    );
  }

<<<<<<< HEAD
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

=======
  let body: { currentPassword?: string; totpCode?: string };
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const currentPassword = body.currentPassword || "";
  const totpCode = (body.totpCode || "").trim();
  if (!currentPassword) return badRequest("currentPassword is required to disable 2FA");
  if (!/^\d{6}$/.test(totpCode)) return badRequest("A 6-digit TOTP code is required");

  try {
    const dbUser = await db.user.findUnique({
      where: { id: user.userId },
      select: { id: true, email: true, passwordHash: true, mfaEnabled: true, mfaSecret: true },
    });
    if (!dbUser) {
      return NextResponse.json(
        { error: "not_found", message: "User not found" },
        { status: 404 }
      );
    }
    if (!dbUser.mfaEnabled || !dbUser.mfaSecret) {
      return NextResponse.json(
        { error: "mfa_not_enabled", message: "2FA is not enabled on this account." },
        { status: 400 }
      );
    }

    // Verify current password.
    const passwordOk = await verifyPassword(currentPassword, dbUser.passwordHash);
    if (!passwordOk) {
      return NextResponse.json(
        { error: "invalid_password", message: "Current password is incorrect." },
        { status: 403 }
      );
    }

    // Verify current TOTP code.
    if (!verifyTotp(dbUser.mfaSecret, totpCode)) {
      return NextResponse.json(
        { error: "invalid_code", message: "Invalid 6-digit code. Try again." },
        { status: 403 }
      );
    }

    // Both factors verified — safe to disable.
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
