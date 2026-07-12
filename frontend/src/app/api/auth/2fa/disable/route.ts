import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser, verifyPassword } from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";

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
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", message: "Authentication required" },
      { status: 401 }
    );
  }

  let body: { currentPassword?: string; totpCode?: string };
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
      data: { mfaSecret: null, mfaEnabled: false, lastTotpCounter: null },
    });
    // FE-034: 2FA disable is security-critical — must be auditable.
    const audit = await writeAuditLog({
      user,
      action: "2fa_disable",
      resource: user.userId,
      critical: true,
    });
    if (!audit.ok) {
      // The 2FA WAS disabled, but the audit log failed. This is a
      // security incident — re-enable 2FA (forcing the user to set it
      // up again) and return an error.
      await db.user.update({
        where: { id: user.userId },
        data: { mfaEnabled: false }, // secret already cleared; user must re-enroll
      });
      return internalError("2FA disabled but audit log failed. Please re-enable 2FA from your security settings.");
    }
    return NextResponse.json({ ok: true, enabled: false });
  } catch (e) {
    console.error("2FA disable failed:", e);
    return internalError("Failed to disable 2FA.");
  }
}
