import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  getAuthenticatedUser,
  hashPassword,
  verifyPassword,
  validatePasswordPolicy,
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
    if (!audit.ok) {
      // The password WAS changed, but the audit log failed. We MUST
      // revoke all sessions to force re-login — otherwise an attacker
      // who changed the password (e.g. via session hijack) would
      // remain logged in with no audit trail.
      const { revokeAllRefreshTokensForUser } = await import("@/lib/auth/server");
      await revokeAllRefreshTokensForUser(user.userId);
      return internalError("Password changed but audit log failed. All sessions revoked — please log in again.");
    }
    return NextResponse.json({ ok: true, message: "Password updated." });
  } catch (e) {
    console.error("Password update failed:", e);
    return internalError("Failed to update password.");
  }
}
