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
    if (!audit.ok) {
      return internalError("Password was updated but the audit log failed. Please log in again with your new password.");
    }

    // BE-016 ROOT FIX: ALWAYS revoke all refresh tokens after a password
    // change. If revocation FAILS, return 500 — do NOT return 200 with
    // a false sense of security. A password change where the attacker's
    // stolen refresh tokens keep working for 30 days is NOT a successful
    // password change — it's a security incident.
    try {
      await revokeAllRefreshTokensForUser(user.userId);
    } catch (revErr) {
      const revMsg = revErr instanceof Error ? revErr.message : String(revErr);
      console.error("[password] BE-016: refresh-token revocation failed:", revMsg);
      return NextResponse.json(
        {
          error: "session_revocation_failed",
          message: "Password was updated but session revocation failed — other sessions may still be active. Please contact support immediately.",
        },
        { status: 500 }
      );
    }

    // All revocation succeeded — clear cookies and return success.
    await clearAuthCookies();
    return NextResponse.json({
      ok: true,
      message: "Password updated. All other sessions have been signed out — please log in again.",
    });
  } catch (e) {
    console.error("Password update failed:", e);
    return internalError("Failed to update password.");
  }
}
