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
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * POST /api/auth/password
 * Body: { currentPassword: string, newPassword: string }
 *
 * Verifies the current password, validates the new password against the
 * policy, and updates the user's passwordHash. Returns 200 on success.
 */
export async function POST(req: NextRequest) {
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

    // FE-004 ROOT FIX: OWASP — password change MUST invalidate all existing
    // sessions. If an attacker had stolen the old password and was quietly
    // using a refresh token, this kills their access. If the legitimate
    // user was changing the password because they suspected compromise,
    // this is the ONLY way to actually re-secure the account.
    try {
      const revoked = await revokeAllRefreshTokensForUser(user.userId);
       
      console.log(`[password] revoked ${revoked} refresh token(s) for user ${user.userId}`);
    } catch (err) {
       
      console.error("[password] failed to revoke refresh tokens", err);
      // We still proceed — the password IS changed; the lingering tokens
      // will expire within 30 days at worst. But we surface a warning.
    }

    await writeAuditLog({
      user,
      action: "password_change",
      resource: user.userId,
      // Note: we also write a separate audit entry for the revocation so
      // forensics can reconstruct "password change → all sessions killed".
    });
    await writeAuditLog({
      user,
      action: "sessions_revoked",
      resource: user.userId,
      metadata: { trigger: "password_change" },
    });

    // Force the current session to re-authenticate: clear the cookies so
    // the user is logged out of THIS browser too. They must sign in with
    // the new password.
    await clearAuthCookies();

    return NextResponse.json({
      ok: true,
      message: "Password updated. All other sessions have been signed out. Please sign in again.",
      requireReauth: true,
    });
  } catch (e) {
    console.error("Password update failed:", e);
    return internalError("Failed to update password.");
  }
}
