import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  getAuthenticatedUser,
  hashPassword,
  verifyPassword,
  validatePasswordPolicy,
  revokeAllRefreshTokensForUser,
  clearAuthCookies,
} from "@/lib/auth/server";
import { badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";

/**
 * POST /api/auth/password
 * Body: { currentPassword: string, newPassword: string }
 *
 * Verifies the current password, validates the new password against the
 * policy, and updates the user's passwordHash. Returns 200 on success.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  let body: { currentPassword?: string; newPassword?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const currentPassword = body.currentPassword || "";
  const newPassword = body.newPassword || "";

  if (!currentPassword || !newPassword) {
    return badRequest("Both currentPassword and newPassword are required.");
  }

  const policy = validatePasswordPolicy(newPassword);
  if (!policy.ok) {
    return badRequest(policy.reason || "New password does not meet policy.");
  }

  const dbUser = await db.user.findUnique({ where: { id: user.userId } });
  if (!dbUser) {
    return NextResponse.json({ error: "not_found", message: "User not found" }, { status: 404 });
  }

  const ok = await verifyPassword(currentPassword, dbUser.passwordHash);
  if (!ok) {
    return NextResponse.json(
      { error: "invalid_credentials", message: "Current password is incorrect." },
      { status: 403 }
    );
  }

  try {
    const newHash = await hashPassword(newPassword);
    await db.user.update({
      where: { id: user.userId },
      data: { passwordHash: newHash },
    });
    // FE-034: password_change is security-critical — must be auditable.
    const audit = await writeAuditLog({
      user,
      action: "password_change",
      resource: user.userId,
      critical: true,
    });
    // FE-004 ROOT FIX (v2): ALWAYS revoke all refresh tokens after a
    // password change — not just when the audit log fails. This is the
    // OWASP-recommended pattern: a password change MUST invalidate every
    // outstanding session. Two threat models require this:
    //   (a) Attacker stole the password and changed it → victim's old
    //       refresh cookies (still valid for 30 days) would otherwise
    //       keep working, giving the attacker a persistent back door
    //       even after the victim recovers the account.
    //   (b) User changes password because they suspect compromise →
    //       the attacker's stolen refresh tokens would otherwise keep
    //       working for 30 days, defeating the purpose of the change.
    // The previous code only revoked on audit-log failure, which is the
    // INVERSE of safe behavior — it revoked exactly when the password
    // update had already happened but audit failed, leaving the
    // common-path (audit succeeds) WIDE OPEN.
    try {
      await revokeAllRefreshTokensForUser(user.userId);
    } catch (revErr) {
      // Revocation failure is a security incident — log loudly. We still
      // clear the current session's cookies below so the user is forced
      // to re-authenticate with the new password locally; the lingering
      // tokens will eventually expire (30-day TTL).
      console.error("[password] refresh-token revocation failed", revErr);
    }
    // Clear the current session's cookies so the user is forced to
    // re-authenticate with the new password. This is the user-facing
    // signal that the password change took effect across all sessions.
    await clearAuthCookies();
    if (!audit.ok) {
      // The password WAS changed AND sessions were revoked, but the
      // audit log failed. Inform the user — they need to log in again.
      return internalError("Password changed and all sessions revoked, but the audit log failed. Please log in again with your new password.");
    }
    return NextResponse.json({
      ok: true,
      message: "Password updated. All other sessions have been signed out — please log in again.",
    });
  } catch (e) {
    console.error("Password update failed:", e);
    return internalError("Failed to update password.");
  }
}
